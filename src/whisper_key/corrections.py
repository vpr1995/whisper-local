# corrections.py
# Persistence for post-transcription corrections (postprocess.replacements) —
# the backing store for "Fix this everywhere..." and the dashboard's Replacements
# card. Also persists postprocess.snippets for the Snippets card.
# Writes user_settings.yaml directly (same pattern as dictionary.py) so callers
# don't need a live ConfigManager; the running app hot-reloads the postprocess
# section on file change, so a correction takes effect on the next dictation.

import logging
from pathlib import Path
from typing import List

from ruamel.yaml import YAML

from .utils import get_user_app_data_path

logger = logging.getLogger(__name__)
USER_SETTINGS = "user_settings.yaml"


def _settings_path() -> Path:
    return Path(get_user_app_data_path()) / USER_SETTINGS


# Read the current correction list from user_settings.yaml (empty if unset).
def list_replacements() -> List[dict]:
    path = _settings_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding='utf-8') as f:
            data = YAML().load(f) or {}
        items = (data.get('postprocess') or {}).get('replacements') or []
        return [dict(i) for i in items if isinstance(i, dict)]
    except Exception as e:
        logger.warning(f"Could not read replacements: {e}")
        return []


# Append a correction (from_text → to_text). Returns False for empty input, a
# no-op fix (from == to), or an existing identical rule; True once persisted.
def add_replacement(from_text: str, to_text: str,
                    whole_word: bool = True, case_sensitive: bool = False) -> bool:
    from_text = (from_text or '').strip()
    to_text = (to_text or '')
    if not from_text or from_text == to_text:
        return False

    path = _settings_path()
    yaml = YAML()
    if path.exists():
        with open(path, encoding='utf-8') as f:
            data = yaml.load(f) or {}
    else:
        data = {}

    postprocess = data.setdefault('postprocess', {})
    current = list(postprocess.get('replacements') or [])

    # Skip an exact duplicate, but allow updating the target for an existing
    # "from" by replacing that entry rather than stacking a second. Match the
    # "from" case-INsensitively (matching is case-insensitive by default), so
    # adding "teh" when "Teh" already exists updates it instead of creating a
    # second rule that double-fires on the same text.
    for existing in current:
        if isinstance(existing, dict) and str(existing.get('from', '')).lower() == from_text.lower():
            if str(existing.get('to', '')) == to_text:
                return False
            existing['from'] = from_text
            existing['to'] = to_text
            _write(path, yaml, data)
            return True

    entry = {'from': from_text, 'to': to_text}
    if not whole_word:
        entry['whole_word'] = False
    if case_sensitive:
        entry['case_sensitive'] = True
    current.append(entry)
    postprocess['replacements'] = current
    _write(path, yaml, data)
    return True


# Delete a correction by its "from" text (case-insensitive, like add_replacement).
# Returns True when a rule was actually removed.
def remove_replacement(from_text: str) -> bool:
    return _remove_from_postprocess_list('replacements', 'from', from_text)


# ── Snippets (postprocess.snippets) ─────────────────────────────────────────
# Same user_settings.yaml round-trip as corrections, so the dashboard's
# Vocabulary page can manage both from one place.

def list_snippets() -> List[dict]:
    path = _settings_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding='utf-8') as f:
            data = YAML().load(f) or {}
        items = (data.get('postprocess') or {}).get('snippets') or []
        return [dict(i) for i in items if isinstance(i, dict)]
    except Exception as e:
        logger.warning(f"Could not read snippets: {e}")
        return []


# Add a snippet, or update the expansion/mode of an existing trigger (matched
# case-insensitively, as snippets.py matches). False for an empty trigger/expansion.
def save_snippet(trigger: str, expansion: str, mode: str = 'anywhere') -> bool:
    trigger = ' '.join((trigger or '').split())
    if not trigger or not (expansion or '').strip():
        return False
    mode = 'alone' if mode == 'alone' else 'anywhere'

    path = _settings_path()
    yaml = YAML()
    data = {}
    if path.exists():
        with open(path, encoding='utf-8') as f:
            data = yaml.load(f) or {}
    postprocess = data.setdefault('postprocess', {})
    current = list(postprocess.get('snippets') or [])

    for existing in current:
        if isinstance(existing, dict) and str(existing.get('trigger', '')).lower() == trigger.lower():
            if existing.get('expansion') == expansion and existing.get('mode', 'anywhere') == mode:
                return False
            existing['expansion'] = expansion
            existing['mode'] = mode
            break
    else:
        current.append({'trigger': trigger, 'expansion': expansion, 'mode': mode})
    postprocess['snippets'] = current
    _write(path, yaml, data)
    return True


def remove_snippet(trigger: str) -> bool:
    return _remove_from_postprocess_list('snippets', 'trigger', trigger)


# ── Shared persistence ──────────────────────────────────────────────────────

# Drops the first entry of postprocess.<list_key> whose `key_field` equals
# `value` (case-insensitive). Returns False when the file or entry is missing.
def _remove_from_postprocess_list(list_key: str, key_field: str, value: str) -> bool:
    value = (value or '').strip().lower()
    path = _settings_path()
    if not value or not path.exists():
        return False
    yaml = YAML()
    with open(path, encoding='utf-8') as f:
        data = yaml.load(f) or {}
    current = list((data.get('postprocess') or {}).get(list_key) or [])
    for index, existing in enumerate(current):
        if isinstance(existing, dict) and str(existing.get(key_field, '')).strip().lower() == value:
            del current[index]
            data['postprocess'][list_key] = current
            _write(path, yaml, data)
            return True
    return False


def _write(path: Path, yaml: YAML, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f)
    logger.info("Saved a post-transcription correction to user_settings.yaml")
