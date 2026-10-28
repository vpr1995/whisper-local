# settings_store.py
# Pure (no UI) helpers for reading form values into config types and writing them
# to user_settings.yaml. Used by the dashboard's Settings page; kept out of the
# web layer so the coercion rules stay testable without pywebview.

import logging
import os

logger = logging.getLogger(__name__)


# Settings whose values are genuinely numeric. ONLY these are int/float-coerced;
# everything else stays a string so free-text fields (initial_prompt, hotkeys,
# ollama model/endpoint) aren't silently turned into numbers when they happen to
# look numeric — e.g. initial_prompt "2024" must stay the string "2024".
_NUMERIC_PATHS = {
    'whisper.beam_size',
    'whisper.unload_after_minutes',
    'audio.max_duration',
    'audio.noise_suppression.strength',
    'vad.vad_silence_timeout_seconds',
    'postprocess.ollama.timeout',
    'audio.whisper_mode.max_gain',
    'hotkey.double_tap_window_ms',
    'history.retention_days',
    'stats.typing_wpm',
}


# Turn a raw form value (what the dashboard sent) into the Python type the
# setting is stored as, keyed by the setting's path so coercion is by declared
# type, not by guessing from the string contents.
def coerce_setting_value(raw, path):
    if path == 'whisper.languages':
        from .dictation_languages import normalize_languages
        return normalize_languages(raw)
    # A blank retention is "keep", which the config spells as null, not "".
    if path == 'history.retention_days' and str(raw).strip() == '':
        return None
    if path in _NUMERIC_PATHS:
        try:
            return int(raw)
        except (ValueError, TypeError):
            pass
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass
    return raw


# Persist ONE already-coerced value through ConfigManager. Two-level paths
# ("whisper.model") update directly; three-level paths ("postprocess.ai.polish")
# merge into a copy of the nested dict so sibling keys survive. Raises if the
# write fails; silently ignores paths that aren't in the config.
def write_setting(cm, path, value):
    parts = path.split('.')
    if len(parts) == 2:
        cm.update_user_setting(parts[0], parts[1], value)
    elif len(parts) == 3:
        nested = dict((cm.config.get(parts[0]) or {}).get(parts[1]) or {})
        nested[parts[2]] = value
        cm.update_user_setting(parts[0], parts[1], nested)


# Reset user settings to defaults while PRESERVING whisper.hotwords, which live
# inside user_settings.yaml. The reset dialog promises the dictionary survives, so
# we can't just delete the file.
def reset_settings_preserving_hotwords(settings_path) -> list:
    from ruamel.yaml import YAML
    preserved = []
    if os.path.exists(settings_path):
        try:
            with open(settings_path, encoding='utf-8') as f:
                existing = YAML().load(f) or {}
            preserved = list((existing.get('whisper') or {}).get('hotwords') or [])
        except Exception:
            preserved = []
        os.remove(settings_path)
    if preserved:
        with open(settings_path, 'w', encoding='utf-8') as f:
            YAML().dump({'whisper': {'hotwords': preserved}}, f)
    return preserved


# ── Settings owned by other dashboard pages ──────────────────────────────────
# Speech-engine settings (Models page) and AI settings (Style page) used to
# live on the Settings page. Their rules (options, ranges, coercion, restart) stay
# here so any page can read, validate and save them without the Settings catalogue.
#   rule keys: kind (select|number|text|toggle), options [(value, label)] for static
#   selects, min/max/integer/unit for numbers, required/url for text.

# The 15 languages the old tray menu offered, plus auto-detect.
LANGUAGE_CODES = ['auto', 'en', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'pl',
                  'ru', 'ja', 'zh', 'ko', 'hi', 'ar']

MODEL_SETTING_RULES = {
    'whisper.model': dict(kind='select'),  # options come from the registry: model_options(config)
    'whisper.device': dict(kind='select', options=[('cpu', 'Processor (CPU)'), ('cuda', 'Graphics card (GPU)')]),
    'whisper.unload_after_minutes': dict(kind='number', min=0, max=1440, integer=False, unit='min'),
    'whisper.backend': dict(kind='select', options=[('faster_whisper', 'faster-whisper'),
                                                    ('whisper_cpp', 'whisper.cpp'), ('parakeet', 'Parakeet')]),
    'whisper.compute_type': dict(kind='select', options=[('int8', 'int8'), ('float16', 'float16'),
                                                         ('float32', 'float32')]),
    'whisper.beam_size': dict(kind='number', min=1, max=10, integer=True),
    'whisper.initial_prompt': dict(kind='text'),
    'whisper.prompt_from_selection': dict(kind='toggle'),
    'whisper.task': dict(kind='select', options=[('transcribe', 'Transcribe'), ('translate', 'Translate to English')]),
    'whisper.language': dict(kind='select'),  # options: language_options()
    'whisper.languages': dict(kind='text'),   # "en, fr" in the UI, a list in the config
}

# AI provider choice, polish toggle and the Ollama server settings (Style page "AI" card).
AI_SETTING_RULES = {
    'postprocess.ai.provider': dict(kind='select', options=[('auto', 'Auto'), ('apple_intelligence', 'Apple Intelligence'),
                                                            ('ollama', 'Ollama')]),
    'postprocess.ai.polish': dict(kind='toggle'),
    'postprocess.ollama.model': dict(kind='text', required=True),
    'postprocess.ollama.endpoint': dict(kind='text', required=True, url=True),
    'postprocess.ollama.timeout': dict(kind='number', min=1, max=120, integer=False, unit='sec'),
}

SETTING_RULES = {**MODEL_SETTING_RULES, **AI_SETTING_RULES}

# Only postprocess.* is re-read per dictation; everything under whisper.* is loaded at startup.
HOT_RELOAD_PREFIXES = ('postprocess.', 'history.')


def setting_needs_restart(path) -> bool:
    return not path.startswith(HOT_RELOAD_PREFIXES)


# [{value, label}] for the language dropdown, "Auto-detect" first.
def language_options() -> list:
    from .dictation_languages import language_name
    return [{'value': code, 'label': 'Auto-detect' if code == 'auto' else language_name(code)}
            for code in LANGUAGE_CODES]


# Enabled models from the config registry, headed by "auto".
def model_options(config) -> list:
    models = (config.get('whisper') or {}).get('models') or {}
    options = [{'value': 'auto', 'label': 'Auto (recommended)'}]
    for key, info in models.items():
        info = info if isinstance(info, dict) else {}
        if info.get('enabled', True) is not False:
            options.append({'value': str(key), 'label': str(info.get('label') or key)})
    return options


# Reads a dotted path ("whisper.model") out of the nested config dict; None if absent.
def get_config_value(config, path):
    node = config
    for part in path.split('.'):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


# Dropdown options for a select path in SETTING_RULES. Always includes the stored
# value (a custom model, say), or the UI would show a choice that is not really set.
def setting_options(path, config) -> list:
    if path == 'whisper.model':
        options = model_options(config)
    elif path == 'whisper.language':
        options = language_options()
    else:
        options = [{'value': value, 'label': label} for value, label in SETTING_RULES[path]['options']]
    stored = get_config_value(config, path)
    if stored not in (None, '') and all(option['value'] != str(stored) for option in options):
        options.append({'value': str(stored), 'label': f'{stored} (current)'})
    return options


# The stored value as a form wants to show it: bool for toggles, number/None for
# numbers, "en, fr" for the languages list, plain string otherwise.
def display_setting_value(path, config):
    stored = get_config_value(config, path)
    if path == 'whisper.languages':
        from .dictation_languages import normalize_languages
        return ', '.join(normalize_languages(stored))
    kind = SETTING_RULES[path]['kind']
    if kind == 'toggle':
        return bool(stored)
    if kind == 'number':
        return stored
    return '' if stored is None else str(stored)


# Turns a raw form value into what gets stored, or raises ValueError with a message
# fit to show the user. `config` is only needed to check model names.
def validate_setting(path, raw, config=None):
    import re
    rule = SETTING_RULES.get(path)
    if rule is None:
        raise ValueError(f'Unknown setting: {path}')
    kind = rule['kind']
    if kind == 'toggle':
        if not isinstance(raw, bool):
            raise ValueError('Expected on or off.')
        return raw
    if kind == 'select':
        if str(raw) not in {option['value'] for option in setting_options(path, config or {})}:
            raise ValueError('Choose one of the listed options.')
        return str(raw)
    if kind == 'number':
        if isinstance(raw, bool) or raw is None or (isinstance(raw, str) and not raw.strip()):
            raise ValueError('Enter a number.')
        number = coerce_setting_value(raw, path)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or number != number:
            raise ValueError('Enter a number.')
        if rule['integer'] and number != int(number):
            raise ValueError('Enter a whole number.')
        number = int(number) if rule['integer'] else float(number)
        if number < rule['min'] or number > rule['max']:
            raise ValueError(f"Choose a value between {rule['min']} and {rule['max']}.")
        return number
    text = str(raw if raw is not None else '').strip()
    if path == 'whisper.languages':
        return coerce_setting_value(text, path)
    if rule.get('required') and not text:
        raise ValueError('This cannot be empty.')
    if rule.get('url') and not re.match(r'https?://\S+$', text):
        raise ValueError('Enter an address starting with http:// or https://.')
    return text


# Validate and persist one SETTING_RULES setting. Returns {ok, value, needs_restart,
# error}, the same shape the Settings page's save_setting returns.
def save_validated_setting(cm, path, raw) -> dict:
    try:
        stored = validate_setting(path, raw, cm.config)
        write_setting(cm, path, stored)
        return {'ok': True, 'error': None, 'needs_restart': setting_needs_restart(path),
                'value': display_setting_value(path, cm.config)}
    except ValueError as e:
        return {'ok': False, 'error': str(e), 'needs_restart': False}
