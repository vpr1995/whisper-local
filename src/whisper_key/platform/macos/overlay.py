# platform/macos/overlay.py
# Native floating pill for macOS: a blurred, rounded HUD panel that shows the
# recording state, a live level waveform, the elapsed time and the streaming
# transcript while you talk. Built on AppKit (not Tk) because Tk cannot run off
# the main thread here, and a non-activating NSPanel never steals focus from the
# text field the final result is pasted into.
# Windows mirror: platform/windows/overlay.py (the Tk pill in level_overlay.py).

import logging
import threading
import time
from collections import deque
from typing import Callable

from . import app as platform_app

logger = logging.getLogger(__name__)

try:
    from AppKit import (
        NSAnimationContext, NSAppearance, NSBackingStoreBuffered, NSColor, NSFont,
        NSPanel, NSScreen, NSStatusWindowLevel, NSTextField, NSView, NSVisualEffectView,
    )
    from Foundation import NSMakeRect
    _APPKIT_AVAILABLE = True
except ImportError:
    _APPKIT_AVAILABLE = False


# ── AppKit constants ──────────────────────────────────────────────────────────
# Spelled out numerically so the module does not depend on which names a given
# pyobjc release happens to export.
STYLE_BORDERLESS = 0
STYLE_NONACTIVATING_PANEL = 1 << 7
BEHAVIOR_ALL_SPACES = 1 << 0
BEHAVIOR_STATIONARY = 1 << 4
BEHAVIOR_FULLSCREEN_AUXILIARY = 1 << 8
MATERIAL_HUD_WINDOW = 13
BLENDING_BEHIND_WINDOW = 0
EFFECT_STATE_ACTIVE = 1
LINE_BREAK_WORD_WRAP = 0
ALIGN_RIGHT = 2


class Overlay:
    # ── Look and feel ─────────────────────────────────────────────────────────
    WIDTH = 360
    COMPACT_HEIGHT = 44      # status row only ("Listening…")
    PREVIEW_MAX_LINES = 3
    PREVIEW_BOTTOM_PAD = 12
    CORNER_RADIUS = 14
    TINT_ALPHA = 0.62
    EDGE_MARGIN = 20
    PADDING = 14
    BAR_COUNT = 7
    BAR_WIDTH = 3
    BAR_GAP = 3
    BAR_MAX_HEIGHT = 18
    LEVEL_AMP = 25.0         # same gain as the Tk pill: raw RMS is tiny
    UPDATE_HZ = 25
    FADE_SECONDS = 0.18
    SUCCESS_HOLD_SECONDS = 0.7
    MESSAGE_HOLD_SECONDS = 2.0
    MAX_PREVIEW_CHARS = 130  # roughly three lines at 13 pt in the panel width

    RED = (1.0, 0.27, 0.23)
    AMBER = (1.0, 0.72, 0.2)
    GREEN = (0.25, 0.8, 0.4)

    POSITIONS = ('bottom-center', 'bottom-right', 'bottom-left',
                 'top-center', 'top-right', 'top-left')

    def __init__(self, level_provider: Callable[[], float],
                 click_through: bool = True,
                 position: str = 'bottom-center'):
        self.level_provider = level_provider
        self.click_through = click_through
        self.position = position if position in self.POSITIONS else 'bottom-center'
        self._available = _APPKIT_AVAILABLE

        self._panel = None
        self._mode = 'hidden'
        self._streaming_text = ''
        self._recording_started_at = 0.0
        self._smoothed_level = 0.0
        self._level_history = deque([0.0] * self.BAR_COUNT, maxlen=self.BAR_COUNT)
        # Bumped on every mode change so delayed actions (a fade-out after a
        # flash) can tell they have been overtaken and do nothing.
        self._generation = 0
        self._ticker_thread = None
        self._tick_pending = False

    # ── Public API (mirrors level_overlay.LevelOverlay) ───────────────────────

    def start(self):
        if not self._available or self._panel is not None:
            return
        platform_app.run_on_ui_thread(self._build_panel_safely)

    def show_recording(self):
        self._recording_started_at = time.time()
        self._streaming_text = ''
        self._enter_mode('recording')

    def show_processing(self):
        self._enter_mode('processing')

    def hide(self):
        self._enter_mode('hidden')

    def flash_success(self):
        self._flash(self.GREEN, 'Done', self.SUCCESS_HOLD_SECONDS)

    def flash_failure(self, message: str = None):
        self._flash(self.RED, message or 'Failed', self.MESSAGE_HOLD_SECONDS)

    def set_streaming_text(self, text: str):
        new_text = (text or '').strip()
        if new_text == self._streaming_text:
            return
        self._streaming_text = new_text
        self._ui(self._refresh_content)

    # A warning shown while recording continues; the next mode change clears it.
    def show_notice(self, message: str):
        def show():
            if self._mode in ('recording', 'processing'):
                self._set_status(message, self.RED)
        self._ui(show)

    def set_position(self, name: str):
        if name not in self.POSITIONS:
            return
        self.position = name
        self._ui(lambda: self._apply_frame(self._current_height()))

    def shutdown(self):
        self._mode = 'hidden'
        self._generation += 1
        self._ui(lambda: self._panel.orderOut_(None))

    # ── Mode changes ──────────────────────────────────────────────────────────

    def _enter_mode(self, mode: str):
        if not self._available:
            return
        self._mode = mode
        self._generation += 1
        generation = self._generation
        if mode == 'hidden':
            self._streaming_text = ''
            self._ui(lambda: self._fade_out(generation))
            return
        self._ui(self._show_panel)
        self._start_ticker()

    def _flash(self, color, message: str, hold_seconds: float):
        if not self._available:
            return
        self._mode = 'flash'
        self._generation += 1
        generation = self._generation

        def show():
            self._show_panel()
            self._dot.setAlphaValue_(1.0)
            self._set_status(message, color, dot_color=color)
            self._draw_bars([1.0] * self.BAR_COUNT, color)
        self._ui(show)
        self._after(hold_seconds, generation, lambda: self._fade_out(generation))

    # ── Panel construction (UI thread) ────────────────────────────────────────

    def _build_panel_safely(self):
        try:
            self._build_panel()
        except Exception as e:
            logger.warning(f"Floating overlay disabled: {e}")
            self._available = False
            self._panel = None

    def _build_panel(self):
        frame = NSMakeRect(0, 0, self.WIDTH, self.COMPACT_HEIGHT)
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, STYLE_BORDERLESS | STYLE_NONACTIVATING_PANEL, NSBackingStoreBuffered, False)
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setFloatingPanel_(True)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setIgnoresMouseEvents_(self.click_through)
        panel.setCollectionBehavior_(
            BEHAVIOR_ALL_SPACES | BEHAVIOR_STATIONARY | BEHAVIOR_FULLSCREEN_AUXILIARY)
        panel.setAppearance_(NSAppearance.appearanceNamed_('NSAppearanceNameVibrantDark'))
        panel.setAlphaValue_(0.0)

        # Frosted background with rounded corners.
        background = NSVisualEffectView.alloc().initWithFrame_(frame)
        background.setMaterial_(MATERIAL_HUD_WINDOW)
        background.setBlendingMode_(BLENDING_BEHIND_WINDOW)
        background.setState_(EFFECT_STATE_ACTIVE)
        background.setWantsLayer_(True)
        background.layer().setCornerRadius_(self.CORNER_RADIUS)
        background.layer().setMasksToBounds_(True)
        panel.setContentView_(background)

        # Dark tint over the blur so white text stays readable on light windows.
        self._tint = NSView.alloc().initWithFrame_(frame)
        self._tint.setWantsLayer_(True)
        self._tint.layer().setBackgroundColor_(
            NSColor.colorWithWhite_alpha_(0.08, self.TINT_ALPHA).CGColor())
        background.addSubview_(self._tint)

        # Status row: pulsing dot, status text, waveform, timer.
        self._dot = self._make_layer_view(8, 8, corner_radius=4)
        self._status_label = self._make_label(13, bold=True)
        self._timer_label = self._make_label(12, monospaced_digits=True)
        self._timer_label.setAlignment_(ALIGN_RIGHT)
        self._bars = [self._make_layer_view(self.BAR_WIDTH, 4, corner_radius=1.5)
                      for _ in range(self.BAR_COUNT)]

        # Live transcript: wraps over up to three lines below the status row.
        self._preview_label = NSTextField.wrappingLabelWithString_('')
        self._preview_label.setFont_(NSFont.systemFontOfSize_(13))
        self._preview_label.setTextColor_(NSColor.colorWithWhite_alpha_(1.0, 0.92))
        self._preview_label.setMaximumNumberOfLines_(3)
        self._preview_label.cell().setLineBreakMode_(LINE_BREAK_WORD_WRAP)

        for view in [self._dot, self._status_label, self._timer_label,
                     self._preview_label, *self._bars]:
            background.addSubview_(view)

        self._panel = panel
        self._apply_frame(self.COMPACT_HEIGHT)

    def _make_layer_view(self, width, height, corner_radius):
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(corner_radius)
        return view

    def _make_label(self, size, bold=False, monospaced_digits=False):
        label = NSTextField.labelWithString_('')
        if monospaced_digits:
            font = NSFont.monospacedDigitSystemFontOfSize_weight_(size, 0.0)
        elif bold:
            font = NSFont.systemFontOfSize_weight_(size, 0.3)  # semibold
        else:
            font = NSFont.systemFontOfSize_(size)
        label.setFont_(font)
        label.setTextColor_(NSColor.whiteColor())
        return label

    # ── Layout (UI thread) ────────────────────────────────────────────────────

    # Status row plus exactly as many text lines as the preview needs (max 3),
    # so the panel hugs its content instead of leaving an empty band.
    def _current_height(self):
        if not self._streaming_text or self._panel is None:
            return self.COMPACT_HEIGHT
        text_width = self.WIDTH - 2 * self.PADDING
        self._preview_label.setStringValue_(self._preview_tail(self._streaming_text))
        needed = self._preview_label.cell().cellSizeForBounds_(
            NSMakeRect(0, 0, text_width, 10000)).height
        line_height = self._preview_label.font().boundingRectForFont().size.height
        text_height = min(needed, line_height * self.PREVIEW_MAX_LINES)
        return int(self.COMPACT_HEIGHT + text_height + self.PREVIEW_BOTTOM_PAD - 4)

    # Resize and place the panel in its corner; the status row stays on top and
    # the preview text fills whatever space is below it.
    def _apply_frame(self, height):
        if self._panel is None:
            return
        screen = NSScreen.mainScreen() or NSScreen.screens()[0]
        visible = screen.visibleFrame()
        min_x, min_y = visible.origin.x, visible.origin.y
        max_x = min_x + visible.size.width
        max_y = min_y + visible.size.height

        if self.position.endswith('center'):
            x = min_x + (visible.size.width - self.WIDTH) / 2
        elif self.position.endswith('right'):
            x = max_x - self.WIDTH - self.EDGE_MARGIN
        else:
            x = min_x + self.EDGE_MARGIN
        # Cocoa's origin is bottom-left: bottom positions grow upward, top
        # positions grow downward from a fixed top edge.
        if self.position.startswith('bottom'):
            y = min_y + self.EDGE_MARGIN
        else:
            y = max_y - self.EDGE_MARGIN - height

        self._panel.setFrame_display_(NSMakeRect(x, y, self.WIDTH, height), True)
        self._layout_subviews(height)

    def _layout_subviews(self, height):
        pad = self.PADDING
        row_center = height - self.COMPACT_HEIGHT / 2
        self._dot.setFrame_(NSMakeRect(pad, row_center - 4, 8, 8))

        timer_width = 40
        self._timer_label.setFrame_(NSMakeRect(
            self.WIDTH - pad - timer_width, row_center - 8, timer_width, 16))

        bars_width = self.BAR_COUNT * (self.BAR_WIDTH + self.BAR_GAP) - self.BAR_GAP
        self._bars_left = self.WIDTH - pad - timer_width - 10 - bars_width
        self._bars_center_y = row_center

        status_left = pad + 16
        self._status_label.setFrame_(NSMakeRect(
            status_left, row_center - 9, self._bars_left - status_left - 8, 18))

        self._tint.setFrame_(NSMakeRect(0, 0, self.WIDTH, height))

        preview_height = height - self.COMPACT_HEIGHT + 4 - self.PREVIEW_BOTTOM_PAD
        self._preview_label.setFrame_(NSMakeRect(
            pad, self.PREVIEW_BOTTOM_PAD, self.WIDTH - 2 * pad, max(0, preview_height)))
        self._preview_label.setHidden_(preview_height <= 0)

    # ── Rendering (UI thread) ─────────────────────────────────────────────────

    def _show_panel(self):
        if self._panel is None:
            return
        self._apply_frame(self._current_height())
        self._refresh_content()
        self._panel.orderFrontRegardless()
        self._animate_alpha(1.0)

    def _fade_out(self, generation):
        if self._panel is None or generation != self._generation:
            return
        self._animate_alpha(0.0)

        def order_out():
            if generation == self._generation and self._panel is not None:
                self._panel.orderOut_(None)
        self._after(self.FADE_SECONDS + 0.05, generation, order_out)

    def _animate_alpha(self, alpha):
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(self.FADE_SECONDS)
        self._panel.animator().setAlphaValue_(alpha)
        NSAnimationContext.endGrouping()

    # Status row text + preview text for the current mode.
    def _refresh_content(self):
        if self._panel is None or self._mode not in ('recording', 'processing'):
            return
        if self._mode == 'processing':
            self._set_status('Transcribing…', self.AMBER, dot_color=self.AMBER)
        else:
            self._set_status('Listening…' if not self._streaming_text else 'Recording',
                             None, dot_color=self.RED)

        target_height = self._current_height()
        if abs(self._panel.frame().size.height - target_height) > 0.5:
            self._apply_frame(target_height)
        self._preview_label.setStringValue_(self._preview_tail(self._streaming_text))

    # Keep the newest words visible: the start of a long sentence scrolls away.
    def _preview_tail(self, text):
        if len(text) <= self.MAX_PREVIEW_CHARS:
            return text
        tail = text[-self.MAX_PREVIEW_CHARS:]
        first_space = tail.find(' ')
        if 0 <= first_space < 20:
            tail = tail[first_space + 1:]
        return '…' + tail

    def _set_status(self, text, text_color=None, dot_color=None):
        self._status_label.setStringValue_(text)
        self._status_label.setTextColor_(
            self._color(text_color) if text_color else NSColor.whiteColor())
        if dot_color:
            self._dot.layer().setBackgroundColor_(self._color(dot_color).CGColor())

    def _draw_bars(self, heights, rgb):
        cg_color = self._color(rgb).CGColor()
        for index, bar in enumerate(self._bars):
            bar_height = 4 + (self.BAR_MAX_HEIGHT - 4) * min(1.0, max(0.0, heights[index]))
            x = self._bars_left + index * (self.BAR_WIDTH + self.BAR_GAP)
            bar.setFrame_(NSMakeRect(x, self._bars_center_y - bar_height / 2,
                                     self.BAR_WIDTH, bar_height))
            bar.layer().setBackgroundColor_(cg_color)

    def _color(self, rgb):
        return NSColor.colorWithSRGBRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], 1.0)

    # ── Animation ticker ──────────────────────────────────────────────────────
    # A small worker thread paces the animation and hands each frame to the UI
    # thread. It skips a frame while the previous one is still queued, so a busy
    # main thread never builds up a backlog.

    def _start_ticker(self):
        if self._ticker_thread and self._ticker_thread.is_alive():
            return
        self._ticker_thread = threading.Thread(
            target=self._ticker_loop, daemon=True, name='floating-overlay-ticker')
        self._ticker_thread.start()

    def _ticker_loop(self):
        while self._mode in ('recording', 'processing'):
            if not self._tick_pending:
                self._tick_pending = True
                self._ui(self._tick)
            time.sleep(1.0 / self.UPDATE_HZ)

    def _tick(self):
        self._tick_pending = False
        if self._panel is None or self._mode not in ('recording', 'processing'):
            return
        now = time.time()

        if self._mode == 'processing':
            # Gentle travelling wave while Whisper works.
            heights = [0.35 + 0.3 * abs(((now * 2 + i * 0.15) % 2) - 1)
                       for i in range(self.BAR_COUNT)]
            self._draw_bars(heights, self.AMBER)
            return

        try:
            level = float(self.level_provider())
        except Exception:
            level = 0.0
        target = min(1.0, max(0.0, level * self.LEVEL_AMP))
        self._smoothed_level = self._smoothed_level * 0.55 + target * 0.45
        # Scrolling history: the newest level enters on the right.
        self._level_history.append(self._smoothed_level)
        self._draw_bars(list(self._level_history), (1.0, 1.0, 1.0))

        # Pulse the red dot about once a second.
        self._dot.setAlphaValue_(0.55 + 0.45 * abs(((now * 1.2) % 2) - 1))
        elapsed = int(now - self._recording_started_at)
        self._timer_label.setStringValue_(f"{elapsed // 60}:{elapsed % 60:02d}")

    # ── Thread helpers ────────────────────────────────────────────────────────

    # Run fn on the main thread; never raise into the caller's thread.
    def _ui(self, fn):
        if not self._available:
            return

        def guarded():
            if self._panel is None:
                return
            try:
                fn()
            except Exception as e:
                logger.warning(f"Floating overlay update failed: {e}")
        platform_app.run_on_ui_thread(guarded)

    # Run fn on the main thread after a delay, unless the mode changed meanwhile.
    def _after(self, seconds, generation, fn):
        def fire():
            if generation == self._generation:
                self._ui(fn)
        timer = threading.Timer(seconds, fire)
        timer.daemon = True
        timer.start()
