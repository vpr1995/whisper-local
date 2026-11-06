# tests/test_live_transcriber.py
# Joining overlapping decode windows for live transcription. A fake decoder returns
# words from a script, so each case controls time jitter, skipped speech and
# repeated words. The failure cases come from the spike: short adjacent words were
# dropped by a time-only cut, and windows that started mid-speech skipped seconds.

import unittest

import numpy as np

from whisper_key.live_transcriber import SAMPLE_RATE, Word, decode_after, words_after

KEPT = [Word("fix", 9.6), Word("for", 9.8), Word("the", 10.0)]


class WordsAfterTests(unittest.TestCase):
    def test_nothing_kept_returns_every_word(self):
        words = [Word("hello", 0.1), Word("there", 0.4)]
        self.assertEqual(words_after(words, []), (words, True))

    def test_words_after_the_anchor_are_returned(self):
        words = [Word("for", 9.8), Word("the", 10.0), Word("export", 10.3), Word("feature", 10.7)]
        self.assertEqual(words_after(words, KEPT), ([Word("export", 10.3), Word("feature", 10.7)], True))

    def test_a_short_next_word_survives_time_jitter(self):
        # The anchor moved one frame later and the next word starts only 0.1 s after
        # it. A time-only cut at kept + 0.15 s dropped "export" in the spike.
        words = [Word("the", 10.08), Word("export", 10.1)]
        self.assertEqual(words_after(words, KEPT), ([Word("export", 10.1)], True))

    def test_case_and_punctuation_do_not_break_the_anchor(self):
        kept = [Word("Sophia,", 5.0)]
        words = [Word("sophia", 5.08), Word("and", 5.5)]
        self.assertEqual(words_after(words, kept), ([Word("and", 5.5)], True))

    def test_a_repeated_word_matches_the_copy_closest_in_time(self):
        words = [Word("the", 9.85), Word("the", 10.02), Word("file", 10.3)]
        self.assertEqual(words_after(words, KEPT), ([Word("file", 10.3)], True))

    def test_without_an_anchor_a_time_cut_is_used(self):
        words = [Word("the", 8.0), Word("export", 10.2)]
        self.assertEqual(words_after(words, KEPT), ([Word("export", 10.2)], False))


# A fake decoder over a fixed script of words. Windows that start inside
# `skip_if_start_in` return only words after `skip_until`, like Parakeet's skips.
class FakeDecoder:
    def __init__(self, script, skip_if_start_in=(), skip_until=0.0):
        self.script = script
        self.skip_if_start_in = skip_if_start_in
        self.skip_until = skip_until
        self.calls = []

    def __call__(self, audio_slice, offset_s):
        end_s = offset_s + len(audio_slice) / SAMPLE_RATE
        self.calls.append(offset_s)
        skipping = any(low <= offset_s < high for low, high in self.skip_if_start_in)
        return [word for word in self.script
                if offset_s <= word.start < end_s and not (skipping and word.start < self.skip_until)]


SCRIPT = [Word(f"w{i}", i * 0.5) for i in range(40)]   # one word every 0.5 s for 20 s
AUDIO = np.zeros(20 * SAMPLE_RATE, dtype=np.float32)


class DecodeAfterTests(unittest.TestCase):
    def test_a_good_window_needs_no_retry(self):
        decoder = FakeDecoder(SCRIPT)
        result = decode_after(decoder, AUDIO, 7.0, 15 * SAMPLE_RATE, SCRIPT[:20])
        self.assertEqual(result.words, SCRIPT[20:30])
        self.assertTrue(result.anchored)
        self.assertEqual(result.retries, 0)
        self.assertEqual(decoder.calls, [7.0])

    def test_a_skipped_window_start_is_retried_earlier(self):
        # A window that starts at 7 s skips speech up to 12 s, so the anchor at 9.5 s
        # is missing. The retry from 6 s works.
        decoder = FakeDecoder(SCRIPT, skip_if_start_in=[(6.5, 7.5)], skip_until=12.0)
        result = decode_after(decoder, AUDIO, 7.0, 15 * SAMPLE_RATE, SCRIPT[:20])
        self.assertEqual(result.words, SCRIPT[20:30])
        self.assertTrue(result.anchored)
        self.assertEqual(result.retries, 1)
        self.assertEqual(decoder.calls, [7.0, 6.0])

    def test_when_every_retry_fails_the_time_cut_is_used(self):
        decoder = FakeDecoder(SCRIPT, skip_if_start_in=[(4.5, 7.5)], skip_until=12.0)
        result = decode_after(decoder, AUDIO, 7.0, 15 * SAMPLE_RATE, SCRIPT[:20])
        self.assertEqual(result.words, SCRIPT[24:30])
        self.assertFalse(result.anchored)
        self.assertEqual(result.retries, 2)
        self.assertEqual(decoder.calls, [7.0, 6.0, 5.0])

    def test_no_retry_before_the_recording_start(self):
        decoder = FakeDecoder(SCRIPT, skip_if_start_in=[(0.0, 1.0)], skip_until=3.0)
        result = decode_after(decoder, AUDIO, 0.0, 5 * SAMPLE_RATE, [Word("w1", 0.5)])
        self.assertFalse(result.anchored)
        self.assertEqual(result.retries, 0)
        self.assertEqual(decoder.calls, [0.0])


if __name__ == '__main__':
    unittest.main()
