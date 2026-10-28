# platform/windows/app.py
# Windows app-lifecycle bits: single-keypress input (msvcrt) and the thread
# requirements the main loop must respect. Windows needs no special main-thread
# handling, so these are mostly trivial.
# macOS mirror: platform/macos/app.py (NSApplication run loop, which DOES
# require the main thread).
import msvcrt

# Tk windows each run their own root on a worker thread, which Windows allows.
# macOS mirror is True: Tk there must live on the main thread.
TK_MAIN_THREAD_ONLY = False


def setup():
    pass

def run_event_loop(shutdown_event):
    while not shutdown_event.wait(timeout=0.1):
        pass

# Gives the process its own taskbar identity so the dashboard window groups
# under "Whisper Local" with our icon instead of python.exe's. The window icon
# itself is passed to pywebview (winforms honours it), not set here.
# macOS mirror: platform/macos/app.py, which also sets the Dock icon.
def set_process_identity(app_name: str, icon_path: str):
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('WhisperLocal.Dashboard')

def getch():
    return msvcrt.getwch()


# Windows has no main-thread requirement for tray/UI objects, so this just
# calls through. It exists so callers never have to branch on platform.
# macOS mirror marshals onto the main thread — see the note there.
def run_on_ui_thread(fn):
    fn()


# The user's locale language as a code ("en", "de"), '' if unknown.
def system_language() -> str:
    import ctypes
    buffer = ctypes.create_unicode_buffer(85)
    if not ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
        return ''
    return buffer.value.split('-')[0].lower()
