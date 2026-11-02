# dashboard/api/vocabulary.py
# Python API for the Vocabulary page: hotwords (whisper.hotwords), replacements
# (postprocess.replacements) and snippets (postprocess.snippets). All three are
# plain edits of user_settings.yaml; the running app hot-reloads them, so changes
# apply on the next dictation without a restart.

import logging

logger = logging.getLogger(__name__)


# True when the engine the user selected does not take Words (hotwords) while it
# listens. Parakeet does not: its hotword search dropped or garbled short
# dictations, so parakeet_engine.py turns it off and word snap fixes names after. A Parakeet model runs on Parakeet whatever
# whisper.backend says (ModelRegistry.resolve), and `auto` may pick one.
# Reads only the config, so the page never imports the model stack.
def selected_engine_skips_hotwords(whisper_cfg: dict) -> bool:
    from ...engine_factory import resolve_backend
    backend, model_key = resolve_backend(whisper_cfg, None)
    model_cfg = (whisper_cfg.get('models') or {}).get(model_key) or {}
    return backend == 'parakeet' or model_cfg.get('backend') == 'parakeet'


class VocabularyApi:
    # ── Words (hotwords) ─────────────────────────────────────────────────────

    def get_vocabulary(self) -> dict:
        from ... import corrections, dictionary
        return {
            'words': sorted(dictionary.list_hotwords(), key=str.lower),
            'replacements': [{'from': str(item.get('from', '')), 'to': str(item.get('to', ''))}
                             for item in corrections.list_replacements()],
            'snippets': [{'trigger': str(item.get('trigger', '')),
                          'expansion': str(item.get('expansion', '')),
                          'mode': 'alone' if str(item.get('mode', '')).lower() == 'alone' else 'anywhere'}
                         for item in corrections.list_snippets()],
        }

    # Lets the Words card say up front how the selected engine uses words, so a
    # word that Parakeet only fixes afterwards is not a surprise.
    def get_words_engine(self) -> dict:
        from ...config_manager import ConfigManager
        try:
            whisper_cfg = ConfigManager(quiet=True).get_whisper_config()
            return {'skips_hotwords': selected_engine_skips_hotwords(whisper_cfg), 'engine': 'Parakeet'}
        except Exception as e:
            logger.debug(f"Could not tell which engine is selected: {e}")
            return {'skips_hotwords': False, 'engine': ''}

    # False when the word is empty or already present.
    def add_word(self, word: str) -> bool:
        from ... import dictionary
        return dictionary.add_word(word)

    def remove_word(self, word: str) -> bool:
        from ... import dictionary
        return dictionary.remove_word(word)

    # Frequent proper-noun-like words from the user's history, as [{word, count}].
    # Never added automatically: the page offers them as one-click chips.
    def suggest_words(self) -> list:
        from ... import dictionary
        return [{'word': word, 'count': count} for word, count in dictionary.suggest_hotwords_from_history()]

    # ── Replacements (wrong -> right) ────────────────────────────────────────

    # Adds a pair, or updates the target of an existing "wrong" text.
    def add_replacement(self, wrong_text: str, right_text: str) -> bool:
        from ... import corrections
        return corrections.add_replacement(wrong_text, right_text)

    def remove_replacement(self, wrong_text: str) -> bool:
        from ... import corrections
        return corrections.remove_replacement(wrong_text)

    # ── Snippets (spoken trigger -> expansion) ───────────────────────────────

    def save_snippet(self, trigger: str, expansion: str, mode: str = 'anywhere') -> bool:
        from ... import corrections
        return corrections.save_snippet(trigger, expansion, mode)

    def remove_snippet(self, trigger: str) -> bool:
        from ... import corrections
        return corrections.remove_snippet(trigger)
