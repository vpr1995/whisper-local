# live_transcriber.py
# Live transcription: the speech model transcribes the recording in short windows
# while the user speaks, so at key-up only the last seconds are left to decode.
# The window-joining helpers find where the new words of an overlapping window start.
# LiveTranscriber runs the rounds on a worker thread and decodes the rest at key-up.

import collections
import logging
import re
import threading
import time
from typing import Callable, List, NamedTuple, Optional, Sequence, Tuple

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


# ── Live session ─────────────────────────────────────────────────────────────

def _join(words: Sequence[Word]) -> str:
    return " ".join(word.text for word in words)


# One dictation. The audio callback feeds raw mono chunks; a worker thread runs
# decode rounds over the newest audio and commits old words, so at key-up
# finish() decodes only the last few seconds. Cadence numbers come from the spike
# (docs/plans/2026-11-06-0933-live-parakeet-local-refiner-implementation.md).
class LiveTranscriber:
    # First minute: frequent rounds for a live overlay text. Commits wait until 15 s
    # of open text, so short dictations are decoded with full context.
    FAST_PHASE_S = 60.0
    FAST_INTERVAL_S = 0.4
    FAST_LEFT_CONTEXT_S = 5.0
    FAST_COMMIT_AFTER_S = 15.0
    # Long recordings: few rounds with short context. This cut ASR CPU from ~95% to
    # ~12% on a 15-min clip with no change in key-up time.
    SLOW_INTERVAL_S = 5.0
    SLOW_LEFT_CONTEXT_S = 3.0
    # Words that start this close to the round end can still change; do not commit them.
    COMMIT_GAP_S = 2.0
    # At key-up, decode again this much audio before the cut, so the join has an anchor.
    RELEASE_CONTEXT_S = 4.0
    WORKER_POLL_S = 0.02

    def __init__(self,
                 decode_window: Callable[[np.ndarray, float], List[Word]],
                 input_rate: int = SAMPLE_RATE,
                 on_text: Optional[Callable[[str], None]] = None):
        self.logger = logging.getLogger(__name__)
        self._decode_window = decode_window
        self._on_text = on_text
        # Mic chunks wait here; the audio callback only appends.
        self._pending = collections.deque()
        # Mic rates other than 16 kHz (WASAPI) are resampled by the worker. A stream
        # resampler keeps its state across chunks, so chunk edges get no artifacts.
        self._resampler = None
        if input_rate != SAMPLE_RATE:
            import soxr
            self._resampler = soxr.ResampleStream(input_rate, SAMPLE_RATE, 1, dtype='float32')
        # 16 kHz audio of the whole recording. Grows by doubling, so no copy per round.
        self._audio = np.zeros(60 * SAMPLE_RATE, dtype=np.float32)
        self._heard = 0
        self._drain_lock = threading.Lock()

        # Round state, guarded by _lock.
        self._lock = threading.Lock()
        self._finished = False
        self._failed = False
        self._committed: List[Word] = []
        self._commit_t = 0.0
        self._open_words: List[Word] = []
        self._round_end = 0.0
        self._rounds = 0
        self._retries = 0
        self._busy_s = 0.0
        # Timing of the last finish(), for the log (no text).
        self.last_stats = None

        self._stop = threading.Event()
        self._thread = None

    # ── Feeding audio ──

    # Starts the worker. `preroll` is the audio from before the key press (mono).
    def start(self, preroll: Optional[np.ndarray] = None):
        if preroll is not None and len(preroll):
            self.feed(preroll)
        self._thread = threading.Thread(target=self._run, daemon=True, name='live-transcriber')
        self._thread.start()

    # Called on the audio thread: keep it cheap.
    def feed(self, mono_chunk: np.ndarray):
        if not self._finished:
            self._pending.append(mono_chunk)

    # Moves fed chunks into the 16 kHz buffer. `last` flushes the resampler.
    def _drain(self, last: bool = False) -> int:
        with self._drain_lock:
            while self._pending:
                self._append(self._pending.popleft().reshape(-1).astype(np.float32, copy=False), last=False)
            if last and self._resampler is not None:
                self._append(np.zeros(0, dtype=np.float32), last=True)
            return self._heard

    def _append(self, chunk: np.ndarray, last: bool):
        if self._resampler is not None:
            chunk = self._resampler.resample_chunk(chunk, last=last)
        end = self._heard + len(chunk)
        if end > len(self._audio):
            grown = np.zeros(max(end, 2 * len(self._audio)), dtype=np.float32)
            grown[:self._heard] = self._audio[:self._heard]
            # A running round keeps its reference to the old array; that stays valid.
            self._audio = grown
        self._audio[self._heard:end] = chunk
        self._heard = end

    # ── Rounds ──

    # (interval, left context, commit after) for a recording `heard` samples long.
    def _round_plan(self, heard: int) -> Tuple[float, float, float]:
        if heard / SAMPLE_RATE <= self.FAST_PHASE_S:
            return self.FAST_INTERVAL_S, self.FAST_LEFT_CONTEXT_S, self.FAST_COMMIT_AFTER_S
        return self.SLOW_INTERVAL_S, self.SLOW_LEFT_CONTEXT_S, 0.0

    # One round over [commit point - left context, heard]. Commits words that start
    # more than COMMIT_GAP_S before the round end. Results of a round that ends after
    # key-up are dropped.
    def step(self, heard: int):
        round_started = time.perf_counter()
        _, left_context, commit_after = self._round_plan(heard)
        with self._lock:
            if self._finished:
                return
            commit_t, committed = self._commit_t, list(self._committed)
        result = decode_after(self._decode_window, self._audio, max(0.0, commit_t - left_context),
                              heard, committed)
        end = heard / SAMPLE_RATE
        with self._lock:
            if self._finished:
                return
            words = result.words
            if end - commit_t > commit_after:
                keep = [word for word in words if word.start < end - self.COMMIT_GAP_S]
                # Commit only up to a word that stays open: its start is the new commit point.
                if keep and len(keep) < len(words):
                    self._committed += keep
                    self._commit_t = words[len(keep)].start
                    words = words[len(keep):]
            self._open_words = words
            self._round_end = end
            self._rounds += 1
            self._retries += result.retries
            self._busy_s += time.perf_counter() - round_started
            text = _join(self._committed + self._open_words)
        if self._on_text and text:
            try:
                self._on_text(text)
            except Exception as error:
                self.logger.debug(f"Live text callback failed: {error}")

    def _run(self):
        done = 0
        while not self._stop.is_set():
            heard = self._drain()
            if heard - done < self._round_plan(heard)[0] * SAMPLE_RATE:
                self._stop.wait(self.WORKER_POLL_S)
                continue
            done = heard
            try:
                self.step(heard)
            except Exception as error:
                # finish() sees the flag and the caller decodes the whole recording.
                self.logger.error(f"Live transcription round failed: {error}", exc_info=True)
                with self._lock:
                    self._failed = True
                return

    # ── Key-up ──

    # The final transcript, or None when live transcription failed (the caller then
    # decodes the whole recording). A round still running is not waited for: its
    # results are dropped. Call after the recorder stopped feeding.
    def finish(self) -> Optional[str]:
        started = time.perf_counter()
        with self._lock:
            self._finished = True
            failed = self._failed
            committed, open_words = list(self._committed), list(self._open_words)
            commit_t, round_end = self._commit_t, self._round_end
        self._stop.set()
        if failed:
            return None
        heard = self._drain(last=True)
        # Keep open words before the cut; decode the rest again with some context.
        t_cut = max(commit_t, round_end - self.COMMIT_GAP_S)
        kept = committed + [word for word in open_words if word.start < t_cut]
        try:
            result = decode_after(self._decode_window, self._audio, max(0.0, t_cut - self.RELEASE_CONTEXT_S),
                                  heard, kept)
        except Exception as error:
            self.logger.error(f"Live transcription key-up decode failed: {error}", exc_info=True)
            return None
        duration = heard / SAMPLE_RATE
        self.last_stats = {
            'keyup_ms': round((time.perf_counter() - started) * 1000),
            'duration_s': round(duration, 1),
            'rounds': self._rounds,
            'retries': self._retries + result.retries,
            'release_anchored': result.anchored,
            'lag_s': round(duration - round_end, 2),
            'busy_pct': round(100 * self._busy_s / duration) if duration else 0,
        }
        return _join(kept + result.words)

    # Ends the session with no result (cancel, device lost).
    def cancel(self):
        with self._lock:
            self._finished = True
        self._stop.set()
        self._pending.clear()
