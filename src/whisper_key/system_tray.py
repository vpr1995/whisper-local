# system_tray.py
# The always-visible control surface: tray icon, balloon notifications, and a
# deliberately short right-click menu (status, start/stop dictation, model /
# microphone / auto-paste quick pickers, open dashboard, quit). Everything else
# lives in the dashboard window. The icon doubles as a state indicator — it
# swaps art for idle/recording/processing so the user can see what the app is
# doing at a glance. pystray/Pillow are imported defensively so a machine
# without a usable tray still runs headless instead of crashing.

import logging
import os
import signal
from typing import Optional, TYPE_CHECKING

from .platform import app as platform_app, permissions, icons, console

try:
    import pystray
    from PIL import Image
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    pystray = None
    Image = None

if TYPE_CHECKING:
    from .state_manager import StateManager
    from .config_manager import ConfigManager

class SystemTray:
    def __init__(self,
                 state_manager: 'StateManager',
                 tray_config: dict = None,
                 config_manager: Optional['ConfigManager'] = None,
                 model_registry = None,
                 console_config: dict = None):

        self.state_manager = state_manager
        self.tray_config = tray_config or {}
        self.config_manager = config_manager
        self.model_registry = model_registry
        self.console_config = console_config or {}
        self.logger = logging.getLogger(__name__)
               
        self.icon = None  # pystray object, holds menu, state, etc.
        self.is_running = False
        self.current_state = "idle"
        self.available = True
        
        if self._check_tray_availability():
            self._load_icons_to_cache()
    
    def _check_tray_availability(self) -> bool:
        if not self.tray_config['enabled']:
            self.logger.warning("   ✗ System tray disabled in configuration")
            self.available = False

        elif not TRAY_AVAILABLE:
            self.logger.warning("   ✗ System tray not available - pystray or Pillow not installed")
            self.available = False

        return self.available

    def _load_icons_to_cache(self):
        try:
            self.icons = icons.get_tray_icons()
        except Exception as e:
            self.logger.error(f"Failed to load tray icons: {e}")
            self.icons = {
                "idle": self._create_fallback_icon("idle"),
                "recording": self._create_fallback_icon("recording"),
                "processing": self._create_fallback_icon("processing"),
                "sleeping": self._create_fallback_icon("sleeping"),
            }
        
    def _create_fallback_icon(self, state: str) -> Image.Image:
        colors = {
            'idle': (128, 128, 128),      # Gray
            'recording': (34, 139, 34),   # Green  
            'processing': (255, 165, 0),  # Orange
            'sleeping': (88, 86, 214),    # Indigo: model unloaded after idle
        }
        
        color = colors.get(state, (128, 128, 128))  # Default to gray
        icon = Image.new('RGBA', (16, 16), color + (255,))

        return icon
    
    def _build_model_menu_items(self, current_model: str, is_model_loading: bool) -> list:
        items = []

        no_models_item = pystray.MenuItem("No downloaded models", None, enabled=False)

        if not self.model_registry:
            return [no_models_item]

        def make_model_selector(model_key):
            return lambda icon, item: self._select_model(model_key)

        def make_is_current(model_key):
            return lambda item: model_key == current_model

        def model_selection_enabled(item):
            return not is_model_loading

        first_group = True
        for group in self.model_registry.get_groups_ordered():
            # Only downloaded models are offered (picking one would otherwise
            # trigger a surprise multi-hundred-MB download); the current model
            # always stays so the radio check never vanishes.
            models = [m for m in self.model_registry.get_models_by_group(group)
                      if m.key == current_model or self.model_registry.is_model_cached(m.key)]
            if not models:
                continue

            if not first_group:
                items.append(pystray.Menu.SEPARATOR)
            first_group = False

            for model in models:
                items.append(pystray.MenuItem(
                    model.label,
                    make_model_selector(model.key),
                    radio=True,
                    checked=make_is_current(model.key),
                    enabled=model_selection_enabled
                ))

        # pystray rejects an empty submenu; show a disabled hint instead.
        if not items:
            items.append(no_models_item)

        return items

    # ── Menu status + dictation toggle ────────────────────────────────────────

    # One-line status for the disabled header row. Reads the lock-free
    # get_application_state() flags (not get_current_state(), which takes the
    # state lock) because pystray may evaluate this while the UI thread is
    # being marshalled to by a thread that already holds that lock.
    def _status_text(self, item=None) -> str:
        try:
            app_state = self.state_manager.get_application_state()
            if app_state.get('model_loading'):
                return "Whisper Local: Loading model…"
            if app_state.get('recording'):
                return "Whisper Local: Listening…"
            if app_state.get('processing'):
                return "Whisper Local: Transcribing…"
            if self.state_manager.is_hotkeys_paused():
                return "Whisper Local: Hotkeys paused"
        except Exception as e:
            self.logger.debug(f"Could not read state for tray status: {e}")
        return "Whisper Local: Ready"

    # "Ctrl+Win" rather than the config's lowercase "ctrl+win" so the label
    # reads like a keycap hint next to the action.
    def _format_hotkey_label(self, hotkey_string: str) -> str:
        return "+".join(part.strip().capitalize() for part in hotkey_string.split("+") if part.strip())

    def _dictation_toggle_label(self, item=None) -> str:
        try:
            if self.state_manager.get_application_state().get('recording'):
                return "Stop dictation"
            hotkey = self.config_manager.get_setting('hotkey', 'recording_hotkey')
        except Exception:
            return "Start dictation"
        return f"Start dictation   {self._format_hotkey_label(hotkey)}" if hotkey else "Start dictation"

    # Mouse equivalent of the record hotkey: calls the same state_manager entry
    # points the hotkey listener does (start_recording / stop_recording). Run on
    # its own thread, like a hotkey callback, because starting the mic and
    # playing the start sound must not block the tray's UI thread.
    def _toggle_dictation(self, icon=None, item=None):
        import threading

        def work():
            try:
                if self.state_manager.get_application_state().get('recording'):
                    self.state_manager.stop_recording()
                else:
                    self.state_manager.start_recording()
            except Exception as e:
                self.logger.error(f"Failed to toggle dictation from tray: {e}")
        threading.Thread(target=work, daemon=True, name='tray-dictation-toggle').start()

    # Shows the dashboard on `page`; if it is already open it just navigates.
    def _open_dashboard(self, page: str = "home", icon=None, item=None):
        from . import window_launcher
        window_launcher.open_dashboard(page, on_app_command=self._handle_dashboard_command)

    # Runs the app-side half of a command the dashboard sent (the launcher has
    # already checked it against its whitelist). Called on the launcher's reader
    # thread, so it must not block for long. The menu refresh after a pause
    # toggle happens in StateManager.set_paused.
    def _handle_dashboard_command(self, command: str):
        if command == 'restart':
            self._restart_application_from_tray()
        elif command == 'retry_last_dictation':
            self.state_manager.retry_last_dictation()
        elif command == 'toggle_pause_hotkeys':
            self.state_manager.toggle_hotkeys_paused()
        else:
            self.logger.warning(f"Unhandled dashboard command: {command}")

    # ── Menu construction ─────────────────────────────────────────────────────

    # Rebuilt from scratch on every refresh rather than mutated in place, because
    # pystray menus are immutable once shown — this is how checkmarks and the
    # model list stay current. Deliberately short: status → dictation toggle →
    # quick pickers (model / mic / auto-paste) → dashboard → quit. Everything
    # else lives in the dashboard window.
    def _create_menu(self):
        try:
            app_state = self.state_manager.get_application_state()
            is_model_loading = app_state.get('model_loading', False)

            # With `auto` the config key isn't a menu item, so the check mark
            # follows the model the engine runs.
            configured_model = self.config_manager.get_setting('whisper', 'model')
            current_model = getattr(self.state_manager.whisper_engine, 'model_key', configured_model)

            current_host = self.state_manager.get_current_audio_host()
            available_devices = self.state_manager.get_available_audio_devices(current_host)
            current_device = self.state_manager.get_current_audio_device_id()

            def is_current_device(dev_id):
                return lambda item: current_device == dev_id

            def switch_device(dev_id, dev_name):
                return lambda icon, item: self._select_audio_device(dev_id, dev_name)

            audio_device_items = []
            for device in (available_devices or []):
                audio_device_items.append(
                    pystray.MenuItem(
                        device['name'],
                        switch_device(device['id'], device['name']),
                        radio=True,
                        checked=is_current_device(device['id'])
                    )
                )

            model_sub_menu_items = self._build_model_menu_items(current_model, is_model_loading)

            menu_items = []

            # Windows pyapp builds own a console; it is the double-click target
            # there. Everywhere else double-click opens the dashboard.
            owns_console = console.owns_console()
            if owns_console:
                menu_items.append(pystray.MenuItem("Show Console", self._show_console, default=True))
                menu_items.append(pystray.Menu.SEPARATOR)

            menu_items += [
                # Disabled row: text only, callable so it tracks live state.
                pystray.MenuItem(self._status_text, None, enabled=False),
                pystray.MenuItem(
                    self._dictation_toggle_label,
                    self._toggle_dictation,
                    enabled=not is_model_loading,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    f"Model: {current_model.title()}" + (" (auto)" if configured_model == 'auto' else ""),
                    pystray.Menu(*model_sub_menu_items)
                ),
                pystray.MenuItem("Microphone", pystray.Menu(*audio_device_items)),
                # Single checkbox: unchecked means "copy to clipboard only".
                # Reads config fresh so the box is right even if settings
                # changed elsewhere since the menu was built.
                pystray.MenuItem(
                    "Auto-paste",
                    lambda icon, item: self._set_transcription_mode(
                        not self.config_manager.get_setting('clipboard', 'auto_paste')),
                    checked=lambda item: bool(self.config_manager.get_setting('clipboard', 'auto_paste')),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Open Whisper Local…",
                    lambda icon, item: self._open_dashboard(page="home"),
                    default=not owns_console,
                ),
                pystray.MenuItem(
                    "Settings…",
                    lambda icon, item: self._open_dashboard(page="settings"),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit Whisper Local", self._quit_application_from_tray),
            ]

            return pystray.Menu(*menu_items)

        except Exception as e:
            self.logger.error(f"Error in _create_menu: {e}")
            raise

    def _set_transcription_mode(self, auto_paste: bool):
        # Paste needs Accessibility: show macOS's dialog; pasting starts
        # working as soon as the user allows it.
        if auto_paste and not permissions.check_accessibility_permission():
            permissions.request_accessibility_permission()

        self.state_manager.update_transcription_mode(auto_paste)
        self._set_menu()

    def _select_model(self, model_key: str):
        try:
            success = self.state_manager.request_model_change(model_key)

            if success:
                self.config_manager.update_user_setting('whisper', 'model', model_key)
                self._set_menu()
            else:
                self.logger.warning(f"Request to change model to {model_key} was not accepted")

        except Exception as e:
            self.logger.error(f"Error selecting model {model_key}: {e}")

    def _select_audio_device(self, device_id: int, device_name: str):
        success = self.state_manager.request_audio_device_change(device_id, device_name)

        if success:
            self.config_manager.update_user_setting('audio', 'input_device', device_id)
            self._set_menu()
        else:
            self.logger.warning(f"Request to change audio device to {device_id} was not accepted")

    def _show_console(self, icon=None, item=None):
        console.show()

    def apply_console_settings(self):
        if not console.owns_console() or not self.available:
            return
        if self.console_config.get('start_hidden', False):
            console.hide()
        console.start_minimize_monitor(console.hide)

    def _quit_application_from_tray(self, icon=None, item=None):
        os.kill(os.getpid(), signal.SIGINT)

    # Reached from the dashboard's About page ("Restart"), not the tray menu.
    #
    # Relaunch, then quit this instance — the new one's single-instance guard
    # takes over the lock. The command comes from utils.build_relaunch_command,
    # the same builder autostart uses, so the two can't disagree.
    #
    # This used to be [sys.executable] + sys.argv, which broke every pip install
    # on Windows (issue #3): argv[0] is the console-script path, and pip ships
    # only a compiled whisper-local.exe stub there — no plain script for
    # python.exe to open. Extra CLI args are carried over so a restart keeps
    # flags like --test.
    def _restart_application_from_tray(self, icon=None, item=None):
        import subprocess
        import sys
        from .utils import build_relaunch_command
        try:
            command = build_relaunch_command() + sys.argv[1:]
            self.logger.info(f"Restarting via: {command}")
            subprocess.Popen(command)
        except Exception as e:
            # Only quit if the relaunch actually spawned — a failed restart
            # must not leave the user with no app at all.
            self.logger.error(f"Restart failed to relaunch: {e}")
            self.notify("Restart failed — please relaunch manually.")
            return
        os.kill(os.getpid(), signal.SIGINT)

    # Every mutation of the tray object goes through here.
    #
    # On macOS these reach AppKit, which must only be touched from the main
    # thread. Through macOS 26 doing otherwise merely logged a warning; macOS 27
    # made it a hard SIGTRAP that kills the process outright (issue #13). The
    # callers here are worker threads — the recording thread drives update_state()
    # and the level monitor rewrites the title every 150 ms — so without this the
    # app dies on the first hotkey press. SIGTRAP is not a Python exception, so
    # the try/except below is for ordinary failures only; it could never have
    # caught the crash.
    #
    # On Windows run_on_ui_thread() calls straight through, so this costs nothing.
    def _on_ui_thread(self, mutate, description: str):
        def guarded():
            try:
                mutate()
            except Exception as e:
                self.logger.debug(f"Tray {description} failed: {e}")
        try:
            platform_app.run_on_ui_thread(guarded)
        except Exception as e:
            self.logger.debug(f"Could not dispatch tray {description}: {e}")

    # Menus are BUILT off-thread (pure Python) and only ASSIGNED on the UI
    # thread, which is the part that reaches NSStatusItem.
    def _set_menu(self):
        if not self.icon:
            return
        menu = self._create_menu()
        self._on_ui_thread(lambda: setattr(self.icon, 'menu', menu), 'menu update')

    def notify(self, message: str, title: str = "Whisper Local"):
        if not TRAY_AVAILABLE or not self.is_running or not self.icon:
            return
        self._on_ui_thread(lambda: self.icon.notify(message, title), 'notify')

    def update_state(self, new_state: str):
        if not TRAY_AVAILABLE or not self.is_running:
            return

        self.current_state = new_state

        art = self.icons.get(new_state)
        if art is not None:
            self._on_ui_thread(lambda: setattr(self.icon, 'icon', art), 'icon art')
        self._set_menu()

        if new_state == "recording":
            self._start_level_monitor()
        else:
            self._stop_level_monitor()
            tooltip = self.tray_config.get('tooltip', 'Whisper Local')
            self._on_ui_thread(lambda: setattr(self.icon, 'title', tooltip), 'tooltip')

    def _start_level_monitor(self):
        import threading
        prev_thread = getattr(self, '_level_thread', None)
        if prev_thread and prev_thread.is_alive():
            prev_stop = getattr(self, '_level_stop', None)
            if prev_stop:
                prev_stop.set()
            prev_thread.join(timeout=0.3)
        self._level_stop = threading.Event()

        def loop():
            while not self._level_stop.is_set():
                try:
                    level = self.state_manager.audio_recorder.get_current_level()
                except Exception:
                    level = 0.0
                bars = self._level_bars(level)
                if self.icon:
                    title = f"Whisper Local · 🎤 {bars}"
                    self._on_ui_thread(
                        lambda t=title: setattr(self.icon, 'title', t), 'level title')
                self._level_stop.wait(0.15)

        self._level_thread = threading.Thread(target=loop, daemon=True, name="tray-level")
        self._level_thread.start()

    def _stop_level_monitor(self):
        stop = getattr(self, '_level_stop', None)
        if stop:
            stop.set()

    def _level_bars(self, level: float) -> str:
        bins = ['░░░░░░', '█░░░░░', '██░░░░', '███░░░', '████░░', '█████░', '██████']
        scaled = min(int(level * 60), len(bins) - 1)
        return bins[scaled]

    def refresh_menu(self):
        self._set_menu()
    
    def start(self):
        if not self.available:
            return False

        if self.is_running:
            self.logger.warning("System tray is already running")
            return True

        try:
            idle_icon = self.icons.get("idle")
            menu = self._create_menu()

            self.icon = pystray.Icon(
                name="whisper-local",
                icon=idle_icon,
                title="Whisper Local",
                menu=menu
            )

            self.icon.run_detached()

            self.is_running = True
            print("   ✓ System tray icon is running...")

            return True

        except Exception as e:
            self.logger.error(f"Failed to start system tray: {e}")
            return False
    
    def stop(self):
        if not self.is_running:
            return

        try:
            self.icon.stop()
            self.is_running = False

        except Exception as e:
            self.logger.error(f"Error stopping system tray: {e}")
