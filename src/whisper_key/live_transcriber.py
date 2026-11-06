# live_transcriber.py
# Live transcription: the speech model transcribes the recording in short windows
# while the user speaks, so at key-up only the last seconds are left to decode.
# This file holds the window-joining helpers. A new window overlaps text that is
# already kept, so the helpers find where the new words start.

import re
from typing import Callable, List, NamedTuple, Sequence, Tuple

import numpy as np

SAMPLE_RATE = 16000

# The last kept word must appear again within this time in the next window.
# Word times move by about one frame (0.08 s) between two decodes of the same audio.
ANCHOR_TOLERANCE_S = 0.3
# With no anchor, keep words that start after the last kept word plus this gap.
# Short words ("the export") can start less than 0.15 s apart, so the gap stays small.
TIME_FALLBACK_GAP_S = 0.04
# A window with no anchor probably started mid-speech and the model skipped words.
# Decode again from earlier points.
RETRY_SHIFTS_S = (1.0, 2.0)


# One recognized word with its start time in seconds from the recording start.
class Word(NamedTuple):
    text: str
    start: float


# ── Joining windows ──────────────────────────────────────────────────────────

# Case and punctuation change between decodes ("Sophia," / "sophia"), so compare
# letters, digits and apostrophes only.
def _word_key(text: str) -> str:
    return re.sub(r"[^\w']", "", text.lower())


# The words of a new decode that come after the already kept words, and whether the
# last kept word was found again (the anchor). The match is by text near its time,
# not by time alone, because word times jitter between decodes.
def words_after(words: Sequence[Word], kept: Sequence[Word]) -> Tuple[List[Word], bool]:
    if not kept:
        return list(words), True
    last = kept[-1]
    last_key = _word_key(last.text)
    candidates = [i for i, word in enumerate(words)
                  if abs(word.start - last.start) <= ANCHOR_TOLERANCE_S and _word_key(word.text) == last_key]
    if candidates:
        # "the the": take the copy closest to the kept word's time.
        anchor = min(candidates, key=lambda i: abs(words[i].start - last.start))
        return list(words[anchor + 1:]), True
    return [word for word in words if word.start > last.start + TIME_FALLBACK_GAP_S], False


class WindowResult(NamedTuple):
    words: List[Word]   # words after the kept words
    anchored: bool      # False: no decode found the last kept word; words come from a time cut
    retries: int        # extra decodes needed


# Decodes audio[start_s:end_sample] and returns the words after `kept`. A missing
# anchor means the window start made the model skip speech, so it decodes again
# from 1 s and 2 s earlier. `decode_window(audio_slice, offset_s)` returns Words with
# absolute times.
def decode_after(decode_window: Callable[[np.ndarray, float], List[Word]],
                 audio: np.ndarray, start_s: float, end_sample: int,
                 kept: Sequence[Word]) -> WindowResult:
    for attempt, shift in enumerate((0.0,) + RETRY_SHIFTS_S):
        window_start = max(0.0, start_s - shift)
        words, anchored = words_after(
            decode_window(audio[int(window_start * SAMPLE_RATE):end_sample], window_start), kept)
        # From the recording start there is no earlier audio to try.
        if anchored or window_start == 0.0:
            break
    return WindowResult(words, anchored, attempt)
