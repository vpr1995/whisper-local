# tests/test_word_snap.py
# Word snap: a misheard name that sounds like a Words entry becomes that entry,
# and everything else (ordinary words, other real names) stays as it is.
# The cases come from real Parakeet mishearings of "Prudhvi".

import unittest

from whisper_key.word_snap import snap_to_words

WORDS = ['Prudhvi']


class SnapsNearCertainMishearingsTests(unittest.TestCase):
    def test_mishearings_that_sound_the_same_become_the_word(self):
        for heard in ('Pradvi', 'Pradhvi', 'Pradve'):
            self.assertEqual(snap_to_words(f"Thanks {heard} for the notes.", WORDS),
                             "Thanks Prudhvi for the notes.", heard)

    def test_punctuation_and_possessive_are_kept(self):
        self.assertEqual(snap_to_words("Is that Pradvi's laptop?", WORDS), "Is that Prudhvi's laptop?")
        self.assertEqual(snap_to_words("Ask Pradvi, then me.", WORDS), "Ask Prudhvi, then me.")

    def test_a_name_split_in_two_is_joined(self):
        self.assertEqual(snap_to_words("Call Pra dvi today.", WORDS), "Call Prudhvi today.")

    def test_the_first_word_of_a_sentence_is_checked_too(self):
        self.assertEqual(snap_to_words("Pradvi will send it.", WORDS), "Prudhvi will send it.")

    def test_multi_word_entries_match(self):
        self.assertEqual(snap_to_words("Open Clawed Code now.", ['Claude Code']), "Open Claude Code now.")


class LeavesEverythingElseAloneTests(unittest.TestCase):
    def test_other_real_names_stay(self):
        for name in ('Prithvi', 'Rudy', 'Pradeep', 'David'):
            text = f"Thanks {name} for the notes."
            self.assertEqual(snap_to_words(text, WORDS), text, name)

    def test_ordinary_words_stay_even_at_the_start_of_a_sentence(self):
        for text in ("Prove it works.", "Privy to the plan.", "Proud of you.", "Ready now."):
            self.assertEqual(snap_to_words(text, WORDS), text)

    # Too far to be sure: "Radvi" scores exactly like the real word "Proud", and
    # "Rudvi" lacks the first sound like "Llama" for "Ollama". These are left for
    # an exact replacement (History > "Fix this everywhere").
    def test_uncertain_mishearings_are_left_for_an_exact_fix(self):
        for heard in ('Rudvi', 'Radvi', 'Prutvi', 'Pratvi', 'Puddi'):
            text = f"Thanks {heard} for the notes."
            self.assertEqual(snap_to_words(text, WORDS), text, heard)

    def test_a_correct_word_keeps_the_word_after_it(self):
        self.assertEqual(snap_to_words("Parakeet v3 is fast.", ['Parakeet']), "Parakeet v3 is fast.")

    def test_a_different_word_missing_the_first_sound_stays(self):
        self.assertEqual(snap_to_words("Llama 2 is out.", ['Ollama']), "Llama 2 is out.")

    def test_lowercase_words_are_never_checked(self):
        self.assertEqual(snap_to_words("we must pradvi it", WORDS), "we must pradvi it")

    def test_the_correct_word_is_left_as_written(self):
        self.assertEqual(snap_to_words("Prudhvi said hi.", WORDS), "Prudhvi said hi.")

    def test_no_words_or_no_text_is_a_no_op(self):
        self.assertEqual(snap_to_words("Pradvi said hi.", []), "Pradvi said hi.")
        self.assertEqual(snap_to_words("", WORDS), "")

    def test_short_words_are_too_risky_to_snap(self):
        self.assertEqual(snap_to_words("Ask Al today.", ['Ali']), "Ask Al today.")


class PipelineTests(unittest.TestCase):
    def _run(self, text, **config):
        from whisper_key.text_postprocess import postprocess
        base = {'vocabulary_words': WORDS, 'word_snap': True, 'cleanup': 'none'}
        return postprocess(text, {**base, **config})

    def test_postprocess_snaps_words(self):
        self.assertEqual(self._run("Thanks Pradvi."), "Thanks Prudhvi.")

    def test_the_switch_turns_it_off(self):
        self.assertEqual(self._run("Thanks Pradvi.", word_snap=False), "Thanks Pradvi.")

    def test_an_exact_replacement_still_wins(self):
        out = self._run("Thanks Pradvi.", replacements=[{'from': 'Pradvi', 'to': 'PV'}])
        self.assertEqual(out, "Thanks PV.")

    def test_lowercase_style_keeps_the_word_capitalized(self):
        self.assertEqual(self._run("Thanks Pradvi", style='very_casual'), "thanks Prudhvi")


if __name__ == '__main__':
    unittest.main()
