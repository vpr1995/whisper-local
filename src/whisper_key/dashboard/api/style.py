# dashboard/api/style.py
# Python API for the Style page: profiles (preset bundles of settings), the global
# writing style, the clean-up level, the AI provider and polish, the AI transforms
# list and the per-app rules. Profiles, style, clean-up and AI settings are
# written to disk here; transforms and app rules are read-only lists with "open the
# YAML" buttons (hand-edited files).

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


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

# Where polish can be switched on, in the order the page lists them: (id, label, app rule id).
# `everywhere` is the global setting; the others are the shipped app rules that carry a
# `polish` override. Code editors and terminals are never listed: they never polish.
POLISH_CATEGORIES = (
    ('email', "Email apps", 'email-clients'),
    ('chat', "Chat apps", 'chat-apps'),
    ('everywhere', "Everywhere else", None),
)

# Friendly wording for the settings a profile flips. Anything not listed falls back to
# "section.key = value" so a user-defined profile still describes itself.
OVERRIDE_PHRASES = {
    ('whisper', 'model'): lambda value: f"Model: {value}",
    ('whisper', 'task'): lambda value: "Translates speech to English" if value == 'translate' else "",
    ('whisper', 'language'): lambda value: f"Language: {value}",
    ('whisper', 'initial_prompt'): lambda value: "Context prompt for the model" if value else "",
    ('clipboard', 'auto_paste'): lambda value: "Pastes automatically" if value else "Copies to the clipboard only",
    ('clipboard', 'delivery_method'): lambda value: "",  # always "paste" in shipped profiles: noise
    ('hotkey', 'recording_mode'): lambda value: "Hold to talk" if value == 'push_to_talk' else "Press to start, press to stop",
    ('voice_commands', 'enabled'): lambda value: "Voice commands on" if value else "Voice commands off",
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

# One short sentence for a single profile override ('' when it is not worth showing).
def describe_override(section: str, key: str, value) -> str:
    phrase = OVERRIDE_PHRASES.get((section, key))
    return phrase(value) if phrase else f"{section}.{key} = {value}"


# All the sentences a profile's `overrides` block stands for, in file order.
def describe_profile_overrides(overrides) -> list:
    sentences = []
    for section, values in (overrides or {}).items():
        if isinstance(values, dict):
            sentences += [describe_override(section, key, value) for key, value in values.items()]
    return [sentence for sentence in sentences if sentence]


# "code_editors" / "code-editors" -> "Code editors" for rule and style names.
def humanize_name(name: str) -> str:
    return str(name).replace('_', ' ').replace('-', ' ').strip().capitalize()


# One app rule as plain data: which apps it matches and what it changes.
def summarize_rule(rule: dict) -> dict:
    matches = rule.get('match') or []
    if isinstance(matches, str):
        matches = [matches]
    effects = []
    if rule.get('suppress'):
        effects.append("Never dictates here")
    if rule.get('style'):
        effects.append(f"Style: {humanize_name(rule['style'])}")
    if 'auto_paste' in rule:
        effects.append("Pastes automatically" if rule['auto_paste'] else "Copies only, no paste")
    if rule.get('auto_send'):
        effects.append("Presses Enter after pasting")
    if rule.get('initial_prompt'):
        effects.append("Custom prompt")
    if isinstance(rule.get('polish'), bool):
        effects.append("AI polish on" if rule['polish'] else "AI polish off")
    return {
        'id': rule.get('id') or '',
        'name': humanize_name(rule['id']) if rule.get('id') else "Your rule",
        'apps': [str(app) for app in matches],
        'effects': effects,
    }


class StyleApi:
    # ── Profiles ─────────────────────────────────────────────────────────────

    # Fresh ProfileManager; its `active` persists in profiles.yaml, so this is the
    # configured profile (the running app switches the same way from the tray).
    def _load_profile_manager(self):
        from ...profiles import ProfileManager
        return ProfileManager(self._load_config())

    def get_profiles(self) -> dict:
        manager = self._load_profile_manager()
        active = manager.get_active()
        return {
            'active': active,
            'profiles': [{
                'id': name,
                'name': humanize_name(name),
                'description': (manager.profiles[name] or {}).get('description', ''),
                'changes': describe_profile_overrides((manager.profiles[name] or {}).get('overrides')),
                'active': name == active,
            } for name in manager.list_profiles()],
        }

    # Applies a profile's settings to user_settings.yaml and remembers it as active.
    # Profiles overwrite those settings for good (even `whisper.model`), and the
    # running app only reads them at startup, hence restart_required.
    def set_profile(self, profile_id: str) -> dict:
        if not self._load_profile_manager().apply(profile_id):
            return {'ok': False, 'error': f"Unknown profile: {profile_id}"}
        return {'ok': True, 'restart_required': True}

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
            'polish_in': self._polish_categories(bool(ai.get('polish', False))),
            'model': str(ollama.get('model') or ''),
            'endpoint': str(ollama.get('endpoint') or ''),
            'timeout': ollama.get('timeout', 5),
            'active_provider': status['active_provider'],
            'status_text': ai_status_text(status),
        }

    # [{id, label, enabled}] for the "Polish in" toggles. A category whose app rule is
    # unset follows the global setting; one whose rule the user turned off is not listed.
    def _polish_categories(self, global_polish: bool) -> list:
        from ...app_rules import AppRules
        rules_by_id = {rule.get('id'): rule for rule in AppRules().rules}
        categories = []
        for category_id, label, rule_id in POLISH_CATEGORIES:
            if rule_id is None:
                categories.append({'id': category_id, 'label': label, 'enabled': global_polish})
            elif rule_id in rules_by_id:
                rule_polish = rules_by_id[rule_id].get('polish')
                categories.append({'id': category_id, 'label': label,
                                   'enabled': rule_polish if isinstance(rule_polish, bool) else global_polish})
        return categories

    # Switches AI polish on or off for one category. "Everywhere else" is the global
    # setting; an app category writes `polish` into its rule in app_rules.yaml.
    def set_polish_for(self, category_id: str, enabled) -> dict:
        from ...app_rules import set_rule_polish
        rule_by_category = {category: rule for category, _, rule in POLISH_CATEGORIES}
        if category_id not in rule_by_category:
            return {'ok': False, 'error': f"Unknown polish category: {category_id}"}
        rule_id = rule_by_category[category_id]
        if not isinstance(enabled, bool):
            return {'ok': False, 'error': "Expected on or off."}
        if rule_id is None:
            return self.set_ai_setting('polish', enabled)
        try:
            set_rule_polish(rule_id, enabled)
        except Exception as error:
            logger.error(f"Could not save polish for {rule_id}: {error}")
            return {'ok': False, 'error': "Could not write app_rules.yaml."}
        return {'ok': True, 'restart_required': False, 'settings': self.get_ai_settings()}

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

    # ── Transforms ───────────────────────────────────────────────────────────

    # Transforms as {name, description, keys}; `keys` is [] when it has no hotkey.
    def get_transforms(self) -> list:
        from ...transforms import TransformsManager
        return [{
            'name': humanize_name(transform.get('name', '')),
            'description': transform.get('description', ''),
            'keys': [part.strip() for part in str(transform.get('hotkey') or '').split('+') if part.strip()],
        } for transform in TransformsManager().list_transforms()]

    def open_transforms_file(self) -> bool:
        return self._open_user_file('transforms.yaml')

    # ── Per-app rules ────────────────────────────────────────────────────────

    def get_app_rules(self) -> list:
        from ...app_rules import AppRules
        return [summarize_rule(rule) for rule in AppRules().rules]

    def open_app_rules_file(self) -> bool:
        return self._open_user_file('app_rules.yaml')

    # ── Hand-edited files ────────────────────────────────────────────────────

    # Custom profiles live in profiles.yaml, custom writing styles in user_settings.yaml.
    def open_profiles_file(self) -> bool:
        return self._open_user_file('profiles.yaml')

    def open_user_settings_file(self) -> bool:
        return self._open_user_file('user_settings.yaml')

    # ── Helpers ──────────────────────────────────────────────────────────────

    # Opens a file in the user's config folder with the OS default app.
    def _open_user_file(self, filename: str) -> bool:
        from ...utils import get_user_app_data_path, open_file
        path = Path(get_user_app_data_path()) / filename
        if not os.path.exists(path):
            return False
        open_file(path)
        return True
