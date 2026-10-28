# platform/macos/permissions.py
# Checks and requests the Accessibility / Input Monitoring grants that macOS
# requires before global hotkeys or synthetic keystrokes work at all. Without
# these the app appears silently broken, so failures must be explained clearly.
# Windows mirror: platform/windows/permissions.py (always-granted stubs).
import logging
import os
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0

try:
    from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions
    _appservices_available = True
except ImportError:
    _appservices_available = False
    logger.warning("ApplicationServices not available - permission checks disabled")


def _get_terminal_app_name() -> str:
    term_program = os.environ.get('TERM_PROGRAM', '')
    if 'iTerm' in term_program:
        return 'iTerm'
    elif term_program == 'Apple_Terminal':
        return 'Terminal'
    elif term_program:
        return term_program.replace('.app', '')
    return 'your terminal app'


def check_accessibility_permission() -> bool:
    if not _appservices_available:
        return True
    return AXIsProcessTrusted()


def request_accessibility_permission():
    if not _appservices_available:
        return
    options = {'AXTrustedCheckOptionPrompt': True}
    AXIsProcessTrustedWithOptions(options)


# ── Microphone permission and System Settings panes ──────────────────────────

# AVCaptureDevice.authorizationStatus(for: .audio) values, as the words the dashboard uses.
MICROPHONE_STATUS_NAMES = {0: 'not_determined', 1: 'restricted', 2: 'denied', 3: 'granted'}

# Deep links into System Settings. A fixed list: callers name a pane, never a URL.
SETTINGS_PANE_URLS = {
    'microphone': 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone',
    'accessibility': 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility',
    'apple_intelligence': 'x-apple.systempreferences:com.apple.Siri-Settings.extension',
}


# 'granted', 'denied', 'restricted' or 'not_determined' (macOS asks on the first recording).
# AVFoundation is loaded through objc so no extra pyobjc package is needed; anything that
# goes wrong reads as 'granted' so a failed check never nags.
def check_microphone_permission() -> str:
    try:
        import objc
        objc.loadBundle('AVFoundation', {}, bundle_path='/System/Library/Frameworks/AVFoundation.framework')
        capture_device = objc.lookUpClass('AVCaptureDevice')
        return MICROPHONE_STATUS_NAMES.get(int(capture_device.authorizationStatusForMediaType_('soun')), 'granted')
    except Exception as error:
        logger.debug(f"Microphone permission check unavailable: {error}")
        return 'granted'


# Opens one System Settings pane by name (see SETTINGS_PANE_URLS). False for an unknown pane.
def open_settings_pane(pane: str) -> bool:
    url = SETTINGS_PANE_URLS.get(pane)
    if url is None:
        return False
    subprocess.run(['/usr/bin/open', url], check=False)
    return True


# Hotkeys and auto-paste both need Accessibility. When it is missing, show
# macOS's own dialog, keep running, and check once a second; on the grant,
# `on_granted` runs (main restarts the hotkey listener), so no restart is
# needed. Returns True when the permission is already there.
def ensure_accessibility(on_granted) -> bool:
    if check_accessibility_permission():
        return True
    request_accessibility_permission()
    app_name = _get_terminal_app_name() if sys.stdin is not None and sys.stdin.isatty() else 'Whisper Local (python)'
    print("🔐 Whisper Local needs Accessibility permission for its hotkeys and auto-paste.")
    print(f"   Turn on {app_name} in System Settings → Privacy & Security → Accessibility.")
    print("   The app keeps running and continues by itself after you allow it.")
    threading.Thread(target=_wait_for_accessibility, args=(on_granted,),
                     daemon=True, name='accessibility-wait').start()
    return False


def _wait_for_accessibility(on_granted):
    while not check_accessibility_permission():
        time.sleep(POLL_SECONDS)
    logger.info("Accessibility permission granted while running")
    # The hotkey monitor was first set up on the main thread, so it is
    # restarted there too (the main loop pumps the NSApp run loop).
    from PyObjCTools import AppHelper
    AppHelper.callAfter(_run_granted_callback, on_granted)


def _run_granted_callback(on_granted):
    try:
        on_granted()
        print("✓ Accessibility permission granted: hotkeys are live")
    except Exception as e:
        logger.error(f"Could not start hotkeys after the permission grant: {e}")
        print(f"❌ Could not start hotkeys after the permission grant ({e}). Restart Whisper Local.")
