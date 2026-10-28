# platform/macos/app.py
# macOS app lifecycle: runs an NSApplication as an accessory (no Dock icon) and
# pumps its run loop, which Cocoa requires on the MAIN thread — the reason the
# platform layer exposes thread requirements at all.
# Windows mirror: platform/windows/app.py (no such constraint).
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSEventMaskAny, NSDefaultRunLoopMode
from Foundation import NSDate, NSObject, NSOperationQueue, NSThread

# Uniquely named: Objective-C class names are process-global, and pywebview (the
# dashboard) defines its own "AppDelegate"; sharing the name aborts the import.
class WhisperLocalAppDelegate(NSObject):
    def applicationSupportsSecureRestorableState_(self, app):
        return True

_delegate = None

# ── Tk coexistence (issue #14) ────────────────────────────────────────────────
# Tk on macOS draws through Cocoa, so a Tk window can only be created on the
# main thread, which here belongs to the NSApplication run loop. Windows that
# normally run on a worker thread (level overlay, first-run welcome) read this
# through utils.tk_requires_main_thread() and adapt.
TK_MAIN_THREAD_ONLY = True

# Held for the process lifetime; see _preload_tk().
_tk_preload_root = None


# Let Tk create the shared NSApplication before pyobjc does.
#
# Whoever calls sharedApplication() first decides its class. Tk 9 installs its
# own subclass (TKApplication) and its drawing code then calls selectors only
# that subclass has, e.g. -macOSVersion from GetRGBA. If pyobjc gets there
# first, NSApp is a plain NSApplication and the first later tk.Tk() dies on an
# unrecognised selector. That's an NSException, not a Python exception, so no
# try/except can catch it: the process aborts with exit 134.
#
# The hidden root must stay alive: destroying the last Tk root tears down Tk's
# app-level state, and the next tk.Tk() would start the fight again. Best effort
# only. Without Tk (or without a display) we fall back to a plain NSApplication
# and the Tk windows skip themselves.
def _preload_tk():
    global _tk_preload_root
    try:
        import tkinter as tk
        _tk_preload_root = tk.Tk()
        _tk_preload_root.withdraw()
    except Exception:
        _tk_preload_root = None


def setup():
    global _delegate
    _preload_tk()  # must run before sharedApplication() below
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    _delegate = WhisperLocalAppDelegate.alloc().init()
    app.setDelegate_(_delegate)

# ── Process identity (dashboard child) ───────────────────────────────────────
# A plain `python -m ...` process has no .app bundle, so macOS labels it with
# the Python interpreter's bundle: rocket icon in the Dock, "Python" in the menu
# bar. This patches what can be patched at runtime. Call it in the process that
# owns a regular (Dock-visible) window, before the window is created.
#
# - Menu-bar app-menu title: AppKit builds it from CFBundleName in the main
#   bundle's info dictionary, which is a mutable dict, so overriding it works.
# - Dock icon: NSApplication.setApplicationIconImage_ overrides the interpreter's.
# - Dock hover label and Cmd-Tab name: LaunchServices derives these from the
#   on-disk bundle (the interpreter's "Python"), which cannot be changed at
#   runtime; only a real .app bundle fixes those.
def set_process_identity(app_name: str, icon_path: str):
    from AppKit import NSImage
    from Foundation import NSBundle, NSProcessInfo
    info = NSBundle.mainBundle().infoDictionary()
    if info is not None:
        for key in ('CFBundleName', 'CFBundleDisplayName'):
            info[key] = app_name
    NSProcessInfo.processInfo().setProcessName_(app_name)
    # Creating NSApp here is what pywebview would do next anyway (plain class).
    app = NSApplication.sharedApplication()
    icon_image = NSImage.alloc().initWithContentsOfFile_(icon_path)
    if icon_image is not None:
        app.setApplicationIconImage_(icon_image)

def getch():
    import tty
    import termios
    import sys
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch

# Run fn on the main thread, because Cocoa requires every AppKit mutation to
# happen there.
#
# This is not a nicety. Through macOS 26 a background-thread menu-bar write
# only logged a warning; macOS 27 made it a hard trap — BSServiceMainRunLoopQueue's
# barrier assertion raises SIGTRAP and the process dies instantly (issue #13).
# SIGTRAP is not a Python exception, so no try/except at the call site can save
# it; the write simply must not happen off-thread.
#
# Dispatch is async on purpose: the callers are the recording thread and the
# level monitor, and neither may block on the UI. Blocks queued before the run
# loop starts simply run once it does. run_event_loop() below pumps
# NSDefaultRunLoopMode, which services the main queue.
def run_on_ui_thread(fn):
    if NSThread.isMainThread():
        fn()
        return
    NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


def run_event_loop(shutdown_event):
    app = NSApplication.sharedApplication()
    while not shutdown_event.is_set():
        event = app.nextEventMatchingMask_untilDate_inMode_dequeue_(
            NSEventMaskAny,
            NSDate.dateWithTimeIntervalSinceNow_(0.1),
            NSDefaultRunLoopMode,
            True
        )
        if event:
            app.sendEvent_(event)


# The user's first preferred language as a code ("en", "de"). Read from
# NSLocale, because a LaunchAgent start has no LANG to go by. '' if unknown.
def system_language() -> str:
    from Foundation import NSLocale
    preferred = NSLocale.preferredLanguages()
    return str(preferred[0]).split('-')[0].lower() if preferred else ''
