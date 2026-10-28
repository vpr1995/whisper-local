# auto_model.py
# Resolves whisper.model: auto to a concrete model, so a new user never has to
# choose. Parakeet is faster than Whisper base on CPU and more accurate than
# Whisper small, but only knows 25 European languages; a set-up GPU runs
# Whisper large-v3-turbo; anything else keeps Whisper base.

import logging

from .dictation_languages import normalize_languages

logger = logging.getLogger(__name__)

AUTO = 'auto'

# Parakeet TDT v3's languages (model card, nvidia/parakeet-tdt-0.6b-v3).
PARAKEET_LANGUAGES = {
    'bg', 'hr', 'cs', 'da', 'nl', 'en', 'et', 'fi', 'fr', 'de', 'el', 'hu', 'it',
    'lv', 'lt', 'mt', 'pl', 'pt', 'ro', 'sk', 'sl', 'es', 'sv', 'ru', 'uk',
}


# (model key, one-line reason shown at startup).
def pick_model(whisper_cfg: dict) -> tuple:
    if whisper_cfg.get('device') == 'cuda':
        return 'large-v3-turbo', "best accuracy on your GPU"
    spoken = _spoken_languages(whisper_cfg)
    if whisper_cfg.get('task') == 'translate':
        return 'base', "translation needs Whisper"
    if spoken and all(code in PARAKEET_LANGUAGES for code in spoken):
        return 'parakeet-v3', f"fast and accurate for {', '.join(spoken)}"
    if spoken:
        return 'base', f"covers {', '.join(spoken)}"
    return 'base', "language unknown, so the model that covers all of them"


# What the user dictates in: the forced language, else the listed languages,
# else the operating system's language.
def _spoken_languages(whisper_cfg: dict) -> list:
    language = whisper_cfg.get('language')
    if language and language != 'auto':
        return [str(language).lower()]
    listed = normalize_languages(whisper_cfg.get('languages'))
    if listed:
        return listed
    system = _system_language()
    return [system] if system else []


def _system_language() -> str:
    try:
        from .platform import app
        return app.system_language()
    except Exception as e:
        logger.debug(f"System language unavailable: {e}")
        return ''
