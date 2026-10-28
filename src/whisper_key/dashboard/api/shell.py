# dashboard/api/shell.py
# API every page shares: app info, opening the changelog, copying text, JS error
# logging and shared config loading. Owned by the shell, not by any page.

import logging
import webbrowser

logger = logging.getLogger(__name__)

# Fallback when pyproject.toml has no Changelog URL (e.g. an installed wheel).
DEFAULT_CHANGELOG_URL = "https://github.com/drajb/whisper-local/blob/master/CHANGELOG.md"


# ── Helpers ──────────────────────────────────────────────────────────────────

# The Changelog link from pyproject.toml when running from a checkout, else the
# known default. Installed wheels don't ship pyproject.toml, hence the fallback.
def _changelog_url() -> str:
    try:
        import tomllib
        from pathlib import Path
        pyproject = Path(__file__).resolve().parents[4] / 'pyproject.toml'
        with open(pyproject, 'rb') as f:
            return tomllib.load(f)['project']['urls']['Changelog']
    except Exception:
        return DEFAULT_CHANGELOG_URL


class ShellApi:
    # `app_state` is the running app's state (idle, sleeping, recording,
    # processing) or None when no app launched this window. window.py keeps it
    # current as the app reports changes. Private: pywebview only exposes methods.
    def __init__(self, initial_page: str = 'home', app_state=None):
        self._initial_page = initial_page
        self._app_state = app_state

    # Fresh ConfigManager (merged defaults + user_settings.yaml) for each call:
    # settings change on disk while the dashboard is open, and the YAML is tiny.
    def _load_config(self):
        from ...config_manager import ConfigManager
        return ConfigManager(quiet=True)

    # ── Boot ─────────────────────────────────────────────────────────────────

    # Which page to show first (from the launcher's payload), the app's current
    # state for the status bar, plus sidebar-footer facts.
    def get_app_info(self) -> dict:
        from ...platform import IS_MACOS
        from ...utils import get_version
        return {
            'version': get_version(),
            'platform': 'macos' if IS_MACOS else 'windows',
            'initial_page': self._initial_page,
            'app_state': self._app_state,
        }

    # ── Actions ──────────────────────────────────────────────────────────────

    # Opens the GitHub changelog in the system browser ("What's new").
    def open_changelog(self) -> bool:
        return self.open_external_url(_changelog_url())

    # Opens an http(s) URL in the system browser. Refuses other schemes so page
    # content can never launch file:// or custom-protocol handlers.
    def open_external_url(self, url: str) -> bool:
        if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
            return False
        return bool(webbrowser.open(url))

    # Puts text on the system clipboard (transcript "Copy" buttons).
    def copy_text(self, text: str) -> bool:
        import pyperclip
        try:
            pyperclip.copy(text or '')
            return True
        except Exception as e:
            logger.warning(f"Clipboard copy failed: {e}")
            return False

    # Asks the running app to do something only it can (restart, retry the last
    # dictation, pause the hotkeys): see window_launcher.APP_COMMANDS. False when
    # the dashboard has no app to ask, e.g. it was started from a terminal.
    def send_app_command(self, command: str) -> bool:
        from ...window_launcher import send_command_to_app
        return send_command_to_app(command)

    # JS errors (window.onerror / unhandled rejections) end up in dashboard.log so a
    # broken page is visible without devtools.
    def log_js_error(self, message: str, source: str = '', line: int = 0, column: int = 0,
                     stack: str = '') -> None:
        logger.error(f"JS error: {message} ({source}:{line}:{column})\n{stack or ''}")
