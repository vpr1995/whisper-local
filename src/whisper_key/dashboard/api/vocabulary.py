# dashboard/api/vocabulary.py
# Python API for the Vocabulary page: hotwords (whisper.hotwords), replacements
# (postprocess.replacements) and snippets (postprocess.snippets). All three are
# plain edits of user_settings.yaml; the running app hot-reloads them, so changes
# apply on the next dictation without a restart.

import contextlib
import io
import logging

logger = logging.getLogger(__name__)


# dictionary.add_word/remove_word print CLI feedback; swallow it so a GUI action
# never writes to the launcher's stdout.
def _quiet(function, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args)


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

    # False when the word is empty or already present.
    def add_word(self, word: str) -> bool:
        from ... import dictionary
        return _quiet(dictionary.add_word, word)

    def remove_word(self, word: str) -> bool:
        from ... import dictionary
        return _quiet(dictionary.remove_word, word)

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
