# rolling_recognizer.py
# Live preview from an offline model (Moonshine v2 via sherpa-onnx). Offline
# models are far more accurate on fast speech than small streaming models, so we
# re-decode the current phrase a few times a second on a worker thread, and a
# pause closes the phrase. Mirrors StreamingRecognizer's API, so
# ContinuousStreamingRecognizer drives either one.
import logging
import os
import threading
import time
import wave
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import soxr

if TYPE_CHECKING:
    from .model_registry import ModelRegistry

SAMPLE_RATE = 16000


class RollingRecognizer:
    # ── Tuning ────────────────────────────────────────────────────────────────
    DECODE_INTERVAL_SECONDS = 0.35  # how often the preview refreshes
    ENDPOINT_SILENCE_SECONDS = 0.7  # a pause this long closes the phrase
    MIN_PHRASE_SECONDS = 0.5
    MAX_PHRASE_SECONDS = 20.0       # bounds decode cost (≈0.5 s at 20 s of audio)
    SPEECH_RMS = 0.008              # a chunk above this counts as speech
    # Silence is measured against the phrase's own loudness, so a loud mic and a
    # quiet one both close phrases at their pauses.
    SILENCE_RATIO = 0.2
    SILENCE_RMS_MIN = 0.004
    SILENCE_RMS_MAX = 0.02
    NUM_THREADS = 2                 # leave cores free for the final Whisper pass

    def __init__(self, model_type: str, recording_rate: int = 16000,
                 model_registry: "ModelRegistry" = None):
        self.logger = logging.getLogger(__name__)
        self.model_type = model_type
        self.recording_rate = recording_rate
        self.model_registry = model_registry
        self.recognizer = None

        self._lock = threading.Lock()
        self._audio_arrived = threading.Event()
        self._worker = None
        self._clear_phrase_state()
        self._partial_text = ''
        self._final_text = ''
        self._endpoint_pending = False
        # Bumped whenever buffered audio is discarded, so a decode that was
        # running at the time drops its now-stale result.
        self._generation = 0

    # ── Model lifecycle ───────────────────────────────────────────────────────

    def load_model(self) -> bool:
        try:
            import sherpa_onnx
        except ImportError:
            self.logger.warning("sherpa-onnx not installed, streaming recognition unavailable")
            return False
        if not self.model_registry:
            return False

        result = self.model_registry.get_streaming_model_path(self.model_type)
        if not result:
            self.logger.warning(f"Streaming model '{self.model_type}' not available")
            return False
        model_path, files = result
        paths = {name: os.path.join(model_path, files[name])
                 for name in ('encoder', 'decoder', 'tokens')}
        for name, path in paths.items():
            if not os.path.exists(path):
                self.logger.warning(f"Missing streaming model file: {name}")
                return False

        self.recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine_v2(
            encoder=paths['encoder'], decoder=paths['decoder'], tokens=paths['tokens'],
            num_threads=self.NUM_THREADS)
        self._worker = threading.Thread(target=self._decode_loop, daemon=True,
                                        name='rolling-recognizer')
        self._worker.start()
        self.logger.info(f"Rolling recognizer loaded: {self.model_type}")
        return True

    # The first decode pays one-time setup costs; take them before the user
    # starts talking.
    def warmup(self) -> bool:
        if not self.is_loaded():
            return False
        warmup_file = Path(__file__).parent / "assets" / "sounds" / "streaming-recognizer-warmup.wav"
        if not warmup_file.exists():
            return False
        with wave.open(str(warmup_file), 'rb') as wf:
            rate, channels = wf.getframerate(), wf.getnchannels()
            audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        audio = audio.astype(np.float32) / 32768.0
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        if rate != SAMPLE_RATE:
            audio = soxr.resample(audio, rate, SAMPLE_RATE).astype(np.float32)
        self.logger.info(f"Warmup complete, recognized: '{self._decode(audio)}'")
        return True

    def is_loaded(self) -> bool:
        return self.recognizer is not None

    def set_recording_rate(self, rate: int) -> None:
        self.recording_rate = rate

    # ── Audio in (audio callback thread: must stay cheap) ────────────────────

    def process_chunk(self, audio_chunk: np.ndarray) -> None:
        if not self.is_loaded():
            return
        samples = audio_chunk.flatten().astype(np.float32)
        if self.recording_rate != SAMPLE_RATE:
            samples = soxr.resample(samples, self.recording_rate, SAMPLE_RATE).astype(np.float32)
        if samples.size == 0:
            return
        rms = float(np.sqrt(np.mean(samples * samples)))
        chunk_seconds = samples.size / SAMPLE_RATE

        with self._lock:
            self._phrase_chunks.append(samples)
            self._phrase_samples += samples.size
            silence_rms = min(self.SILENCE_RMS_MAX,
                              max(self.SILENCE_RMS_MIN, self._phrase_peak_rms * self.SILENCE_RATIO))
            if rms >= self.SPEECH_RMS:
                self._speech_seen = True
            if rms > silence_rms:
                self._trailing_silence = 0.0
                self._phrase_peak_rms = max(self._phrase_peak_rms, rms)
            else:
                self._trailing_silence += chunk_seconds
        self._audio_arrived.set()

    # ── Results out (read by ContinuousStreamingRecognizer) ──────────────────

    def get_partial_result(self) -> str:
        with self._lock:
            return self._final_text if self._endpoint_pending else self._partial_text

    def is_endpoint(self) -> bool:
        with self._lock:
            return self._endpoint_pending

    # Called after an endpoint is consumed and when a new recording starts.
    # After an endpoint the worker has already dropped the closed phrase's
    # audio, so only the flags are cleared and the next phrase's audio is kept.
    def reset(self) -> None:
        with self._lock:
            if not self._endpoint_pending:
                self._clear_phrase_state()
                self._generation += 1
            self._endpoint_pending = False
            self._final_text = ''
            self._partial_text = ''
            has_waiting_audio = self._phrase_samples > 0
        if has_waiting_audio:
            self._audio_arrived.set()

    # ── Worker ────────────────────────────────────────────────────────────────

    def _clear_phrase_state(self):
        self._phrase_chunks = []
        self._phrase_samples = 0
        self._decoded_samples = 0
        self._speech_seen = False
        self._trailing_silence = 0.0
        self._phrase_peak_rms = 0.0

    def _decode_loop(self):
        while True:
            self._audio_arrived.wait()
            self._audio_arrived.clear()
            started = time.monotonic()
            try:
                self._decode_once()
            except Exception as e:
                self.logger.warning(f"Rolling decode failed: {e}")
            # Pace refreshes; audio that arrives meanwhile waits for the next pass.
            time.sleep(max(0.0, self.DECODE_INTERVAL_SECONDS - (time.monotonic() - started)))

    def _decode_once(self):
        with self._lock:
            if self._endpoint_pending:
                return  # reset() wakes us once the endpoint is consumed
            if not self._speech_seen:
                self._drop_leading_silence()
                return
            if self._phrase_samples == self._decoded_samples:
                return
            phrase_seconds = self._phrase_samples / SAMPLE_RATE
            closes_phrase = (
                (self._trailing_silence >= self.ENDPOINT_SILENCE_SECONDS
                 and phrase_seconds >= self.MIN_PHRASE_SECONDS)
                or phrase_seconds >= self.MAX_PHRASE_SECONDS)
            chunk_count = len(self._phrase_chunks)
            audio = np.concatenate(self._phrase_chunks)
            generation = self._generation

        text = self._decode(audio)

        with self._lock:
            if generation != self._generation:
                return
            self._decoded_samples = audio.size
            if closes_phrase:
                # Keep any audio that arrived during the decode: it belongs to
                # the next phrase.
                remaining = self._phrase_chunks[chunk_count:]
                self._clear_phrase_state()
                self._phrase_chunks = remaining
                self._phrase_samples = sum(c.size for c in remaining)
                if text:
                    self._final_text = text
                    self._endpoint_pending = True
                self._partial_text = ''
            else:
                self._partial_text = text

    # Before any speech, keep only a short tail so the phrase starts near the
    # first word instead of carrying seconds of room noise.
    def _drop_leading_silence(self):
        keep_samples = int(0.3 * SAMPLE_RATE)
        while self._phrase_chunks and self._phrase_samples - self._phrase_chunks[0].size >= keep_samples:
            self._phrase_samples -= self._phrase_chunks.pop(0).size
        self._decoded_samples = 0

    def _decode(self, audio: np.ndarray) -> str:
        stream = self.recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio)
        self.recognizer.decode_stream(stream)
        return stream.result.text.strip()
