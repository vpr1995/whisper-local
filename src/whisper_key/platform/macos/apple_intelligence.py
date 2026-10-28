# platform/macos/apple_intelligence.py
# Runs Apple's on-device language model through a small Swift helper program that
# is compiled once on this Mac (no Xcode needed, only the Command Line Tools).
# Windows mirror: platform/windows/apple_intelligence.py (always unavailable).
import hashlib
import json
import logging
import os
import platform
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from ...utils import OnDeviceAIError

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

HELPER_SOURCE = Path(__file__).parent / 'assets' / 'apple_intelligence_helper.swift'
MINIMUM_MACOS_MAJOR_VERSION = 26      # FoundationModels ships with macOS 26
AVAILABILITY_CHECK_TIMEOUT_SECONDS = 10
BUILD_TIMEOUT_SECONDS = 300
# Reasons that can change while the app runs (the user flips a switch, the model
# finishes downloading), so they are asked again after this long.
RECHECKABLE_REASONS = ('apple_intelligence_off', 'model_not_ready', 'unknown')
RECHECK_AFTER_SECONDS = 60


# ── Helper binary location ───────────────────────────────────────────────────

# Lives in the app data folder, next to a record of which source it was built from.
def _helper_directory() -> Path:
    from ...utils import get_user_app_data_path
    return Path(get_user_app_data_path()) / 'apple_intelligence'


def _helper_path() -> Path:
    return _helper_directory() / 'helper'


def _source_hash() -> str:
    return hashlib.sha256(HELPER_SOURCE.read_bytes()).hexdigest()


def _built_record_path() -> Path:
    return _helper_directory() / 'built-from.sha256'


# ── Static checks (no subprocess) ────────────────────────────────────────────

# The reason this Mac can never run the helper, or '' when the hardware and OS are fine.
def _unsupported_system_reason() -> str:
    if platform.machine() != 'arm64':
        return 'needs_apple_silicon'
    try:
        macos_major = int(platform.mac_ver()[0].split('.')[0])
    except (ValueError, IndexError):
        macos_major = 0
    if macos_major < MINIMUM_MACOS_MAJOR_VERSION:
        return 'needs_macos_26'
    return ''


# ── Availability ─────────────────────────────────────────────────────────────

_availability_lock = threading.Lock()
_cached_answer = None            # (available, reason, time of the answer)
_build_failure_reason = ''       # set by prepare() when it could not build the helper


# Asks the helper whether the on-device model can answer right now.
def _ask_helper_for_availability() -> tuple:
    try:
        result = subprocess.run([str(_helper_path()), 'availability'], capture_output=True,
                                text=True, timeout=AVAILABILITY_CHECK_TIMEOUT_SECONDS)
        answer = json.loads(result.stdout)
        if answer.get('available') is True:
            return True, ''
        return False, str(answer.get('reason') or 'unknown')
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        logger.warning(f"Apple Intelligence availability check failed: {error}")
        return False, 'unknown'


# Cheap and safe to call often: never compiles, never opens the developer-tools dialog.
# Returns (available, reason_code); reason_code is '' when available. `force_refresh`
# skips the cached answer (the dashboard's "Check again").
def availability(force_refresh: bool = False) -> tuple:
    global _cached_answer
    unsupported_reason = _unsupported_system_reason()
    if unsupported_reason:
        return False, unsupported_reason
    if not _helper_path().is_file():
        return False, _build_failure_reason or 'not_built'

    with _availability_lock:
        if _cached_answer is not None and not force_refresh:
            available, reason, answered_at = _cached_answer
            still_fresh = time.monotonic() - answered_at < RECHECK_AFTER_SECONDS
            if available or reason not in RECHECKABLE_REASONS or still_fresh:
                return available, reason
        available, reason = _ask_helper_for_availability()
        _cached_answer = (available, reason, time.monotonic())
        return available, reason


# Forgets the cached answer, so the next availability() asks the helper again.
def _forget_cached_availability():
    global _cached_answer
    with _availability_lock:
        _cached_answer = None


# ── Building the helper ──────────────────────────────────────────────────────

def _run(arguments, timeout=60):
    return subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)


# True when Apple's Command Line Tools (the Swift compiler) are installed. Uses
# `xcode-select -p`, which only reads the setting; running xcrun without the tools
# would open macOS's install dialog.
def command_line_tools_installed() -> bool:
    try:
        tools = _run(['/usr/bin/xcode-select', '-p'])
    except (OSError, subprocess.SubprocessError):
        return False
    return tools.returncode == 0 and Path(tools.stdout.strip()).is_dir()


# Opens Apple's own installer dialog for the Command Line Tools. Returns at once: the
# download and install happen in that dialog, not in this app.
def install_command_line_tools() -> bool:
    try:
        subprocess.Popen(['/usr/bin/xcode-select', '--install'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError as error:
        logger.warning(f"Could not start the Command Line Tools installer: {error}")
        return False


# Compiles the Swift helper if it is missing or its source changed. Runs once at app
# start on a background thread. Building happens in a temporary folder and the new
# binary replaces the old one only when the build worked, so a failure never breaks
# a helper that already works. Failures are logged and remembered as a reason code.
def prepare():
    global _build_failure_reason
    if _unsupported_system_reason():
        return
    helper = _helper_path()
    record = _built_record_path()
    try:
        source_hash = _source_hash()
        if helper.is_file() and record.is_file() and record.read_text() == source_hash:
            return

        # Checked first because running xcrun without the tools opens macOS's
        # "install the command line developer tools" dialog.
        if not command_line_tools_installed():
            if _build_failure_reason != 'needs_command_line_tools':
                logger.warning("Apple Intelligence needs Apple's Command Line Tools to set itself up. "
                               "Install them with: xcode-select --install")
            _build_failure_reason = 'needs_command_line_tools'
            return

        helper.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=helper.parent) as build_directory:
            built = Path(build_directory) / 'helper'
            result = _run(['/usr/bin/xcrun', 'swiftc', '-O', '-o', str(built), str(HELPER_SOURCE)],
                          timeout=BUILD_TIMEOUT_SECONDS)
            if result.returncode != 0:
                raise OSError(f"swiftc failed: {result.stderr.strip()[-500:]}")
            os.replace(built, helper)       # atomic: a running helper keeps its old inode
        record.write_text(source_hash)
        _build_failure_reason = ''
        _forget_cached_availability()
        logger.info(f"Apple Intelligence helper built: {helper}")
    except (OSError, subprocess.SubprocessError) as error:
        _build_failure_reason = 'build_failed'
        logger.warning(f"Could not build the Apple Intelligence helper: {error}")


# ── Generating text ──────────────────────────────────────────────────────────

# One-shot: runs the helper with the prompt and returns the model's text. Raises
# OnDeviceAIError with a short code on any failure; a call that runs past the timeout
# is killed (subprocess.run does that) so a model that hangs cannot stall the caller.
def generate(prompt: str, timeout_seconds: float) -> str:
    try:
        result = subprocess.run([str(_helper_path()), 'generate'], input=json.dumps({'prompt': prompt}),
                                capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        raise OnDeviceAIError('timeout', f"no answer within {timeout_seconds:g}s")
    except OSError as error:
        raise OnDeviceAIError('not_built', str(error))

    try:
        answer = json.loads(result.stdout)
    except ValueError:
        raise OnDeviceAIError('failed', f"unreadable helper output (exit {result.returncode})")
    if result.returncode == 0 and isinstance(answer.get('text'), str):
        return answer['text']
    error_code = str(answer.get('error') or 'failed')
    if error_code == 'unavailable':
        _forget_cached_availability()       # the model went away: look again next time
    raise OnDeviceAIError(error_code, str(answer.get('detail') or answer.get('reason') or ''))
