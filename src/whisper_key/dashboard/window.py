# dashboard/window.py
# Creates the pywebview window, wires the Python API to it and blocks until it
# closes. Runs in the dashboard child process only (webview.start() insists on
# the process's main thread, which the running app's tray loop already owns).

import json
import logging
import os
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..platform import IS_MACOS, IS_WINDOWS, app as platform_app
from ..utils import get_user_app_data_path, resolve_asset_path

logger = logging.getLogger(__name__)

WINDOW_TITLE = "Whisper Local"
WINDOW_SIZE = (980, 680)
WINDOW_MIN_SIZE = (760, 520)
WEB_ROOT = Path(__file__).parent / 'web'

# Dock / taskbar / title-bar icon. Windows wants an .ico; macOS takes the PNG.
APP_ICON_PATH = resolve_asset_path(
    'platform/windows/assets/whisperkey-icon.ico' if IS_WINDOWS else 'assets/app_icon.png')

# Test/CI hook: when set to a number of seconds, the window closes itself after
# that long so a smoke test (or a screenshot script) never has to kill the
# process. Unset in normal use.
AUTOCLOSE_ENV_VAR = 'WHISPER_LOCAL_DASHBOARD_AUTOCLOSE_SECONDS'

# Test hook: WHISPER_LOCAL_DASHBOARD_THEME=light|dark forces the page theme (and
# native window colour) regardless of the OS setting, so both palettes can be
# checked without switching the system theme. Unset in normal use.
THEME_ENV_VAR = 'WHISPER_LOCAL_DASHBOARD_THEME'

# Window background before the page paints; keep equal to --bg-page in
# web/styles.css (light and dark) or the first frame flashes the wrong colour.
LIGHT_BACKGROUND = '#f4f4f6'
DARK_BACKGROUND = '#1c1c1e'

# Loggers that chatter at INFO on every ConfigManager load (each API call makes
# one); in this short-lived process that only drowns dashboard.log.
NOISY_LOGGERS = ('whisper_key.config_manager', 'whisper_key.transforms')


# ── Logging ──────────────────────────────────────────────────────────────────

# The child process has no logging of its own. Own file (not app.log) so it never
# fights the running app's rotating handler; JS errors land here via the API.
# stderr only, never stdout: stdout is the command pipe back to the app.
def _configure_child_logging():
    for noisy_name in NOISY_LOGGERS:
        logging.getLogger(noisy_name).setLevel(logging.WARNING)
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handlers = [logging.StreamHandler(sys.stderr)]
    try:
        log_path = Path(get_user_app_data_path()) / 'dashboard.log'
        handlers.append(RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=1, encoding='utf-8'))
    except OSError:
        pass  # a read-only profile still gets the stderr handler
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)


# ── Auto-close (test hook) ───────────────────────────────────────────────────

# Seconds until auto-close from the env var, or None when unset/invalid.
def _autoclose_seconds():
    raw = os.environ.get(AUTOCLOSE_ENV_VAR, '').strip()
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return seconds if seconds > 0 else None


# Runs on pywebview's worker thread once the GUI loop is up.
def _close_after(window, seconds: float):
    time.sleep(seconds)
    logger.info(f"Dashboard auto-closing after {seconds:g}s ({AUTOCLOSE_ENV_VAR})")
    window.destroy()


# ── Native window colour ─────────────────────────────────────────────────────

# True when the OS is in dark mode. Unknown (or any error) counts as light.
def _system_prefers_dark() -> bool:
    try:
        if IS_MACOS:
            # The key only exists in dark mode, so the command fails when light.
            result = subprocess.run(['defaults', 'read', '-g', 'AppleInterfaceStyle'],
                                    capture_output=True, text=True, timeout=2)
            return result.stdout.strip().lower() == 'dark'
        if IS_WINDOWS:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize')
            with key:
                return winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0
    except Exception:
        pass
    return False


# 'light' / 'dark' from the theme test hook, or None to follow the OS.
def _forced_theme():
    value = os.environ.get(THEME_ENV_VAR, '').strip().lower()
    return value if value in ('light', 'dark') else None


# Applies the forced theme to the loaded page (styles.css keys off <html data-theme>).
def _apply_forced_theme(window, theme: str):
    window.evaluate_js(f"document.documentElement.dataset.theme = {json.dumps(theme)}")


# ── Messages from the running app ────────────────────────────────────────────

# Brings the window forward after the app asked to show a page, e.g. the tray's
# "Settings…" clicked while the dashboard sits behind other windows. Best effort.
def _bring_to_front(window):
    for method_name in ('restore', 'show'):
        try:
            getattr(window, method_name)()
        except Exception as e:
            logger.debug(f"window.{method_name}() failed: {e}")


# Switches the page via the shell's JS router, then raises the window.
def _navigate_window(window, page: str):
    logger.info(f"The app asked the dashboard to show {page!r}")
    window.evaluate_js(f"window.dashboard.navigate({json.dumps(page)})")
    _bring_to_front(window)


# Records the app's new state for the page's next get_app_info and pushes it to
# the status bar now (if the page has not loaded yet, its boot reads the stored one).
def _show_app_state(window, api, state: str):
    api._app_state = state
    window.evaluate_js(f"window.dashboard && window.dashboard.setAppState({json.dumps(state)})")


# Runs on pywebview's worker thread once the GUI loop is up: starts listening
# to the app (if there is one) and arms the optional auto-close.
def _on_gui_ready(window, api, parent_stream, autoclose_seconds):
    # A tty stdin means a standalone launch: there is no app on the other end.
    if parent_stream is not None and not parent_stream.isatty():
        threading.Thread(target=_listen_until_app_exits,
                         args=(window, api, parent_stream),
                         daemon=True, name='dashboard-app-listener').start()
    if autoclose_seconds:
        _close_after(window, autoclose_seconds)


# Follows the app's navigate messages; when the app quits (stdin hits EOF) the
# dashboard closes too, so it never lingers with dead Restart/Retry buttons.
# Only for app-spawned children: a manual `--window dashboard` launch with a
# piped payload also hits EOF right away and must stay open.
def _listen_until_app_exits(window, api, parent_stream):
    from ..window_launcher import listen_to_app, COMMAND_PIPE_ENV_VAR
    listen_to_app(parent_stream,
                  lambda page: _navigate_window(window, page),
                  lambda state: _show_app_state(window, api, state))
    if os.environ.get(COMMAND_PIPE_ENV_VAR) == '1':
        logger.info("The app closed; closing the dashboard")
        window.destroy()


# ── Entry ────────────────────────────────────────────────────────────────────

# Builds the window and blocks in the GUI loop. Returns the process exit code.
def run_dashboard_window(page: str = 'home', parent_stream=None, app_state=None) -> int:
    _configure_child_logging()
    try:
        import webview
    except ImportError:
        logger.error("pywebview is not installed; cannot open the dashboard (pip install pywebview)")
        return 1

    from .api import DashboardApi

    # Before any window exists, so the Dock/menu bar show our name and logo
    # rather than the Python interpreter's. Cosmetic: never block the window.
    try:
        platform_app.set_process_identity(WINDOW_TITLE, APP_ICON_PATH)
    except Exception as e:
        logger.warning(f"Could not set the dashboard's app name/icon: {e}")

    forced_theme = _forced_theme()
    use_dark = forced_theme == 'dark' if forced_theme else _system_prefers_dark()

    api = DashboardApi(initial_page=page, app_state=app_state)
    window = webview.create_window(
        WINDOW_TITLE,
        url=str(WEB_ROOT / 'index.html'),
        js_api=api,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN_SIZE,
        background_color=DARK_BACKGROUND if use_dark else LIGHT_BACKGROUND,
    )
    if forced_theme:
        window.events.loaded += lambda: _apply_forced_theme(window, forced_theme)
    # icon= is honoured by pywebview's winforms (title bar) and cocoa backends.
    webview.start(_on_gui_ready, (window, api, parent_stream, _autoclose_seconds()), icon=APP_ICON_PATH)
    return 0
