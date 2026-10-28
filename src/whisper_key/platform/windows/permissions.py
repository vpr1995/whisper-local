# platform/windows/permissions.py
# Windows needs no Accessibility-style grant (the stubs report granted); the microphone
# privacy switch is read from the registry, and a settings page can be opened by name.
# macOS mirror: platform/macos/permissions.py (the real implementation).
import logging
import os

logger = logging.getLogger(__name__)

# Settings pages by name. A fixed list: callers name a page, never a URI.
SETTINGS_PANE_URIS = {'microphone': 'ms-settings:privacy-microphone'}
MICROPHONE_CONSENT_KEY = r'Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone'


def check_accessibility_permission() -> bool:
    return True


def request_accessibility_permission():
    pass


def ensure_accessibility(on_granted) -> bool:
    return True


# 'granted' or 'denied': the "Let apps access your microphone" switch in Windows privacy
# settings. Anything unreadable counts as granted so a failed check never nags.
def check_microphone_permission() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, MICROPHONE_CONSENT_KEY) as consent_key:
            consent_value, _type = winreg.QueryValueEx(consent_key, 'Value')
        return 'denied' if str(consent_value).lower() == 'deny' else 'granted'
    except Exception as error:
        logger.debug(f"Microphone permission check unavailable: {error}")
        return 'granted'


# Opens one Windows settings page by name (see SETTINGS_PANE_URIS). False for any other name.
def open_settings_pane(pane: str) -> bool:
    uri = SETTINGS_PANE_URIS.get(pane)
    if uri is None:
        return False
    os.startfile(uri)
    return True
