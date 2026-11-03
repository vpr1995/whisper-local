# dashboard/api/style.py
# Python API for the Style page: the writing style, the style per app, the
# clean-up level, and the AI provider and polish switch. Every setting is
# written to user_settings.yaml here; the running app hot-reloads them, so
# changes apply on the next dictation.

import os
from pathlib import Path


# ── Presentation data ────────────────────────────────────────────────────────

# What each built-in writing style does, in the user's words, with a sample sentence.
# Custom styles (postprocess.styles) get no sample.
STYLE_DESCRIPTIONS = {
    'formal': ("Capital first letter and a full stop. For email and documents.", "Let's meet at 3 PM."),
    'casual': ("Capitalized, but no stiff trailing period. For chat.", "Let's meet at 3 PM"),
    'very_casual': ("All lowercase except acronyms and your own words, no final period. For texting.", "lets meet at 3 PM"),
    'verbatim': ("No automatic capitals, periods or lists. For code editors and terminals.", "lets meet at 3 pm"),
}

# What each clean-up level does, in the user's words. Keys mirror
# text_postprocess.CLEANUP_LEVELS (the level names themselves live there).
CLEANUP_DESCRIPTIONS = {
    'none': ("Off", "Type exactly what Whisper heard."),
    'light': ("Light", "Drops stutters (\"I I think\") and filler words like \"um\" and \"uh\"."),
    'medium': ("Medium", "Light, plus voice editing (\"scratch that\") and corrections (\"at 2, actually 3\")."),
    'high': ("High", "Medium, plus spoken times, email addresses and web links turned into text."),
}

# The AI settings the page edits: page field name -> config path.
AI_SETTING_PATHS = {
    'provider': 'postprocess.ai.provider',
    'polish': 'postprocess.ai.polish',
    'model': 'postprocess.ollama.model',
    'endpoint': 'postprocess.ollama.endpoint',
    'timeout': 'postprocess.ollama.timeout',
}


# ── Pure helpers ─────────────────────────────────────────────────────────────

# One sentence saying which AI answers and, when Apple Intelligence is not in use
# although the user may expect it, why not.
def ai_status_text(status: dict) -> str:
    if status['active_provider'] == 'apple_intelligence':
        return "Using Apple Intelligence (on this Mac, offline)."
    if status['provider_setting'] == 'ollama' or status['apple_available']:
        return "Using Ollama."
    return f"{status['apple_reason_text']}. Using Ollama."


# "very_casual" / "very-casual" -> "Very casual" for style names.
def humanize_name(name: str) -> str:
    return str(name).replace('_', ' ').replace('-', ' ').strip().capitalize()


class StyleApi:
    # ── Writing style ────────────────────────────────────────────────────────

    # The global writing style and every style on offer (built-ins plus the user's own).
    def get_writing_styles(self) -> dict:
        from ...styles import available_styles
        postprocess_cfg = self._load_config().config.get('postprocess', {}) or {}
        styles = []
        for style_id in available_styles(postprocess_cfg):
            description, sample = STYLE_DESCRIPTIONS.get(style_id, ("Your own style from user_settings.yaml.", ''))
            styles.append({'id': style_id, 'name': humanize_name(style_id), 'description': description, 'sample': sample})
        return {'current': str(postprocess_cfg.get('style') or ''), 'styles': styles}

    # Sets postprocess.style ('' = none). The running app hot-reloads postprocess
    # settings, so this applies from the next dictation, no restart.
    def set_writing_style(self, style_id: str) -> dict:
        from ...styles import available_styles
        config_manager = self._load_config()
        if style_id and style_id not in available_styles(config_manager.config.get('postprocess', {})):
            return {'ok': False, 'error': f"Unknown style: {style_id}"}
        config_manager.update_user_setting('postprocess', 'style', style_id)
        return {'ok': True, 'restart_required': False}

    # ── Style per app ────────────────────────────────────────────────────────

    # The apps that have their own style (postprocess.app_styles), sorted by
    # name, plus the styles a row can pick.
    def get_app_styles(self) -> dict:
        from ...styles import available_styles
        postprocess_cfg = self._load_config().config.get('postprocess', {}) or {}
        rows = [{'id': app_id, 'name': str(row.get('name') or app_id), 'style': str(row.get('style') or '')}
                for app_id, row in self._app_style_rows(postprocess_cfg).items()]
        styles = [{'id': style_id, 'name': humanize_name(style_id)} for style_id in available_styles(postprocess_cfg)]
        return {'rows': sorted(rows, key=lambda row: row['name'].lower()), 'styles': styles}

    # The apps the picker offers (installed apps on macOS, open and History apps
    # on Windows), without the apps that already have a row.
    def list_pickable_apps(self) -> list:
        from ...platform import apps
        postprocess_cfg = self._load_config().config.get('postprocess', {}) or {}
        configured = self._app_style_rows(postprocess_cfg)
        return [app for app in apps.list_pickable_apps() if app['id'] not in configured]

    # Adds the app's row or changes its style. Hot-reloaded: next dictation, no restart.
    def set_app_style(self, app_id: str, name: str, style_id: str) -> dict:
        from ...styles import available_styles
        app_id = str(app_id or '').strip().lower()
        if not app_id:
            return {'ok': False, 'error': "No app selected"}
        config_manager = self._load_config()
        postprocess_cfg = config_manager.config.get('postprocess', {}) or {}
        if style_id not in available_styles(postprocess_cfg):
            return {'ok': False, 'error': f"Unknown style: {style_id}"}
        rows = self._app_style_rows(postprocess_cfg)
        rows[app_id] = {'name': str(name or app_id).strip(), 'style': style_id}
        config_manager.update_user_setting('postprocess', 'app_styles', rows)
        return {'ok': True, 'restart_required': False}

    # Deletes the app's row: the app uses the global writing style again.
    def remove_app_style(self, app_id: str) -> dict:
        config_manager = self._load_config()
        rows = self._app_style_rows(config_manager.config.get('postprocess', {}) or {})
        if rows.pop(str(app_id or '').lower(), None) is None:
            return {'ok': False, 'error': "This app has no style of its own"}
        config_manager.update_user_setting('postprocess', 'app_styles', rows)
        return {'ok': True, 'restart_required': False}

    # ── Clean-up level ───────────────────────────────────────────────────────

    # The current postprocess.cleanup level and every level on offer, mildest first.
    # An unknown/missing value shows as the default, like the app itself resolves it.
    def get_cleanup_levels(self) -> dict:
        from ...text_postprocess import CLEANUP_LEVELS, DEFAULT_CLEANUP
        configured = (self._load_config().config.get('postprocess', {}) or {}).get('cleanup')
        current = str(configured).strip().lower() if configured is not None else DEFAULT_CLEANUP
        return {
            'current': current if current in CLEANUP_LEVELS else DEFAULT_CLEANUP,
            'default': DEFAULT_CLEANUP,
            'levels': [{'id': level_id,
                        'name': CLEANUP_DESCRIPTIONS.get(level_id, (humanize_name(level_id), ''))[0],
                        'description': CLEANUP_DESCRIPTIONS.get(level_id, ('', ''))[1]}
                       for level_id in CLEANUP_LEVELS],
        }

    # Sets postprocess.cleanup. Hot-reloaded like the writing style: next dictation, no restart.
    def set_cleanup_level(self, level_id: str) -> dict:
        from ...text_postprocess import CLEANUP_LEVELS
        if level_id not in CLEANUP_LEVELS:
            return {'ok': False, 'error': f"Unknown clean-up level: {level_id}"}
        self._load_config().update_user_setting('postprocess', 'cleanup', level_id)
        return {'ok': True, 'restart_required': False}

    # ── AI (provider, polish, Ollama server) ─────────────────────────────────

    # Everything the AI card shows: saved values, the provider that will really answer,
    # and one sentence of live status for the caption under "AI provider".
    def get_ai_settings(self) -> dict:
        from ... import ai_provider
        postprocess = self._load_config().config.get('postprocess', {}) or {}
        ai = postprocess.get('ai', {}) or {}
        ollama = postprocess.get('ollama', {}) or {}
        status = ai_provider.describe_status(postprocess)
        return {
            'provider': status['provider_setting'],
            'polish': bool(ai.get('polish', False)),
            'model': str(ollama.get('model') or ''),
            'endpoint': str(ollama.get('endpoint') or ''),
            'timeout': ollama.get('timeout', 5),
            'active_provider': status['active_provider'],
            'status_text': ai_status_text(status),
        }

    # Saves ONE AI setting (the page saves each control as it is edited). settings_store
    # validates it, so a typo can't leave polish pointing at a dead address. postprocess.*
    # hot-reloads in the running app: no restart. Returns the fresh settings so the page
    # can update the caption and show or hide the Ollama fields.
    def set_ai_setting(self, key: str, value) -> dict:
        from ...settings_store import save_validated_setting
        if key not in AI_SETTING_PATHS:
            return {'ok': False, 'error': f"Unknown AI setting: {key}"}
        result = save_validated_setting(self._load_config(), AI_SETTING_PATHS[key], value)
        if not result['ok']:
            return {'ok': False, 'error': result['error']}
        return {'ok': True, 'restart_required': False, 'settings': self.get_ai_settings()}

    # ── Hand-edited files ────────────────────────────────────────────────────

    # Custom writing styles live in user_settings.yaml (postprocess.styles).
    def open_user_settings_file(self) -> bool:
        return self._open_user_file('user_settings.yaml')

    # ── Helpers ──────────────────────────────────────────────────────────────

    # A plain copy of postprocess.app_styles with only well-formed rows, so a
    # save never mutates the loaded config and a hand-edit typo can't crash the page.
    @staticmethod
    def _app_style_rows(postprocess_cfg: dict) -> dict:
        table = postprocess_cfg.get('app_styles')
        if not isinstance(table, dict):
            return {}
        return {str(app_id).lower(): {'name': str(row.get('name') or app_id), 'style': str(row.get('style') or '')}
                for app_id, row in table.items() if isinstance(row, dict)}

    # Opens a file in the user's config folder with the OS default app.
    def _open_user_file(self, filename: str) -> bool:
        from ...utils import get_user_app_data_path, open_file
        path = Path(get_user_app_data_path()) / filename
        if not os.path.exists(path):
            return False
        open_file(path)
        return True
