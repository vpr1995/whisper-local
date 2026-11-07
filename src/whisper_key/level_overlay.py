# level_overlay.py
# Floating recording pill for Windows (Tk). Deliberately mirrors the macOS pill in
# platform/macos/overlay.py: rounded dark panel, pulsing status dot, status word,
# 7-bar scrolling level waveform, elapsed timer, wrapped live-transcript preview
# and a green/red flash at the end. Failures show their reason here (not just a
# tray balloon) because Windows focus-assist silently swallows balloons.
# All Tk work is marshalled onto the overlay's own thread via _call().

import logging
import platform
import threading
import time
from collections import deque
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class LevelOverlay:
    # ── Look and feel (same numbers as the macOS pill) ────────────────────────
    WIDTH = 360
    COMPACT_HEIGHT = 44          # status row only ("Listening…")
    PREVIEW_MAX_LINES = 3
    PREVIEW_BOTTOM_PAD = 12
    CORNER_RADIUS = 14
    EDGE_MARGIN = 20
    PADDING = 14
    BAR_COUNT = 7
    BAR_WIDTH = 3
    BAR_GAP = 3
    BAR_MIN_HEIGHT = 4
    BAR_MAX_HEIGHT = 18
    TIMER_WIDTH = 40
    LEVEL_AMP = 25.0             # raw RMS is tiny; same gain as the macOS pill
    UPDATE_HZ = 25
    SUCCESS_HOLD_SECONDS = 0.7
    MESSAGE_HOLD_SECONDS = 2.0
    MAX_PREVIEW_CHARS = 130      # roughly three lines in the panel width
    WINDOW_ALPHA = 0.94

    # Colours (macOS RGB triples converted to hex).
    PANEL_BG = '#16181d'
    TEXT = '#ffffff'
    PREVIEW_TEXT = '#ebebeb'
    RED = '#ff453b'
    AMBER = '#ffb833'
    GREEN = '#40cc66'
    # Tk has no per-pixel alpha, so the area outside the rounded corners is
    # painted in this colour and made transparent. Must not appear in the UI.
    TRANSPARENT_KEY = '#010203'

    POSITIONS = ('bottom-center', 'bottom-right', 'bottom-left',
                 'top-center', 'top-right', 'top-left')

    def __init__(self, level_provider: Callable[[], float],
                 click_through: bool = True,
                 position: str = 'bottom-center'):
        self.level_provider = level_provider
        self.click_through = click_through
        self.position = position if position in self.POSITIONS else 'bottom-center'

        self.root: Optional[object] = None
        self._canvas = None
        self._status_font = None
        self._timer_font = None
        self._preview_font = None
        self._thread = None
        self._ready = threading.Event()

        self._mode = 'hidden'
        self._streaming_text = ''
        self._recording_started_at = 0.0
        self._smoothed_level = 0.0
        self._level_history = deque([0.0] * self.BAR_COUNT, maxlen=self.BAR_COUNT)

        # What the panel currently shows; the draw step only reads these.
        self._status_text = 'Listening…'
        self._status_color = self.TEXT
        self._dot_color = self.RED
        self._dot_pulse = 1.0
        self._bar_color = self.TEXT
        self._bar_heights = [0.0] * self.BAR_COUNT
        self._timer_text = '0:00'
        self._applied_height = 0

        # Bumped on every mode change so delayed actions (the hide after a flash,
        # the animation loop) can tell they were overtaken and stop.
        self._generation = 0
        self._available = self._check_tk()

    def _check_tk(self) -> bool:
        try:
            import tkinter  # noqa
            return True
        except ImportError:
            logger.info("Tkinter not available; level overlay disabled")
            return False

    # ── Public API (mirrors platform/macos/overlay.Overlay) ───────────────────

    def start(self):
        if not self._available or (self._thread and self._thread.is_alive()):
            return
        # The pill needs a Tk mainloop running for the life of the app. Where Tk
        # is main-thread-only (macOS), the main thread is already pumping the
        # NSApplication loop, and a worker-thread root aborts the whole process
        # (issue #14). Stand down rather than take the app with us; every other
        # method is inert once _available is False.
        from .utils import tk_requires_main_thread
        if tk_requires_main_thread():
            logger.info("Level overlay not supported on this platform yet; disabled")
            self._available = False
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name='level-overlay')
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def show_recording(self):
        self._recording_started_at = time.time()
        self._streaming_text = ''
        self._call(lambda: self._enter_mode('recording'))

    def show_processing(self):
        self._call(lambda: self._enter_mode('processing'))

    def hide(self):
        self._call(lambda: self._enter_mode('hidden'))

    def flash_success(self):
        self._flash(self.GREEN, 'Done', self.SUCCESS_HOLD_SECONDS)

    # message: shown in the pill during the flash (held longer so it's readable).
    def flash_failure(self, message: str = None):
        self._flash(self.RED, message or 'Failed', self.MESSAGE_HOLD_SECONDS)

    def set_streaming_text(self, text: str):
        new_text = (text or '').strip()
        if new_text == self._streaming_text:
            return
        self._streaming_text = new_text
        self._call(self._refresh_status_for_mode)

    # A warning shown while recording continues; the next mode change clears it.
    def show_notice(self, message: str):
        def show():
            if self._mode in ('recording', 'processing'):
                self._status_text = message
                self._status_color = self.RED
        self._call(show)

    def set_position(self, name: str):
        if name not in self.POSITIONS:
            return
        self.position = name
        self._call(lambda: self._apply_frame(self._current_height()))

    def shutdown(self):
        if not self.root:
            return
        try:
            self.root.after(0, self.root.destroy)
        except Exception:
            pass

    # ── Thread helper ─────────────────────────────────────────────────────────

    # Run fn on the Tk thread; never raise into the caller's thread.
    def _call(self, fn):
        if not self._available or not self.root:
            return
        try:
            self.root.after(0, fn)
        except Exception as e:
            logger.debug(f"Overlay call failed: {e}")

    # ── Mode changes (Tk thread) ──────────────────────────────────────────────

    def _enter_mode(self, mode: str):
        self._mode = mode
        self._generation += 1
        if mode == 'hidden':
            self._streaming_text = ''
            self._safe(self.root.withdraw)
            return
        self._smoothed_level = 0.0
        self._level_history = deque([0.0] * self.BAR_COUNT, maxlen=self.BAR_COUNT)
        self._bar_color = self.TEXT
        self._refresh_status_for_mode()
        self._apply_frame(self._current_height())
        self._safe(self.root.deiconify)
        self._tick(self._generation)

    # Status word and dot colour for the current mode (also clears any notice).
    def _refresh_status_for_mode(self):
        if self._mode == 'processing':
            self._status_text, self._status_color = 'Transcribing…', self.AMBER
            self._dot_color = self.AMBER
        elif self._mode == 'recording':
            self._status_text = 'Recording' if self._streaming_text else 'Listening…'
            self._status_color = self.TEXT
            self._dot_color = self.RED

    def _flash(self, color: str, message: str, hold_seconds: float):
        if not self._available:
            return

        def show():
            self._mode = 'flash'
            self._generation += 1
            generation = self._generation
            # The flash replaces the live text, so drop it and shrink back to one row.
            self._streaming_text = ''
            self._status_text, self._status_color = message, color
            self._dot_color, self._dot_pulse = color, 1.0
            self._bar_color = color
            self._bar_heights = [1.0] * self.BAR_COUNT
            self._apply_frame(self._current_height())
            self._safe(self.root.deiconify)
            self._render()
            self.root.after(int(hold_seconds * 1000), lambda: self._hide_if_current(generation))
        self._call(show)

    def _hide_if_current(self, generation: int):
        if generation == self._generation:
            self._enter_mode('hidden')

    # ── Window construction (Tk thread) ───────────────────────────────────────

    def _run(self):
        try:
            import tkinter as tk
            from tkinter import font as tkfont
            self.root = tk.Tk()
            self.root.withdraw()
            self.root.overrideredirect(True)
            self.root.attributes('-topmost', True)
            try:
                self.root.attributes('-alpha', self.WINDOW_ALPHA)
                self.root.attributes('-transparentcolor', self.TRANSPARENT_KEY)
            except Exception:
                pass

            self._status_font = tkfont.Font(root=self.root, family='Segoe UI Semibold', size=10)
            self._timer_font = tkfont.Font(root=self.root, family='Consolas', size=9)
            self._preview_font = tkfont.Font(root=self.root, family='Segoe UI', size=10)

            self._canvas = tk.Canvas(self.root, width=self.WIDTH, height=self.COMPACT_HEIGHT,
                                     bg=self.TRANSPARENT_KEY, highlightthickness=0, borderwidth=0)
            self._canvas.pack()

            self._apply_frame(self.COMPACT_HEIGHT)
            self._make_click_through()
            self._ready.set()
            self.root.mainloop()
        except Exception as e:
            logger.warning(f"Level overlay crashed: {e}")
            self._available = False
            self._ready.set()

    # ── Layout (Tk thread) ────────────────────────────────────────────────────

    # Status row plus exactly as many text lines as the preview needs (max 3),
    # so the pill hugs its content instead of leaving an empty band.
    def _current_height(self) -> int:
        if not self._streaming_text or self._canvas is None:
            return self.COMPACT_HEIGHT
        probe = self._canvas.create_text(
            0, 0, text=self._preview_tail(self._streaming_text), font=self._preview_font,
            width=self.WIDTH - 2 * self.PADDING, anchor='nw')
        x0, y0, x1, y1 = self._canvas.bbox(probe)
        self._canvas.delete(probe)
        line_height = self._preview_font.metrics('linespace')
        text_height = min(y1 - y0, line_height * self.PREVIEW_MAX_LINES)
        return int(self.COMPACT_HEIGHT + text_height + self.PREVIEW_BOTTOM_PAD - 4)

    # Usable screen rectangle (left, top, right, bottom) excluding the taskbar,
    # like NSScreen.visibleFrame on macOS. Falls back to the full screen.
    def _work_area(self):
        if platform.system() == 'Windows':
            try:
                import ctypes
                from ctypes import wintypes
                rect = wintypes.RECT()
                SPI_GETWORKAREA = 0x0030
                if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
                    return rect.left, rect.top, rect.right, rect.bottom
            except Exception as e:
                logger.debug(f"Work area lookup failed: {e}")
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    # Resize and place the pill in its corner. Bottom positions keep their bottom
    # edge fixed (the pill grows upward); top positions keep their top edge fixed.
    def _apply_frame(self, height: int):
        if self.root is None or self._canvas is None:
            return
        left, top, right, bottom = self._work_area()
        if self.position.endswith('center'):
            x = left + (right - left - self.WIDTH) // 2
        elif self.position.endswith('right'):
            x = right - self.WIDTH - self.EDGE_MARGIN
        else:
            x = left + self.EDGE_MARGIN
        y = bottom - self.EDGE_MARGIN - height if self.position.startswith('bottom') \
            else top + self.EDGE_MARGIN
        self._canvas.configure(height=height)
        self.root.geometry(f"{self.WIDTH}x{height}+{x}+{y}")
        self._applied_height = height

    def _make_click_through(self):
        if not self.click_through or platform.system() != 'Windows':
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetParent.restype = wintypes.HWND
            user32.GetParent.argtypes = [wintypes.HWND]
            # winfo_id() is Tk's inner child window; the visible top-level window is
            # its parent. Styling the child instead leaves it layered with no opacity
            # set, which makes the whole pill invisible.
            self.root.update_idletasks()
            inner_hwnd = self.root.winfo_id()
            hwnd = user32.GetParent(inner_hwnd) or inner_hwnd
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x80000
            WS_EX_TRANSPARENT = 0x20
            current = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, current | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        except Exception as e:
            logger.debug(f"Click-through setup failed: {e}")

    # ── Animation loop (Tk thread) ────────────────────────────────────────────

    # One frame: update bars/dot/timer for the mode, redraw, schedule the next.
    # A newer mode change bumps _generation, which silently ends an older loop.
    def _tick(self, generation: int):
        if generation != self._generation or self._mode not in ('recording', 'processing'):
            return
        now = time.time()
        if self._mode == 'processing':
            # Gentle travelling wave while the model works.
            self._bar_heights = [0.35 + 0.3 * abs(((now * 2 + i * 0.15) % 2) - 1)
                                 for i in range(self.BAR_COUNT)]
            self._bar_color = self.AMBER
        else:
            try:
                level = float(self.level_provider())
            except Exception:
                level = 0.0
            target = min(1.0, max(0.0, level * self.LEVEL_AMP))
            self._smoothed_level = self._smoothed_level * 0.55 + target * 0.45
            # Scrolling history: the newest level enters on the right.
            self._level_history.append(self._smoothed_level)
            self._bar_heights = list(self._level_history)
            self._bar_color = self.TEXT
            # Pulse the red dot about once a second.
            self._dot_pulse = 0.55 + 0.45 * abs(((now * 1.2) % 2) - 1)
            elapsed = int(now - self._recording_started_at)
            self._timer_text = f"{elapsed // 60}:{elapsed % 60:02d}"

        target_height = self._current_height()
        if target_height != self._applied_height:
            self._apply_frame(target_height)
        self._render()
        try:
            self.root.after(int(1000 / self.UPDATE_HZ), lambda: self._tick(generation))
        except Exception:
            pass

    # ── Drawing (Tk thread) ───────────────────────────────────────────────────

    def _render(self):
        canvas = self._canvas
        if canvas is None:
            return
        try:
            canvas.delete('all')
            height = self._applied_height or self.COMPACT_HEIGHT
            self._draw_rounded_rect(0, 0, self.WIDTH, height, self.CORNER_RADIUS, self.PANEL_BG)

            pad = self.PADDING
            row_center_y = self.COMPACT_HEIGHT / 2

            # Pulsing status dot (blended toward the panel colour to fake opacity).
            dot_color = self._blend(self._dot_color, self.PANEL_BG, self._dot_pulse)
            canvas.create_oval(pad, row_center_y - 4, pad + 8, row_center_y + 4,
                               fill=dot_color, outline='')

            # Timer on the right, waveform to its left, status word fills the rest.
            canvas.create_text(self.WIDTH - pad, row_center_y, text=self._timer_text,
                               fill=self.TEXT, font=self._timer_font, anchor='e')
            bars_width = self.BAR_COUNT * (self.BAR_WIDTH + self.BAR_GAP) - self.BAR_GAP
            bars_left = self.WIDTH - pad - self.TIMER_WIDTH - 10 - bars_width
            for index, level in enumerate(self._bar_heights):
                bar_height = self.BAR_MIN_HEIGHT + (self.BAR_MAX_HEIGHT - self.BAR_MIN_HEIGHT) \
                    * min(1.0, max(0.0, level))
                x = bars_left + index * (self.BAR_WIDTH + self.BAR_GAP)
                canvas.create_rectangle(x, row_center_y - bar_height / 2,
                                        x + self.BAR_WIDTH, row_center_y + bar_height / 2,
                                        fill=self._bar_color, outline='')
            canvas.create_text(pad + 16, row_center_y, text=self._status_text,
                               fill=self._status_color, font=self._status_font, anchor='w',
                               width=bars_left - (pad + 16) - 8)

            if self._streaming_text and self._mode in ('recording', 'processing'):
                canvas.create_text(pad, self.COMPACT_HEIGHT - 4,
                                   text=self._preview_tail(self._streaming_text),
                                   fill=self.PREVIEW_TEXT, font=self._preview_font,
                                   width=self.WIDTH - 2 * pad, anchor='nw')
        except Exception as e:
            logger.debug(f"Overlay draw failed: {e}")

    # Rounded rectangle via a smoothed polygon (Tk canvases have no native one).
    def _draw_rounded_rect(self, x0, y0, x1, y1, radius, fill):
        points = [x0 + radius, y0, x1 - radius, y0, x1, y0, x1, y0 + radius,
                  x1, y1 - radius, x1, y1, x1 - radius, y1, x0 + radius, y1,
                  x0, y1, x0, y1 - radius, x0, y0 + radius, x0, y0]
        self._canvas.create_polygon(points, smooth=True, fill=fill, outline='')

    # Keep the newest words visible: the start of a long sentence scrolls away.
    def _preview_tail(self, text: str) -> str:
        if len(text) <= self.MAX_PREVIEW_CHARS:
            return text
        tail = text[-self.MAX_PREVIEW_CHARS:]
        first_space = tail.find(' ')
        if 0 <= first_space < 20:
            tail = tail[first_space + 1:]
        return '…' + tail

    # Mixes foreground_hex over background_hex; opacity 1.0 = pure foreground.
    @staticmethod
    def _blend(foreground_hex: str, background_hex: str, opacity: float) -> str:
        fg = [int(foreground_hex[i:i + 2], 16) for i in (1, 3, 5)]
        bg = [int(background_hex[i:i + 2], 16) for i in (1, 3, 5)]
        return '#' + ''.join(f"{round(f * opacity + b * (1 - opacity)):02x}" for f, b in zip(fg, bg))

    # Run a Tk call that may fail once the window is gone, without raising.
    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception:
            pass
