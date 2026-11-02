# platform/macos/apple_intelligence.py
# Runs Apple's on-device language model through a small Swift helper program that
# is compiled once on this Mac (no Xcode needed, only the Command Line Tools) and
# kept running between requests, so the model stays warm.
# Windows mirror: platform/windows/apple_intelligence.py (always unavailable).
import atexit
import hashlib
import json
import logging
import os
import platform
import queue
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
        shutdown()                          # a helper started from the old binary is replaced on next use
        logger.info(f"Apple Intelligence helper built: {helper}")
    except (OSError, subprocess.SubprocessError) as error:
        _build_failure_reason = 'build_failed'
        logger.warning(f"Could not build the Apple Intelligence helper: {error}")


# ── The serve process ────────────────────────────────────────────────────────
# One helper process stays running and answers requests over stdin/stdout, so the
# model stays warm between dictations. A request that runs past its timeout kills
# the process (the only reliable way to stop a stuck model); the next request
# starts a fresh one. The lock keeps one request in flight: polish, transforms,
# rephrase and prewarm come from different threads, and the model refuses
# concurrent requests anyway.

_server_lock = threading.Lock()
_server_process = None           # the running `helper serve`, or None
_server_replies = None           # queue of reply lines from that process; None marks its exit
_next_request_id = 0


# Copies the helper's reply lines into `replies` until the process exits. Each
# process gets its own queue, so a dying process can never feed the next one.
def _read_replies(process, replies):
    try:
        for line in process.stdout:
            replies.put(line)
    except (OSError, ValueError):
        pass
    replies.put(None)


def _start_server_locked():
    global _server_process, _server_replies
    process = subprocess.Popen([str(_helper_path()), 'serve'], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, bufsize=1)
    replies = queue.Queue()
    threading.Thread(target=_read_replies, args=(process, replies),
                     name='apple-intelligence-replies', daemon=True).start()
    _server_process, _server_replies = process, replies


def _stop_server_locked():
    global _server_process, _server_replies
    process, _server_process, _server_replies = _server_process, None, None
    if process is None:
        return
    try:
        process.kill()
        process.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass


# Stops the helper; the next request starts it again. Called after a rebuild, so
# the new binary is used.
def shutdown():
    with _server_lock:
        _stop_server_locked()


# At app exit the helper is killed without waiting for the lock: a rephrase in
# flight could hold it for 30 s. (If the app crashes instead, the helper sees its
# stdin close and exits by itself.)
def _kill_server_at_exit():
    process = _server_process
    if process is not None:
        try:
            process.kill()
        except OSError:
            pass


atexit.register(_kill_server_at_exit)


# Writes one request line, starting the helper first if it is not running.
# Returns the request's id. Raises OnDeviceAIError when the helper can't be reached.
def _send_locked(request: dict) -> int:
    global _next_request_id
    if _server_process is None or _server_process.poll() is not None:
        _stop_server_locked()
        try:
            _start_server_locked()
        except OSError as error:
            raise OnDeviceAIError('not_built', str(error))
    _next_request_id += 1
    try:
        _server_process.stdin.write(json.dumps({**request, 'id': _next_request_id}) + '\n')
        _server_process.stdin.flush()
    except (OSError, ValueError) as error:
        _stop_server_locked()
        raise OnDeviceAIError('failed', f"helper stopped ({error})")
    return _next_request_id


# ── Generating text ──────────────────────────────────────────────────────────

# Loads the model ahead of a request (the app calls this when a recording starts),
# so the first answer skips the ~1 s cold start. Best effort and never blocks:
# skipped when a request is already running, since the model is warm then anyway.
def prewarm():
    if not _server_lock.acquire(blocking=False):
        return
    try:
        _send_locked({'op': 'prewarm'})
    except OnDeviceAIError as error:
        logger.debug(f"Apple Intelligence prewarm skipped: {error}")
    finally:
        _server_lock.release()


# Sends the prompt to the helper and returns the model's text. Raises
# OnDeviceAIError with a short code on any failure. A call that runs past the
# timeout kills the helper, so a model that hangs cannot stall the caller and a
# late answer can never be read as the reply to the next request.
def generate(prompt: str, timeout_seconds: float) -> str:
    with _server_lock:
        request_id = _send_locked({'op': 'generate', 'prompt': prompt})
        replies = _server_replies
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            try:
                line = replies.get(timeout=max(remaining, 0))
            except queue.Empty:
                _stop_server_locked()
                raise OnDeviceAIError('timeout', f"no answer within {timeout_seconds:g}s")
            if line is None:
                _stop_server_locked()
                raise OnDeviceAIError('failed', "the helper stopped without answering")
            try:
                answer = json.loads(line)
            except ValueError:
                continue                # not a reply line; keep waiting
            if answer.get('id') == request_id:
                break

    if isinstance(answer.get('text'), str):
        return answer['text']
    error_code = str(answer.get('error') or 'failed')
    if error_code == 'unavailable':
        _forget_cached_availability()       # the model went away: look again next time
    raise OnDeviceAIError(error_code, str(answer.get('detail') or answer.get('reason') or ''))
