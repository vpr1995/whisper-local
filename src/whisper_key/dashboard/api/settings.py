# dashboard/api/settings.py
# Python API for the dashboard's Settings page. The page is data-driven: this module
# owns the catalogue of settings (labels, captions, controls, restart rules, which
# top-level card or drill-in view each belongs to) and get_settings_schema() hands it
# to the UI. save_setting() validates, coerces and persists ONE setting at a time
# (instant apply, no Save button), reusing settings_store.py.

import logging
import os
import re

# HOT_RELOAD_PREFIXES: only those keys are re-read by the running app on the next
# dictation; every other setting is read at startup and needs an app restart.
from ...settings_store import HOT_RELOAD_PREFIXES, get_config_value as _dig

logger = logging.getLogger(__name__)

# Pseudo-setting handled by autostart.py rather than user_settings.yaml.
START_ON_LOGIN_PATH = 'app.start_on_login'

HOTKEY_PATHS = (
    'hotkey.recording_hotkey', 'hotkey.stop_key', 'hotkey.auto_send_key', 'hotkey.stop_polish_key',
    'hotkey.cancel_combination', 'hotkey.command_hotkey', 'hotkey.rephrase_hotkey',
    'hotkey.pause_hotkey', 'hotkey.paste_last_hotkey',
)
# Hotkeys the app cannot run without; the rest may be left blank to switch them off.
REQUIRED_HOTKEY_PATHS = ('hotkey.recording_hotkey', 'hotkey.stop_key')

OVERLAY_POSITIONS = ['bottom-center', 'bottom-right', 'bottom-left',
                     'top-center', 'top-right', 'top-left']


# ── Row builders ─────────────────────────────────────────────────────────────

def _options(*pairs):
    return [{'value': value, 'label': label} for value, label in pairs]


# One catalogue entry. `restart` defaults to "derived from the key path"; `control`
# is toggle | select | number | text | hotkey. Extra keyword args (min, max, integer,
# nullable, unit, placeholder, optional, enabled_by) ride along to the UI/validator.
def _row(path, label, caption, control, options=None, restart=None, **extra):
    if restart is None:
        restart = not path.startswith(HOT_RELOAD_PREFIXES)
    return dict(path=path, label=label, caption=caption, control=control,
                options=options, needs_restart=restart, **extra)


def _toggle(path, label, caption, **extra):
    return _row(path, label, caption, 'toggle', **extra)


def _select(path, label, caption, options, **extra):
    return _row(path, label, caption, 'select', options=options, **extra)


def _number(path, label, caption, minimum, maximum, integer=False, **extra):
    return _row(path, label, caption, 'number', min=minimum, max=maximum, integer=integer, **extra)


def _text(path, label, caption, **extra):
    return _row(path, label, caption, 'text', **extra)


def _hotkey(path, label, caption):
    return _row(path, label, caption, 'hotkey', optional=path not in REQUIRED_HOTKEY_PATHS)


# ── Dynamic option lists ─────────────────────────────────────────────────────

# "System default" plus every input device. Failure to enumerate (no PortAudio,
# headless CI) just leaves the default entry so the page still renders.
def _microphone_options(config: dict) -> list:
    pairs = [('default', 'System default')]
    try:
        from ...audio_recorder import AudioRecorder
        configured_host = (config.get('audio') or {}).get('host')
        devices = AudioRecorder.get_available_audio_devices(configured_host)
        several_hosts = len({device['hostapi'] for device in devices}) > 1
        for device in devices:
            suffix = f" ({device['hostapi']})" if several_hosts else ''
            pairs.append((str(device['id']), f"{device['name']}{suffix}"))
    except Exception as e:
        logger.debug(f"Could not list microphones: {e}")
    return _options(*pairs)


# ── The catalogue ────────────────────────────────────────────────────────────

# The Settings page has a short top level and four drill-in views. Speech-engine
# settings (model, device, language...) live on the Models page and AI polish on
# the Style page; their rules are in settings_store.py.
MAIN_VIEW = 'main'
VIEW_TITLES = {
    'more-shortcuts': 'More shortcuts',
    'recording': 'Recording options',
    'formatting': 'Formatting',
    'app-more': 'More app settings',
}


# A card of rows. `view` is MAIN_VIEW or a key of VIEW_TITLES; `more` is the link row
# at the bottom of a top-level card that opens a drill-in view.
def _section(section_id, title, view, rows, more_label=None, more_view=None):
    more = dict(label=more_label, view=more_view) if more_label else None
    return dict(id=section_id, title=title, view=view, rows=rows, more=more)


# Sections in display order. Built per call because the microphone list depends on
# the live hardware.
def _build_catalogue(config: dict) -> list:
    from ...platform import IS_MACOS, IS_WINDOWS
    from ... import autostart

    mac_note = ' (Fn is the Globe key)' if IS_MACOS else ''

    # ── Top level ──
    app_rows = []
    if autostart.is_supported():
        app_rows.append(_toggle(START_ON_LOGIN_PATH, 'Start on login',
                                'Open Whisper Local in the background when you sign in.', restart=False))
    app_rows += [
        _toggle('overlay.enabled', 'Show recording pill',
                'A small floating pill with a level meter appears while you record.'),
        _select('overlay.position', 'Pill position', 'Which screen edge the pill sits on.',
                _options(*[(name, name.replace('-', ' ').capitalize()) for name in OVERLAY_POSITIONS]),
                enabled_by='overlay.enabled'),
        _toggle('audio_feedback.enabled', 'Sounds',
                'Play a short sound when recording starts and stops.'),
        _toggle('voice_commands.enabled', 'Voice commands',
                'Let the command shortcut run your own spoken commands (commands.yaml).'),
        _toggle('update_check.enabled', 'Check for updates',
                'Look for a new release once a day. Nothing is downloaded without you.'),
    ]

    interface_rows = [
        _toggle('overlay.click_through', 'Pill ignores clicks', 'Lets clicks pass through the pill to the app underneath.'),
        _toggle('system_tray.enabled', 'Show tray icon', 'The menu bar or system tray icon.'),
    ]
    if IS_WINDOWS:
        interface_rows.append(_toggle('console.start_hidden', 'Start with console hidden',
                                      'Only for the hideable build: hide the console window at launch.'))

    top_level = [
        _section('shortcuts', 'Shortcuts', MAIN_VIEW, [
            _hotkey('hotkey.recording_hotkey', 'Dictate', 'The keys that start recording in any app' + mac_note + '.'),
            _select('hotkey.recording_mode', 'How it works',
                    'Hold the shortcut while you talk, or tap once to start and again to stop.',
                    _options(('push_to_talk', 'Hold to talk'), ('toggle', 'Tap to start and stop'))),
            _toggle('hotkey.double_tap_to_lock', 'Hands-free lock',
                    'Double-tap the shortcut to keep recording without holding it. Hold to talk only.'),
        ], 'More shortcuts', 'more-shortcuts'),
        _section('microphone', 'Microphone', MAIN_VIEW, [
            _select('audio.input_device', 'Microphone',
                    'Which microphone to record from. System default follows your system setting.',
                    _microphone_options(config)),
        ], 'Recording options', 'recording'),
        _section('after-you-speak', 'After you speak', MAIN_VIEW, [
            _toggle('clipboard.auto_paste', 'Paste automatically',
                    'Type the text at your cursor. Off copies it to the clipboard only. Per-app rules can override this.'),
            _toggle('clipboard.paste_preserve_clipboard', 'Keep my clipboard',
                    'Puts back whatever you had copied once the text is pasted.'),
        ], 'Formatting', 'formatting'),
        _section('app', 'App', MAIN_VIEW, app_rows, 'More', 'app-more'),
    ]

    # ── Drill-in views ──
    more_shortcuts = [
        _section('stop-shortcuts', 'While recording', 'more-shortcuts', [
            _hotkey('hotkey.stop_key', 'Stop', 'Ends the recording and delivers your text.'),
            _hotkey('hotkey.auto_send_key', 'Stop and press Enter',
                    'Stops, pastes, then presses Enter for you. Handy in chat apps. Blank turns it off.'),
            _hotkey('hotkey.stop_polish_key', 'Stop and polish',
                    'Stops and polishes this one dictation with the AI, even where polish is off. Blank turns it off.'),
            _hotkey('hotkey.cancel_combination', 'Cancel', 'Throws the recording away without typing anything.'),
        ]),
        _section('other-shortcuts', 'Other shortcuts', 'more-shortcuts', [
            _hotkey('hotkey.command_hotkey', 'Voice command', 'Say a command instead of dictating text. Blank turns it off.'),
            _hotkey('hotkey.rephrase_hotkey', 'Rephrase selection',
                    'Select text, hold this, and say how to change it, such as "make this shorter". Uses the AI set on the Style page.'),
            _hotkey('hotkey.paste_last_hotkey', 'Paste last dictation',
                    'Pastes your latest dictation again, for when it landed in the wrong window.'),
            _hotkey('hotkey.pause_hotkey', 'Pause shortcuts',
                    'Switches every Whisper Local shortcut off and on, so another app can use the keys.'),
        ]),
        _section('hands-free-timing', 'Hands-free lock', 'more-shortcuts', [
            _number('hotkey.double_tap_window_ms', 'Double-tap speed', 'How quickly the second tap must follow the first.',
                    100, 2000, integer=True, unit='ms'),
        ]),
    ]

    recording = [
        _section('sound', 'Sound', 'recording', [
            _toggle('audio.noise_suppression.enabled', 'Reduce background noise',
                    'Cleans up hiss and hum before transcribing. Needs the noisereduce package.'),
            _number('audio.noise_suppression.strength', 'Noise reduction strength', '0 is gentle, 1 is aggressive.',
                    0.0, 1.0, enabled_by='audio.noise_suppression.enabled'),
            _toggle('audio.whisper_mode.enabled', 'Whisper mode', 'Boosts quiet speech so you can dictate softly.'),
            _number('audio.whisper_mode.max_gain', 'Whisper boost', 'The most a quiet recording is amplified.',
                    1.0, 20.0, unit='x', enabled_by='audio.whisper_mode.enabled'),
            _toggle('audio.pause_media_on_record', 'Pause music while recording',
                    'Pauses the media player when recording starts.'),
        ]),
        _section('stopping', 'Stopping', 'recording', [
            _toggle('vad.vad_realtime_enabled', 'Stop when silent', 'Ends the recording on its own after you go quiet.'),
            _number('vad.vad_silence_timeout_seconds', 'Silence wait', 'How long to wait in silence before stopping.',
                    1.0, 36000.0, unit='sec', enabled_by='vad.vad_realtime_enabled'),
            _number('audio.max_duration', 'Longest recording', 'A recording stops by itself after this long. 0 means no limit.',
                    0, 86400, integer=True, unit='sec'),
            _toggle('audio.continuous_mode', 'Keep listening',
                    'Starts a new recording right after each one is delivered.'),
        ]),
        _section('speech-detection', 'Speech detection', 'recording', [
            _toggle('audio.mic_always_on', 'Keep microphone open',
                    'Catches the first word you say. Turn off to stop the system mic indicator staying on.'),
            _toggle('vad.vad_precheck_enabled', 'Skip silent recordings',
                    'Do not transcribe a recording that contains no speech.'),
            _number('vad.vad_onset_threshold', 'Speech start threshold',
                    'How sure the detector must be that you started talking (0 to 1).', 0.0, 1.0),
            _number('vad.vad_offset_threshold', 'Speech end threshold',
                    'How sure it must be that you stopped (0 to 1).', 0.0, 1.0),
            _number('vad.vad_min_speech_duration', 'Shortest speech', 'Ignore sounds shorter than this.',
                    0.001, 5.0, unit='sec'),
        ]),
    ]

    formatting = [
        _section('punctuation', 'Punctuation and capitals', 'formatting', [
            _toggle('postprocess.capitalize_first', 'Capitalize the first letter', 'Starts each dictation with a capital.'),
            _toggle('postprocess.ensure_punctuation', 'End with punctuation', 'Adds a full stop if the sentence has none.'),
            _toggle('postprocess.strip_trailing_period', 'Remove the final period',
                    'Drops the period at the very end, which suits chat and search boxes.'),
            _toggle('postprocess.inline_formatting', 'Say punctuation',
                    'Say "comma", "period" or "new line" and get the symbol.'),
            _toggle('postprocess.inline_formatting_absorb_punctuation', 'Merge doubled punctuation',
                    'Fixes "hello,, world" when you also say the punctuation out loud.',
                    enabled_by='postprocess.inline_formatting'),
            _toggle('postprocess.inline_formatting_extend', 'Keep English cue words',
                    'Keep "comma" and friends working when you add your own spoken cues.',
                    enabled_by='postprocess.inline_formatting'),
            _toggle('postprocess.list_formatting', 'Spoken lists',
                    'Say "first, second, third" and get a numbered list.'),
        ]),
        _section('delivery', 'Delivery', 'formatting', [
            _select('clipboard.delivery_method', 'Delivery method',
                    'Paste puts text through the clipboard. Type simulates key presses (Windows only).',
                    _options(('paste', 'Paste'), ('type', 'Type'))),
            _text('clipboard.paste_hotkey', 'Paste shortcut', 'The shortcut Whisper Local presses to paste into the app.'),
            _toggle('clipboard.type_also_copy_to_clipboard', 'Also copy typed text',
                    'When typing instead of pasting, leave a copy on the clipboard too.'),
            _toggle('streaming.deliver_to_cursor', 'Type live text at the cursor (experimental)',
                    'Types phrases as you speak and skips the final pass. Less accurate. Needs live preview.',
                    enabled_by='streaming.streaming_enabled'),
        ]),
    ]

    app_more = [
        _section('interface', 'Interface', 'app-more', interface_rows),
        _section('more-sounds', 'Sounds', 'app-more', [
            _toggle('audio_feedback.ready_enabled', 'Sound when ready', 'Play a sound once the model has finished loading.'),
            _toggle('audio_feedback.transcription_complete_enabled', 'Sound when text is ready',
                    'Play a sound each time a transcription finishes.'),
        ]),
        _section('history-stats', 'History and stats', 'app-more', [
            _number('history.retention_days', 'Keep history for',
                    'Blank keeps your newest 2000 dictations. 0 keeps none. 7 keeps a week.',
                    0, 36500, integer=True, nullable=True, unit='days', placeholder='Forever'),
            _number('stats.typing_wpm', 'Your typing speed', 'Used to estimate how much time dictation saves you.',
                    1, 300, integer=True, unit='wpm'),
        ]),
        _section('logs', 'Logs', 'app-more', [
            _select('logging.level', 'Log detail', 'How much the log file records. Use DEBUG when reporting a bug.',
                    _options(*[(level, level.capitalize()) for level in ('DEBUG', 'INFO', 'WARNING', 'ERROR')])),
            _toggle('logging.log_transcriptions', 'Log transcribed text',
                    'Writes what you say into the log file. Off keeps your words private.'),
            _toggle('audit.enabled', 'Delivery audit log',
                    'Keeps a private record of when text was delivered, without the text.'),
        ]),
        _section('experimental', 'Experimental', 'app-more', [
            _toggle('streaming.streaming_enabled', 'Live preview (experimental)',
                    'Show words in the pill while you are still speaking. Downloads a small model on first use.'),
        ]),
    ]

    return top_level + more_shortcuts + recording + formatting + app_more


# ── Reading values ───────────────────────────────────────────────────────────

# The stored value as the UI wants to show it (JSON-friendly, select values as strings).
def _display_value(row: dict, config: dict):
    path = row['path']
    if path == START_ON_LOGIN_PATH:
        from ... import autostart
        return bool(autostart.is_enabled())
    stored = _dig(config, path)
    if row['control'] == 'toggle':
        return bool(stored)
    if row['control'] == 'number':
        return stored  # None (blank) allowed for nullable numbers
    if stored is None:
        return ''
    return str(stored)


# A select must always be able to show the stored value, even one that is not in
# our list (a custom model, an unplugged microphone), or the UI would lie about it.
def _ensure_current_option(row: dict, value) -> None:
    if row['control'] != 'select' or value in ('', None):
        return
    if all(option['value'] != value for option in row['options']):
        row['options'].append({'value': value, 'label': f'{value} (current)'})


# ── Validation ───────────────────────────────────────────────────────────────

# Names accepted in a hotkey on this OS. macOS reads the table its own listener
# uses; Windows asks global-hotkeys. Returns None when no table is available, in
# which case only the shape of the combo is checked.
def _known_key_names():
    from ...platform import IS_MACOS
    try:
        if IS_MACOS:
            from ...platform.macos.hotkeys import MODIFIER_FLAGS
            from ...platform.macos.keycodes import KEY_CODES
            return set(MODIFIER_FLAGS) | set(KEY_CODES) | {'win', 'window', 'windows', 'super'}
        from global_hotkeys.keycodes import vk_key_names
        from ...platform.windows.hotkeys import KEY_MAP
        return set(vk_key_names) | set(KEY_MAP)
    except Exception as e:
        logger.debug(f"No key-name table to validate hotkeys against: {e}")
        return None


# Parts of a combo like "ctrl+option+space" as a lowercase list, or ValueError.
def _parse_combo(text: str) -> list:
    parts = [part.strip().lower() for part in text.split('+')]
    if any(not part for part in parts):
        raise ValueError('Use keys joined with "+", like ctrl+shift+space.')
    if len(set(parts)) != len(parts):
        raise ValueError('A key appears twice in that shortcut.')
    return parts


# Checks a hotkey the same way the app's listener will read it, and refuses a combo
# another shortcut already uses (the app would silently disable one of them).
def _validate_hotkey(row: dict, raw, config: dict) -> str:
    text = str(raw or '').strip()
    if not text:
        if row.get('optional'):
            return ''
        raise ValueError('This shortcut is required.')
    parts = _parse_combo(text)
    known = _known_key_names()
    if known is not None:
        unknown = [part for part in parts if part not in known and not re.fullmatch(r'0x[0-9a-f]+|\d+', part)]
        if unknown:
            raise ValueError(f'Unknown key: {unknown[0]}.')
    from ...platform import IS_MACOS
    if IS_MACOS:
        from ...platform.macos.hotkeys import MODIFIER_FLAGS
        plain_keys = [part for part in parts if part not in MODIFIER_FLAGS and part not in ('win', 'window', 'windows', 'super')]
        if len(plain_keys) > 1:
            raise ValueError('Use at most one regular key, plus modifiers like ctrl or option.')

    wanted = frozenset(parts)
    for other_path in HOTKEY_PATHS:
        other_value = _dig(config, other_path)
        if other_path != row['path'] and other_value and frozenset(_parse_combo(str(other_value))) == wanted:
            raise ValueError('That shortcut is already used by another action.')
    return '+'.join(parts)


def _parse_number(text: str):
    for convert in (int, float):
        try:
            return convert(text.strip())
        except ValueError:
            continue
    raise ValueError('Enter a number.')


# A number from the form: blank only where the setting allows "off", whole numbers
# where the setting is a count, and always inside the declared range.
def _validate_number(row: dict, raw):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if row.get('nullable'):
            return None
        raise ValueError('Enter a number.')
    from ...settings_store import coerce_setting_value
    number = coerce_setting_value(raw, row['path'])
    if isinstance(number, str):
        # Paths outside settings_store's numeric list come back unconverted.
        number = _parse_number(number)
    if isinstance(number, bool) or not isinstance(number, (int, float)) or number != number:
        raise ValueError('Enter a number.')
    if row['integer']:
        if number != int(number):
            raise ValueError('Enter a whole number.')
        number = int(number)
    else:
        number = float(number)
    if number < row['min'] or number > row['max']:
        raise ValueError(f"Choose a value between {row['min']} and {row['max']}.")
    return number


def _validate_microphone(row: dict, raw):
    text = str(raw)
    if text == 'default':
        return 'default'
    if not text.isdigit() or not any(option['value'] == text for option in row['options']):
        raise ValueError('That microphone is not available right now.')
    return int(text)


# Turns the raw value from the UI into what gets stored, or raises ValueError with
# a message fit to show the user.
def _validate_and_coerce(row: dict, raw, config: dict):
    control = row['control']
    if control == 'toggle':
        if not isinstance(raw, bool):
            raise ValueError('Expected on or off.')
        return raw
    if control == 'hotkey':
        return _validate_hotkey(row, raw, config)
    if control == 'number':
        return _validate_number(row, raw)
    if control == 'select':
        if row['path'] == 'audio.input_device':
            return _validate_microphone(row, raw)
        if str(raw) not in {option['value'] for option in row['options']}:
            raise ValueError('Choose one of the listed options.')
        return str(raw)
    # text
    return str(raw if raw is not None else '').strip()


class SettingsApi:
    # ── Reading ──────────────────────────────────────────────────────────────

    # Every setting the page shows: {views: {id: title}, sections: [{id, title, view, more, rows}]}.
    # A row is {path, label, caption, control, options, value, needs_restart, ...}.
    def get_settings_schema(self) -> dict:
        config = self._load_config().config
        sections = _build_catalogue(config)
        for section in sections:
            for row in section['rows']:
                row['value'] = _display_value(row, config)
                _ensure_current_option(row, row['value'])
        return {'views': VIEW_TITLES, 'sections': sections}

    # ── Saving ───────────────────────────────────────────────────────────────

    # Validates and persists one setting immediately. Returns
    # {ok, needs_restart, value, error}; `value` is the normalised value to show
    # (e.g. "EN, fr" comes back as "en, fr").
    def save_setting(self, path: str, value) -> dict:
        try:
            config_manager = self._load_config()
            row = next((row for section in _build_catalogue(config_manager.config)
                        for row in section['rows'] if row['path'] == path), None)
            if row is None:
                return {'ok': False, 'needs_restart': False, 'error': f'Unknown setting: {path}'}
            current = _dig(config_manager.config, path)
            if current is not None:
                _ensure_current_option(row, str(current))

            stored = _validate_and_coerce(row, value, config_manager.config)
            if path == START_ON_LOGIN_PATH:
                self._apply_autostart(stored)
            else:
                from ...settings_store import write_setting
                write_setting(config_manager, path, stored)

            # Echo what is now on disk, so the UI shows normalised values.
            return {'ok': True, 'needs_restart': row['needs_restart'], 'error': None,
                    'value': _display_value(row, self._load_config().config)}
        except ValueError as e:
            return {'ok': False, 'needs_restart': False, 'error': str(e)}
        except Exception as e:
            logger.error(f"Saving {path} failed: {e}")
            return {'ok': False, 'needs_restart': False, 'error': f'Could not save: {e}'}

    # Registers or removes the login item; the OS call reports failure as False.
    @staticmethod
    def _apply_autostart(enabled: bool) -> None:
        from ... import autostart
        succeeded = autostart.enable() if enabled else autostart.disable()
        if not succeeded:
            raise ValueError('Could not change the login item. Check the log for details.')

    # ── Whole-file actions ───────────────────────────────────────────────────

    # Deletes every override but keeps the user's dictionary (hotwords).
    def reset_settings(self) -> dict:
        from ...settings_store import reset_settings_preserving_hotwords
        try:
            reset_settings_preserving_hotwords(self._settings_path())
            return {'ok': True, 'needs_restart': True, 'error': None}
        except Exception as e:
            logger.error(f"Reset failed: {e}")
            return {'ok': False, 'needs_restart': False, 'error': str(e)}

    # Opens user_settings.yaml in the default editor, for people who prefer the file.
    def open_settings_file(self) -> bool:
        from ...utils import open_file
        self._load_config()  # creates the file on first run so there is something to open
        path = self._settings_path()
        if not os.path.exists(path):
            return False
        open_file(path)
        return True

    @staticmethod
    def _settings_path() -> str:
        from ...utils import get_user_app_data_path
        return os.path.join(get_user_app_data_path(), 'user_settings.yaml')
