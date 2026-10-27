# platform/windows/overlay.py
# Floating pill for Windows: the Tk level overlay, which runs its own mainloop
# on a worker thread (Windows has no main-thread rule for UI).
# macOS mirror: platform/macos/overlay.py (native AppKit panel).

from ...level_overlay import LevelOverlay as Overlay  # noqa: F401
