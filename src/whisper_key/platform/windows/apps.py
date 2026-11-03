# platform/windows/apps.py
# The apps a user can give their own writing style on the dashboard Style page.
# On Windows that is the apps open now plus the apps in dictation History.
# Installed apps are not listed: that needs Start Menu shortcut parsing, and
# Store apps all share one host exe. macOS mirror: platform/macos/apps.py.
import ctypes
import ctypes.wintypes as wt
import logging
import os

logger = logging.getLogger(__name__)

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GW_OWNER = 4

# Windows shell and system processes with windows of their own: nothing a user
# dictates into.
SHELL_EXES = {'explorer.exe', 'progman.exe', 'workerw.exe', 'dwm.exe', 'searchhost.exe',
              'shellexperiencehost.exe', 'startmenuexperiencehost.exe', 'lockapp.exe',
              'sihost.exe', 'textinputhost.exe', 'systemsettings.exe'}

# Store (UWP) apps all run inside this host exe, so they can only share one row.
STORE_APP_HOST = 'applicationframehost.exe'


# Open apps and History apps as {id, name}, sorted by name, no duplicate ids.
# The id is the lowercase exe name, the same value foreground.get_foreground_app()
# reports as 'exe'.
def list_pickable_apps() -> list:
    apps_by_id = {}
    for exe_path in _running_window_exe_paths():
        exe_id = os.path.basename(exe_path).lower()
        if exe_id and exe_id not in apps_by_id:
            apps_by_id[exe_id] = {'id': exe_id, 'name': _display_name(exe_id, exe_path)}
    for exe_id in _history_exe_ids():
        if exe_id not in apps_by_id:
            apps_by_id[exe_id] = {'id': exe_id, 'name': _display_name(exe_id, '')}
    for exe_id in SHELL_EXES:
        apps_by_id.pop(exe_id, None)
    return sorted(apps_by_id.values(), key=lambda app: app['name'].lower())


# ── Running apps ─────────────────────────────────────────────────────────────

# Exe paths of the processes that own a visible, titled, top-level window.
# Skips this process and its parent: the dashboard and the app's own console.
def _running_window_exe_paths() -> list:
    own_pids = {os.getpid(), os.getppid()}
    pids = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def collect_window(hwnd, _lparam):
        if (_user32.IsWindowVisible(hwnd) and _user32.GetWindowTextLengthW(hwnd) > 0
                and not _user32.GetWindow(hwnd, GW_OWNER)):
            pid = wt.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value and pid.value not in own_pids and pid.value not in pids:
                pids.append(pid.value)
        return True

    try:
        _user32.EnumWindows(collect_window, 0)
    except Exception as e:
        logger.debug(f"Window list failed: {e}")
        return []
    return [path for path in (_exe_path(pid) for pid in pids) if path]


# Full exe path of a process, '' when Windows denies access (elevated apps).
def _exe_path(pid: int) -> str:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ''
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buffer))
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ''
    finally:
        _kernel32.CloseHandle(handle)


# ── History apps ─────────────────────────────────────────────────────────────

# Exe names the user dictated into before (each History entry keeps one).
def _history_exe_ids() -> list:
    try:
        from ...transcript_log import load_transcripts
        return [str(entry.get('app') or '').lower() for entry in load_transcripts()
                if str(entry.get('app') or '').lower().endswith('.exe')]
    except Exception as e:
        logger.debug(f"History apps unavailable: {e}")
        return []


# ── Display names ────────────────────────────────────────────────────────────

# The exe's FileDescription ("Visual Studio Code"), else its name ("Code").
def _display_name(exe_id: str, exe_path: str) -> str:
    if exe_id == STORE_APP_HOST:
        return 'Windows Store apps (all)'
    if exe_path:
        description = _file_description(exe_path)
        if description:
            return description
    stem = exe_id[:-4] if exe_id.endswith('.exe') else exe_id
    return stem[:1].upper() + stem[1:]


# The "File description" from the exe's version info, '' when it has none.
def _file_description(exe_path: str) -> str:
    try:
        import win32api
        language, codepage = win32api.GetFileVersionInfo(exe_path, '\\VarFileInfo\\Translation')[0]
        key = f'\\StringFileInfo\\{language:04x}{codepage:04x}\\FileDescription'
        return str(win32api.GetFileVersionInfo(exe_path, key) or '').strip()
    except Exception:
        return ''
