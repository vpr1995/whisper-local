# requirements_check.py
# The "What Whisper Local needs" checklist on the dashboard's About page: one row per
# thing the app depends on, whether it is in place, and what fixes it. Pure Python with
# no UI. The checks reuse the platform permission modules, the model registry and the AI
# provider; nothing here duplicates the --doctor logic.

import logging

from . import ai_provider

logger = logging.getLogger(__name__)


# ── Row vocabulary ───────────────────────────────────────────────────────────

STATUS_OK = 'ok'        # in place
STATUS_WARN = 'warn'    # optional or not settled yet: the app works without it
STATUS_FAIL = 'fail'    # required and missing

OLLAMA_DOWNLOAD_URL = 'https://ollama.com/download'
OLLAMA_PROBE_TIMEOUT_SECONDS = 1.5   # the page waits for this, so keep it short

# Fix actions the page may run through run_fix(). The page is untrusted, so only these
# names do anything. Every other fix is handled by the page itself (kind 'page' or 'url').
FIX_OPEN_MICROPHONE_SETTINGS = 'open_microphone_settings'
FIX_OPEN_ACCESSIBILITY_SETTINGS = 'open_accessibility_settings'
FIX_OPEN_APPLE_INTELLIGENCE_SETTINGS = 'open_apple_intelligence_settings'
FIX_INSTALL_COMMAND_LINE_TOOLS = 'install_command_line_tools'


# One checklist row. `fix` is None when nothing is missing or nothing can be done:
#   {kind: 'system', id, label}  run_fix(id) on the Python side
#   {kind: 'page', target, label} navigate the dashboard to a page
#   {kind: 'url', url, label}     open a web page
def _row(row_id: str, title: str, caption: str, status: str, detail: str = '', fix: dict = None) -> dict:
    return {'id': row_id, 'title': title, 'caption': caption, 'status': status, 'detail': detail, 'fix': fix}


def _system_fix(fix_id: str, label: str) -> dict:
    return {'kind': 'system', 'id': fix_id, 'label': label}


# ── Individual checks ────────────────────────────────────────────────────────

def _microphone_row(permissions) -> dict:
    title, caption = "Microphone access", "Needed to hear you while you dictate."
    state = permissions.check_microphone_permission()
    if state == 'granted':
        return _row('microphone', title, caption, STATUS_OK)
    if state == 'not_determined':
        # macOS lists the app only after it has asked, so there is nothing to open yet.
        return _row('microphone', title, caption, STATUS_WARN, "Not asked yet. macOS asks the first time you record.")
    detail = "Blocked by your administrator" if state == 'restricted' else "Blocked in your privacy settings"
    return _row('microphone', title, caption, STATUS_FAIL, detail,
                _system_fix(FIX_OPEN_MICROPHONE_SETTINGS, "Open Settings"))


def _accessibility_row(permissions) -> dict:
    title, caption = "Accessibility access", "Needed for hotkeys and for typing your text into other apps."
    if permissions.check_accessibility_permission():
        return _row('accessibility', title, caption, STATUS_OK)
    return _row('accessibility', title, caption, STATUS_FAIL,
                "Not allowed. Turn on this app in the Accessibility list.",
                _system_fix(FIX_OPEN_ACCESSIBILITY_SETTINGS, "Open Settings"))


# The speech model the app would load (`auto` resolved the way the Models page does)
# and whether its files are on disk.
def selected_speech_model(whisper_cfg: dict) -> tuple:
    from .engine_factory import resolve_backend
    from .model_registry import ModelRegistry
    registry = ModelRegistry(whisper_cfg.get('models', {}), {},
                             active_backend=whisper_cfg.get('backend', 'faster_whisper'))
    _backend, model_key = resolve_backend(whisper_cfg, registry)
    return model_key, registry.is_model_cached(model_key)


def _speech_model_row(whisper_cfg: dict) -> dict:
    title, caption = "Speech model", "Turns your voice into text. It runs on this computer."
    model_key, is_cached = selected_speech_model(whisper_cfg)
    if is_cached:
        return _row('speech_model', title, caption, STATUS_OK, f"{model_key} is downloaded")
    return _row('speech_model', title, caption, STATUS_FAIL, f"{model_key} is not downloaded yet",
                {'kind': 'page', 'target': 'models', 'label': "Open Models"})


# Apple Intelligence is optional, so every state that is not "ready" is amber. The
# Command Line Tools state is worked out here, because the dashboard process never ran
# the helper build that records it.
def _apple_intelligence_row(apple_intelligence, force_refresh: bool) -> dict:
    title = "Apple Intelligence"
    caption = "Cleans up and rewrites your text on this Mac, without internet. Dictation works without it."
    available, reason = ai_provider.apple_availability(force_refresh)
    if available:
        return _row('apple_intelligence', title, caption, STATUS_OK, "Ready")
    if reason == 'not_built' and not apple_intelligence.command_line_tools_installed():
        reason = 'needs_command_line_tools'
    detail = ai_provider.APPLE_REASON_TEXT.get(reason, ai_provider.APPLE_REASON_TEXT['unknown'])
    fix = None
    if reason == 'needs_command_line_tools':
        fix = _system_fix(FIX_INSTALL_COMMAND_LINE_TOOLS, "Install tools")
    elif reason == 'apple_intelligence_off':
        fix = _system_fix(FIX_OPEN_APPLE_INTELLIGENCE_SETTINGS, "Open Settings")
    return _row('apple_intelligence', title, caption, STATUS_WARN, detail, fix)


def _ollama_row(postprocess_cfg: dict) -> dict:
    title, caption = "Ollama", "Optional. Runs the AI text steps while Apple Intelligence is not in use."
    reachable, _models = ai_provider.probe_ollama(postprocess_cfg, OLLAMA_PROBE_TIMEOUT_SECONDS)
    if reachable:
        return _row('ollama', title, caption, STATUS_OK, "Running")
    endpoint = (postprocess_cfg.get('ollama') or {}).get('endpoint') or ai_provider.DEFAULT_OLLAMA_ENDPOINT
    return _row('ollama', title, caption, STATUS_WARN, f"Not reachable at {endpoint}",
                {'kind': 'url', 'url': OLLAMA_DOWNLOAD_URL, 'label': "Get Ollama"})


# ── Public API ───────────────────────────────────────────────────────────────

# Every row that applies on this computer, in display order. `config` is a ConfigManager.
# A check that crashes becomes a warning row instead of blanking the checklist.
def collect_requirements(config, force_refresh: bool = False) -> list:
    from .platform import IS_MACOS, apple_intelligence, permissions
    whisper_cfg = config.get_whisper_config()
    postprocess_cfg = config.get_postprocess_config()

    checks = [('microphone', "Microphone access", lambda: _microphone_row(permissions))]
    if IS_MACOS:
        checks.append(('accessibility', "Accessibility access", lambda: _accessibility_row(permissions)))
    checks.append(('speech_model', "Speech model", lambda: _speech_model_row(whisper_cfg)))
    if IS_MACOS:
        checks.append(('apple_intelligence', "Apple Intelligence",
                       lambda: _apple_intelligence_row(apple_intelligence, force_refresh)))
    if ai_provider.ollama_in_use(postprocess_cfg):
        checks.append(('ollama', "Ollama", lambda: _ollama_row(postprocess_cfg)))

    rows = []
    for row_id, title, run_check in checks:
        try:
            rows.append(run_check())
        except Exception as error:
            logger.warning(f"Requirement check '{row_id}' failed: {error}")
            rows.append(_row(row_id, title, "", STATUS_WARN, f"Could not check ({error})"))
    return rows


# Runs one named system fix. False for an unknown name or a fix that did not start.
def run_fix(fix_id: str) -> bool:
    from .platform import apple_intelligence, permissions
    fixes = {
        FIX_OPEN_MICROPHONE_SETTINGS: lambda: permissions.open_settings_pane('microphone'),
        FIX_OPEN_ACCESSIBILITY_SETTINGS: lambda: permissions.open_settings_pane('accessibility'),
        FIX_OPEN_APPLE_INTELLIGENCE_SETTINGS: lambda: permissions.open_settings_pane('apple_intelligence'),
        FIX_INSTALL_COMMAND_LINE_TOOLS: apple_intelligence.install_command_line_tools,
    }
    fix = fixes.get(fix_id)
    if fix is None:
        return False
    try:
        return bool(fix())
    except Exception as error:
        logger.warning(f"Fix '{fix_id}' failed: {error}")
        return False
