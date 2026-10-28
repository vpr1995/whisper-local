# dashboard/api/models.py
# Python API for the Models page: lists every speech model with its download
# status, downloads a model on a background thread with pollable progress, opens
# the model cache folder, and reads/saves every whisper.* setting the page shows
# (model, language, device, idle unload, and the Advanced view). Saves go through
# settings_store.save_validated_setting. Stateless apart from the one in-flight
# download, which lives in DownloadTracker.

import logging
import os
import re
import threading

from ...settings_store import (
    MODEL_SETTING_RULES, display_setting_value, save_validated_setting, setting_options,
)

logger = logging.getLogger(__name__)


# ── Presentation data ────────────────────────────────────────────────────────

GROUP_TITLES = {
    'official': ("Whisper", "OpenAI's multilingual models, from fastest to most accurate."),
    'custom': ("English-only and distilled", "Smaller or faster variants of Whisper that only know English."),
    'parakeet': ("Parakeet", "NVIDIA's fast model for 25 European languages. Needs the Parakeet engine."),
}

# One line per model on speed/accuracy. Keys not listed get no blurb (custom models).
MODEL_BLURBS = {
    'tiny': "Fastest, least accurate. Fine for quick notes.",
    'base': "A good balance of speed and accuracy on most laptops.",
    'small': "Clearly more accurate than Base, still fast on a CPU.",
    'medium': "High accuracy, but slow without a GPU.",
    'large': "The most accurate Whisper (large-v3). Wants a GPU.",
    'large-v3-turbo': "Near-large accuracy at several times the speed. Best on a GPU.",
    'distil-small.en': "Distilled: smaller and faster than Whisper Small.",
    'distil-medium.en': "Distilled: smaller and faster than Whisper Medium.",
    'distil-large-v3.5': "About 1.5x faster than Large-V3-Turbo with similar accuracy.",
    'parakeet-v3': "About 3x faster than Whisper Small on a CPU. Picks the language itself; cannot translate.",
}
# Two or three words for the tile subtitle, next to the size. Models not listed fall
# back to their language ("English" for the .en ones).
MODEL_TAGLINES = {
    'tiny': "Fastest",
    'base': "Balanced",
    'small': "Accurate",
    'medium': "Very accurate",
    'large': "Most accurate",
    'large-v3-turbo': "Fast + accurate",
    'distil-large-v3.5': "Fast + accurate",
    'parakeet-v3': "Very fast",
}
ENGLISH_ONLY_BLURB = "English-only twin of the multilingual model: a little more accurate at the same size."

# Matches a size like "141MB" or "1.4GB" at the end of a label's parentheses.
SIZE_PATTERN = re.compile(r'^\d[\d.,]*\s*[KMG]B$', re.IGNORECASE)


# ── Pure helpers ─────────────────────────────────────────────────────────────

# "Tiny.En (English, 74MB)" -> ("Tiny.En", "English", "74MB"). The registry has no
# structured size or language fields, only this label, so the page reads them from it.
def parse_model_label(label: str, group: str) -> tuple:
    match = re.match(r'^(.*?)\s*\((.*)\)\s*$', label or '')
    if not match:
        return (label or ''), ('Multilingual' if group == 'official' else ''), ''
    name, inside = match.group(1), [part.strip() for part in match.group(2).split(',')]
    size = next((part for part in inside if SIZE_PATTERN.match(part)), '')
    language_parts = [part for part in inside if part and part != size]
    languages = ', '.join(language_parts) or ('Multilingual' if group == 'official' else '')
    return name, languages, size


# The blurb for a model key: curated text, else a generic line for ".en" models.
def model_blurb(model_key: str) -> str:
    if model_key in MODEL_BLURBS:
        return MODEL_BLURBS[model_key]
    return ENGLISH_ONLY_BLURB if model_key.endswith('.en') else ''


# (repo id, allow_patterns) to fetch for a model, or None when it can't be downloaded
# (a local folder). Mirrors what the engines fetch on first use.
def download_target(model) -> tuple:
    if model is None or model.is_local_path:
        return None
    if model.files:  # sherpa-onnx file set: the whole snapshot
        return model.source, None
    from faster_whisper.utils import _MODELS
    from ...whisper_engine import FASTER_WHISPER_FILES
    repo_id = _MODELS.get(model.source, model.source)
    if '/' not in repo_id or os.path.isdir(repo_id):
        return None
    return repo_id, FASTER_WHISPER_FILES


# ── Download tracking ────────────────────────────────────────────────────────

# State of the single in-flight (or last finished) model download, safe to read from
# the JS polling thread while the download thread writes it.
class DownloadTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = self._idle_state()

    @staticmethod
    def _idle_state() -> dict:
        return {'state': 'idle', 'key': None, 'label': '', 'done_bytes': 0, 'total_bytes': 0, 'error': ''}

    # Begins `run_download(on_progress)` on a daemon thread. False if one is already running.
    def start(self, model_key: str, label: str, run_download) -> bool:
        with self._lock:
            if self._state['state'] == 'running':
                return False
            self._state = dict(self._idle_state(), state='running', key=model_key, label=label)
        threading.Thread(target=self._run, args=(run_download,), daemon=True, name='dashboard-model-download').start()
        return True

    def _run(self, run_download):
        try:
            run_download(self._on_progress)
            self._update(state='done')
        except Exception as error:
            logger.error(f"Model download failed: {error}")
            self._update(state='error', error=str(error) or error.__class__.__name__)

    def _on_progress(self, done_bytes: int, total_bytes: int):
        self._update(done_bytes=done_bytes, total_bytes=total_bytes)

    def _update(self, **changes):
        with self._lock:
            self._state.update(changes)

    # A copy of the state plus a 0-100 percent (None while the total size is unknown).
    def snapshot(self) -> dict:
        with self._lock:
            state = dict(self._state)
        total = state['total_bytes']
        state['percent'] = min(100, int(state['done_bytes'] * 100 / total)) if total else None
        if state['state'] == 'done':
            state['percent'] = 100
        return state

    # Forgets a finished/failed download once the page has reported it.
    def acknowledge(self):
        with self._lock:
            if self._state['state'] in ('done', 'error'):
                self._state = self._idle_state()


_download_tracker = DownloadTracker()



class ModelsApi:
    # ── Helpers ──────────────────────────────────────────────────────────────

    # (whisper config, registry for the configured backend).
    def _load_model_registry(self):
        from ...engine_factory import resolve_backend
        from ...model_registry import ModelRegistry
        whisper_cfg = self._load_config().get_whisper_config()
        backend = whisper_cfg.get('backend', 'faster_whisper')
        # Parakeet models are listed whatever the backend: picking one switches the engine itself.
        registry = ModelRegistry(whisper_cfg.get('models', {}), {}, active_backend=backend)
        return whisper_cfg, registry, resolve_backend

    # "Parakeet V3" for a registry key; the key itself when the registry doesn't know it.
    @staticmethod
    def _model_display_name(registry, model_key: str) -> str:
        model = registry.get_model(model_key)
        return parse_model_label(model.label, model.group)[0] if model else model_key

    # ── Listing ──────────────────────────────────────────────────────────────

    # Everything the page draws: the auto pick for this computer, then models by group.
    def get_models(self) -> dict:
        from ...auto_model import AUTO, pick_model
        from ...engine_factory import missing_backend_dependency
        whisper_cfg, registry, resolve_backend = self._load_model_registry()
        configured = whisper_cfg.get('model', AUTO)
        backend, current_key = resolve_backend(whisper_cfg, registry)
        auto_key, auto_reason = pick_model(whisper_cfg)
        download = _download_tracker.snapshot()
        downloading_key = download['key'] if download['state'] == 'running' else None

        def describe(model) -> dict:
            name, languages, size = parse_model_label(model.label, model.group)
            missing = missing_backend_dependency('parakeet') if model.backend == 'parakeet' else None
            return {
                'key': model.key,
                'name': name,
                'languages': languages,
                'size': size,
                'tagline': MODEL_TAGLINES.get(model.key) or languages,
                'blurb': model_blurb(model.key),
                'cached': registry.is_model_cached(model.key),
                'downloadable': download_target(model) is not None,
                'selected': configured == model.key,
                'in_use': current_key == model.key,
                'auto_picked': configured == AUTO and current_key == model.key,
                'downloading': downloading_key == model.key,
                'warning': (f"{missing[0]}. Run: {missing[1]}" if missing else ''),
            }

        enabled_models = [m for m in registry.whisper_models.values() if m.enabled]
        group_ids = registry.get_groups_ordered()
        group_ids += sorted({m.group for m in enabled_models} - set(group_ids))
        groups = []
        for group_id in group_ids:
            members = [describe(m) for m in enabled_models if m.group == group_id]
            if members:
                title, caption = GROUP_TITLES.get(group_id, (group_id.title(), ''))
                groups.append({'id': group_id, 'title': title, 'caption': caption, 'models': members})

        return {
            'configured': configured,
            'auto_selected': configured == AUTO,
            'auto': {
                'key': auto_key,
                'name': self._model_display_name(registry, auto_key),
                'reason': auto_reason,
                'cached': registry.is_model_cached(auto_key),
                'downloadable': download_target(registry.get_model(auto_key)) is not None,
                'downloading': downloading_key == auto_key,
            },
            'current_name': self._model_display_name(registry, current_key),
            'downloading': ({'key': downloading_key, 'name': self._model_display_name(registry, downloading_key)}
                            if downloading_key else None),
            'groups': groups,
        }

    # ── Actions ──────────────────────────────────────────────────────────────

    # Validates and writes one whisper.* setting from this page (the model pick included).
    # The Settings page no longer offers these, so this is their only writer. Everything
    # under whisper.* is read at startup, so a successful save has needs_restart=True.
    def save_model_setting(self, path: str, raw_value) -> dict:
        if path not in MODEL_SETTING_RULES:
            return {'ok': False, 'error': f"Not a model setting: {path}", 'needs_restart': False}
        return save_validated_setting(self._load_config(), path, raw_value)

    # Current value (and the choices or limits) of every setting the page edits besides the
    # model pick, keyed by path. The page owns labels and captions; the rules stay in
    # settings_store.py so there is one source of truth for what is valid.
    def get_model_settings(self) -> dict:
        from ...dictation_languages import normalize_languages
        config = self._load_config().config
        settings = {}
        for path, rule in MODEL_SETTING_RULES.items():
            if path == 'whisper.model':
                continue  # the tile grid handles it
            spec = {'kind': rule['kind'], 'value': display_setting_value(path, config)}
            if rule['kind'] == 'select':
                spec['options'] = setting_options(path, config)
            if rule['kind'] == 'number':
                spec.update(min=rule['min'], max=rule['max'], integer=rule['integer'], unit=rule.get('unit', ''))
            settings[path] = spec
        # "Languages you speak" is a multi-select on the page: hand it over as a list of codes
        # (the stored list, so a code outside the dropdown still shows as a chip) and as choices.
        language_codes = normalize_languages(config['whisper'].get('languages'))
        spoken_choices = [option for option in setting_options('whisper.language', config) if option['value'] != 'auto']
        spoken_choices += [{'value': code, 'label': code} for code in language_codes
                           if all(option['value'] != code for option in spoken_choices)]
        settings['whisper.languages'] = {'kind': 'multi', 'value': language_codes, 'options': spoken_choices}
        return settings

    # Opens the HuggingFace cache folder. False when nothing has been downloaded yet.
    def open_model_cache_folder(self) -> bool:
        from ...utils import open_file
        _whisper_cfg, registry, _resolve = self._load_model_registry()
        cache_path = registry.get_hf_cache_path()
        if not os.path.isdir(cache_path):
            return False
        open_file(cache_path)
        return True

    # ── Downloading ──────────────────────────────────────────────────────────

    # Starts downloading one model in the background; poll get_download_progress().
    def start_model_download(self, model_key: str) -> dict:
        from ...model_download import download_snapshot
        _whisper_cfg, registry, _resolve = self._load_model_registry()
        model = registry.get_model(model_key)
        target = download_target(model)
        if target is None:
            return {'ok': False, 'error': "This model is not downloadable from here."}
        repo_id, allow_patterns = target

        def run_download(on_progress):
            download_snapshot(repo_id, model.label, allow_patterns, on_progress=on_progress)

        if not _download_tracker.start(model_key, model.label, run_download):
            return {'ok': False, 'error': "Another download is already running."}
        return {'ok': True}

    # {state: idle|running|done|error, key, percent|None, done_bytes, total_bytes, error}.
    # A done/error state is reported once, then the tracker returns to idle.
    def get_download_progress(self) -> dict:
        progress = _download_tracker.snapshot()
        _download_tracker.acknowledge()
        return progress
