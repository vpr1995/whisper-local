# tests/test_live_transcriber.py
# Joining overlapping decode windows for live transcription. A fake decoder returns
# words from a script, so each case controls time jitter, skipped speech and
# repeated words. The failure cases come from the spike: short adjacent words were
# dropped by a time-only cut, and windows that started mid-speech skipped seconds.

import unittest

import numpy as np

from whisper_key.live_transcriber import SAMPLE_RATE, LiveTranscriber, Word, decode_after, words_after

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


# Feeds `seconds` of audio in 0.4 s chunks and runs rounds inline on the live
# cadence, like the worker thread but without a clock.
def replay(live, seconds):
    step, done = int(0.4 * SAMPLE_RATE), 0
    for end in range(step, int(seconds * SAMPLE_RATE) + 1, step):
        live.feed(np.zeros(step, dtype=np.float32))
        heard = live._drain()
        if heard - done >= live._round_plan(heard)[0] * SAMPLE_RATE:
            done = heard
            live.step(heard)


LONG_SCRIPT = [Word(f"w{i}", i * 0.5) for i in range(200)]   # 100 s of words


class LiveTranscriberTests(unittest.TestCase):
    def test_finish_before_any_round_decodes_everything(self):
        live = LiveTranscriber(FakeDecoder(SCRIPT))
        live.feed(np.zeros(3 * SAMPLE_RATE, dtype=np.float32))
        self.assertEqual(live.finish(), " ".join(f"w{i}" for i in range(6)))

    def test_a_too_short_recording_gives_empty_text(self):
        live = LiveTranscriber(lambda audio, offset: [] if len(audio) < 0.25 * SAMPLE_RATE else [Word("x", 0)])
        live.feed(np.zeros(SAMPLE_RATE // 10, dtype=np.float32))
        self.assertEqual(live.finish(), "")

    def test_rounds_then_finish_give_every_word_once(self):
        # 70 s crosses from fast rounds to slow rounds.
        live = LiveTranscriber(FakeDecoder(LONG_SCRIPT))
        replay(live, 70)
        self.assertEqual(live.finish(), " ".join(f"w{i}" for i in range(140)))
        self.assertGreater(live.last_stats['rounds'], 100)
        self.assertTrue(live.last_stats['release_anchored'])

    def test_old_words_get_committed(self):
        live = LiveTranscriber(FakeDecoder(LONG_SCRIPT))
        replay(live, 40)
        self.assertTrue(live._committed)
        self.assertGreater(live._commit_t, 20.0)

    def test_on_text_shows_the_words_so_far(self):
        shown = []
        live = LiveTranscriber(FakeDecoder(SCRIPT), on_text=shown.append)
        replay(live, 2)
        self.assertEqual(shown[-1], "w0 w1 w2 w3")

    def test_feed_after_finish_is_ignored(self):
        live = LiveTranscriber(FakeDecoder(SCRIPT))
        live.feed(np.zeros(SAMPLE_RATE, dtype=np.float32))
        live.finish()
        live.feed(np.zeros(SAMPLE_RATE, dtype=np.float32))
        self.assertEqual(live._drain(), SAMPLE_RATE)

    def test_a_round_that_ends_after_finish_is_dropped(self):
        live = LiveTranscriber(FakeDecoder(SCRIPT))
        live.feed(np.zeros(5 * SAMPLE_RATE, dtype=np.float32))
        heard = live._drain()
        live.finish()
        live.step(heard)
        self.assertEqual(live._rounds, 0)

    def test_a_failed_round_makes_finish_return_none(self):
        def broken(audio, offset):
            raise RuntimeError("model gone")
        live = LiveTranscriber(broken)
        live.start(np.zeros(SAMPLE_RATE, dtype=np.float32))
        live._thread.join(timeout=2)
        self.assertIsNone(live.finish())

    def test_a_48k_mic_is_resampled_to_16k(self):
        live = LiveTranscriber(FakeDecoder(SCRIPT), input_rate=48000)
        for _ in range(10):
            live.feed(np.zeros(4800, dtype=np.float32))   # 1 s at 48 kHz
        live.finish()
        self.assertEqual(live._heard, SAMPLE_RATE)


if __name__ == '__main__':
    unittest.main()
