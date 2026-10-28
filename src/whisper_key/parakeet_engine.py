# parakeet_engine.py
# Opt-in NVIDIA Parakeet TDT v3 backend (sherpa-onnx, already a core dependency),
# selected via whisper.backend: parakeet. Mirrors WhisperEngine's public API so
# the rest of the app is backend-agnostic. About 3x faster than Whisper small on
# CPU (7x on short clips); it auto-detects 25 European languages, cannot
# translate or take a prompt, and biases toward whisper.hotwords through
# sherpa-onnx's beam search.

import gc
import logging
import os
import tempfile
import threading
import time
from typing import Callable, Optional

import numpy as np

SAMPLE_RATE = 16000

# How hard hotwords are pushed. Tuned on TTS clips: 3.0-4.5 fixed names
# ("Pradvi" -> "Prudhvi") with no false hits on ordinary sentences.
HOTWORDS_SCORE = 3.5


class ParakeetEngine:
    # Parakeet scaled past 4 threads in benchmarks, but 4 leaves cores free for
    # the live-preview model and the rest of the app.
    NUM_THREADS = 4

    # Same signature as WhisperEngine so engine_factory can build any backend.
    # Whisper-only knobs (beam size, prompt, compute type) are accepted and
    # ignored.
    def __init__(self,
                 model_key: str = "parakeet-v3",
                 device: str = "cpu",
                 compute_type: str = "int8",
                 language: Optional[str] = None,
                 beam_size: int = 5,
                 initial_prompt: str = "",
                 hotwords: Optional[list] = None,
                 task: str = "transcribe",
                 vad_manager=None,
                 model_registry=None,
                 log_transcriptions: bool = False,
                 languages: Optional[list] = None):

        self.model_key = model_key
        self.device = device
        self.compute_type = compute_type
        self.language = None if language in (None, 'auto') else language
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt or None
        self.hotwords = _clean_hotwords(hotwords)
        self.task = task if task in ('transcribe', 'translate') else 'transcribe'
        self.log_transcriptions = log_transcriptions
        self.vad_manager = vad_manager
        self.registry = model_registry
        self.logger = logging.getLogger(__name__)
        self.model = None
        # Same idle unload contract as WhisperEngine (see idle_unload.py).
        self._model_lock = threading.RLock()
        self._last_used = time.monotonic()
        self.on_model_state_change = None
        self.last_error = None
        # Parakeet picks the language itself, so there is never an outsider to flag.
        self.allowed_languages = []
        self.last_language_outsider = None
        self._translate_warned = False

        self._load_model()

    # ── Model lifecycle ───────────────────────────────────────────────────────

    def _load_model(self):
        print(f"🧠 Loading Parakeet model [{self.model_key}]...")
        try:
            self.model = self._create_model(self.model_key)
            print(f"   ✓ Parakeet model [{self.model_key}] ready! (CPU, threads={self.NUM_THREADS})")
            if self.hotwords:
                print(f"   ✓ Biasing toward {len(self.hotwords)} dictionary words")
            if self.initial_prompt:
                print("   ℹ Parakeet ignores initial_prompt; hotwords and corrections still apply")
            self._warmup()
        except Exception as e:
            self.logger.error(f"Failed to load Parakeet model '{self.model_key}': {e}")
            raise

    # Resolves (and on first use downloads) the model files, then builds the
    # sherpa-onnx recognizer.
    def _create_model(self, model_key: str):
        import sherpa_onnx
        resolved = self.registry.get_model_files_path(model_key) if self.registry else None
        if not resolved:
            raise RuntimeError(f"Parakeet model '{model_key}' is not defined or failed to download")
        folder, files = resolved
        paths = {role: os.path.join(folder, files[role])
                 for role in ('encoder', 'decoder', 'joiner', 'tokens')}
        missing = [role for role, path in paths.items() if not os.path.exists(path)]
        if missing:
            raise RuntimeError(f"Parakeet model files missing: {', '.join(missing)}")
        # Hotwords need beam search; without any, greedy is faster and identical.
        hotword_options = dict(
            decoding_method="modified_beam_search", hotwords_score=HOTWORDS_SCORE,
            modeling_unit="bpe", bpe_vocab=_bpe_vocab_for(paths['tokens']),
        ) if self.hotwords else {}
        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=paths['encoder'], decoder=paths['decoder'], joiner=paths['joiner'],
            tokens=paths['tokens'], model_type="nemo_transducer", num_threads=self.NUM_THREADS,
            **hotword_options)

    def _warmup(self):
        try:
            t0 = time.time()
            self._decode(np.zeros(SAMPLE_RATE, dtype=np.float32))
            self.logger.info(f"Parakeet warmup in {time.time() - t0:.2f}s")
        except Exception as e:
            self.logger.debug(f"Parakeet warmup skipped: {e}")

    # ── Idle unload / on-demand reload (mirrors WhisperEngine) ──────────────

    def unload_if_idle(self, idle_seconds: float) -> bool:
        if not self._model_lock.acquire(blocking=False):
            return False
        try:
            if self.model is None:
                return False
            idle_for = time.monotonic() - self._last_used
            if idle_for < idle_seconds:
                return False
            self.model = None
            gc.collect()
        finally:
            self._model_lock.release()
        print(f"💤 Parakeet model [{self.model_key}] unloaded after "
              f"{idle_for / 60:.1f} min idle to free memory; it reloads on your next recording")
        self.logger.info(f"Model unloaded after {idle_for:.0f}s idle")
        self._notify_model_state_change()
        return True

    def ensure_model_loaded(self) -> bool:
        with self._model_lock:
            self._last_used = time.monotonic()
            if self.model is not None:
                return True
            try:
                print(f"🧠 Reloading Parakeet model [{self.model_key}]...")
                self.model = self._create_model(self.model_key)
                self._notify_model_state_change()
                return True
            except Exception as e:
                self.last_error = f"model reload failed: {e}"
                self.logger.error(f"Failed to reload Parakeet model: {e}", exc_info=True)
                return False

    @property
    def is_model_loaded(self) -> bool:
        return self.model is not None

    def _notify_model_state_change(self):
        if self.on_model_state_change:
            try:
                self.on_model_state_change()
            except Exception as e:
                self.logger.debug(f"Model state callback failed: {e}")

    def preload_model_async(self):
        if self.model is not None:
            self._last_used = time.monotonic()
            return
        threading.Thread(target=self.ensure_model_loaded, daemon=True,
                         name='model-reload').start()

    # ── Transcription ─────────────────────────────────────────────────────────

    # Dictionary words, refreshed before each transcription (state_manager).
    # Words only need a new list per recording, but going from none to some
    # (or back) switches the decoding method, so the recognizer is rebuilt on
    # the next recording.
    def set_hotwords(self, hotwords: Optional[list]):
        cleaned = _clean_hotwords(hotwords)
        with self._model_lock:
            if bool(cleaned) != bool(self.hotwords):
                self.model = None
            self.hotwords = cleaned

    def transcribe_audio(self, audio_data: Optional[np.ndarray]) -> Optional[str]:
        if audio_data is None or len(audio_data) == 0:
            self.logger.warning("No audio data to transcribe")
            return None

        with self._model_lock:
            self.last_error = None
            if not self.ensure_model_loaded():
                return None
            try:
                return self._transcribe_with_loaded_model(audio_data)
            finally:
                self._last_used = time.monotonic()

    # The decode itself; caller holds _model_lock with the model loaded.
    def _transcribe_with_loaded_model(self, audio_data: np.ndarray) -> Optional[str]:
        try:
            if self.vad_manager and self.vad_manager.is_available():
                if not self.vad_manager.check_audio_for_speech(audio_data):
                    print("   ✗ No speech detected, skipping transcription")
                    return None

            if self.task == 'translate' and not self._translate_warned:
                print("   ℹ Parakeet cannot translate; transcribing in the spoken language")
                self._translate_warned = True

            t0 = time.time()
            transcribed_text = self._decode(audio_data.flatten().astype(np.float32))
            print(f"   ✓ Transcription completed in {time.time() - t0:.1f} seconds")

            if self.log_transcriptions:
                self.logger.info(f"Transcribed: '{transcribed_text}'")
            else:
                self.logger.info(f"Transcribed {len(transcribed_text)} chars")
            return transcribed_text or None

        except Exception as e:
            self.last_error = str(e) or e.__class__.__name__
            self.logger.error(f"Transcription failed: {e}", exc_info=True)
            print(f"❌ Transcription failed: {e}")
            return None

    def _decode(self, audio: np.ndarray) -> str:
        stream = (self.model.create_stream("\n".join(self.hotwords)) if self.hotwords
                  else self.model.create_stream())
        stream.accept_waveform(SAMPLE_RATE, audio)
        self.model.decode_stream(stream)
        return stream.result.text.strip()

    # ── Model switching (tray menu) ───────────────────────────────────────────

    def change_model(self,
                     new_model_key: str,
                     progress_callback: Optional[Callable[[str], None]] = None) -> bool:
        if new_model_key == self.model_key:
            return False
        try:
            if progress_callback:
                progress_callback(f"Loading Parakeet model {new_model_key}...")
            with self._model_lock:
                self.model = self._create_model(new_model_key)
                self.model_key = new_model_key
                self._warmup()
            if progress_callback:
                progress_callback(f"Model {new_model_key} ready")
            return True
        except Exception as e:
            self.logger.error(f"Model change failed: {e}")
            if progress_callback:
                progress_callback(f"Model change failed: {e}")
            return False

    def is_loading(self) -> bool:
        return False


def _clean_hotwords(hotwords: Optional[list]) -> list:
    return [str(word).strip() for word in (hotwords or []) if str(word).strip()]


# sherpa-onnx splits hotwords into tokens with a SentencePiece vocab file, which
# the Parakeet export lacks. tokens.txt has the same pieces in merge order, so
# "piece<TAB>-rank" rebuilds it. Written once next to the model (or to the temp
# folder when the model folder is read-only); returns its path.
def _bpe_vocab_for(tokens_path: str) -> str:
    for folder in (os.path.dirname(tokens_path), tempfile.gettempdir()):
        vocab_path = os.path.join(folder, "bpe.vocab")
        if os.path.exists(vocab_path):
            return vocab_path
        try:
            with open(tokens_path, encoding="utf-8") as tokens, \
                 open(vocab_path, "w", encoding="utf-8") as vocab:
                for line in tokens:
                    piece, _, rank = line.rstrip("\n").rpartition(" ")
                    vocab.write(f"{piece}\t{-int(rank)}\n")
            return vocab_path
        except OSError:
            continue
    raise RuntimeError("Could not write bpe.vocab for Parakeet hotwords")
