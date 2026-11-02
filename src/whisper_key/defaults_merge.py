# defaults_merge.py
# Layers the shipped commands.defaults.yaml under the user's commands.yaml, the
# same split config already uses (config.defaults.yaml ships the base,
# user_settings.yaml holds only overrides). The shipped entries always load from
# the package, so new ones reach every user on update; the user file carries
# just their own entries plus per-id overrides.

import logging
from pathlib import Path

from ruamel.yaml import YAML

from .utils import resolve_asset_path

logger = logging.getLogger(__name__)

# Set on a user entry to drop the shipped entry it names, e.g.
#   - trigger: "select all"
#     disabled: true
DISABLED_KEY = 'disabled'


# ── Reading ──────────────────────────────────────────────────────────────────

# Returns None when the file exists but doesn't parse, which the caller must
# tell apart from an empty file: a typo in the user's YAML must never look like
# "no entries".
def _read_yaml(path: Path):
    try:
        with open(path, encoding='utf-8') as f:
            return YAML().load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"Failed to parse {path}: {e}")
        return None


def _read_shipped(defaults_filename: str, section: str):
    path = Path(resolve_asset_path(defaults_filename))
    if not path.exists():
        logger.error(f"Shipped defaults missing: {path}")
        return None
    data = _read_yaml(path)
    if data is None:                      # shipped file itself is corrupt
        return None
    return data.get(section)


# ── Merging ──────────────────────────────────────────────────────────────────

# The user's own entries go in front of the shipped ones, so a command someone
# wrote themselves always beats a shipped one. A user entry that names a shipped
# entry by `id_key` overrides only the keys it sets, or drops it with `disabled`.
def _merge_lists(shipped: list, user: list, id_key: str) -> list:
    shipped_ids = {str(e[id_key]) for e in shipped
                   if isinstance(e, dict) and e.get(id_key) is not None}

    by_id = {}
    for entry in user:
        if isinstance(entry, dict) and str(entry.get(id_key, '')) in shipped_ids:
            by_id[str(entry[id_key])] = entry

    # Anything that doesn't name a shipped entry is the user's own.
    merged = [e for e in user
              if isinstance(e, dict) and str(e.get(id_key, '')) not in shipped_ids]

    for entry in shipped:
        if not isinstance(entry, dict):
            continue
        override = by_id.get(str(entry.get(id_key, '')))
        if override is None:
            merged.append(dict(entry))
            continue
        if override.get(DISABLED_KEY):
            continue
        combined = dict(entry)
        combined.update({k: v for k, v in override.items() if k != DISABLED_KEY})
        merged.append(combined)
    return merged


# Writes a fresh user file: the explanatory header plus an empty section.
def _write_starter_file(path: Path, header: str, section: str):
    yaml = YAML()
    yaml.default_flow_style = False
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8', newline='') as f:
        f.write(header)
        yaml.dump({section: []}, f)
    tmp.replace(path)


# ── Public entry point ───────────────────────────────────────────────────────

# Loads one list section, layering the user's file over the shipped defaults,
# and creates the user file (with its header) when it does not exist yet.
# `id_key` is the field that names a shipped entry (`trigger` for commands).
# Returns (merged, user_data) so the caller can still read its own top-level keys.
def load_layered(defaults_filename: str, user_path: Path, section: str,
                 header: str, id_key: str = 'id'):
    shipped = _read_shipped(defaults_filename, section)
    if shipped is None:
        # No package defaults (a broken install): fall back to the user file
        # alone rather than losing their entries.
        data = _read_yaml(user_path) or {}
        return list(data.get(section) or []), data

    if not user_path.exists():
        _write_starter_file(user_path, header, section)
        return [dict(e) for e in shipped], {}

    data = _read_yaml(user_path)
    if data is None:
        # Their YAML is broken. Run on the shipped entries so the app still
        # works, and don't rewrite the file they're in the middle of editing.
        logger.error(f"{user_path.name} could not be parsed, so only the shipped "
                     f"entries are in effect. Your file has been left as it is.")
        return [dict(e) for e in shipped], {}

    user_section = data.get(section)
    return _merge_lists(shipped, user_section if isinstance(user_section, list) else [], id_key), data
