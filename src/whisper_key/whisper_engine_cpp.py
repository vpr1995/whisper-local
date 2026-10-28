# whisper_engine_cpp.py
# Opt-in whisper.cpp backend (pip install 'whisper-local[whispercpp]'), selected
# via whisper.backend: whisper_cpp. Mirrors WhisperEngine's public API so the rest
# of the app is backend-agnostic. Better on Apple Silicon; uses GGUF models.

import gc
import logging
import os
import threading
import time
from typing import Callable, Optional

import numpy as np


class WhisperEngineCpp:
    def __init__(self,
                 model_key: str = "base.en",
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

        try:
            from pywhispercpp.model import Model
        except ImportError as e:
            raise RuntimeError(
                "whisper.cpp backend requested but pywhispercpp is not installed. "
                "Install with: pip install 'whisper-local[whispercpp]' "
                "or pip install pywhispercpp"
            ) from e

        self.model_key = model_key
        self.device = device
        self.compute_type = compute_type
        self.language = None if language in (None, 'auto') else language
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt or None
        self.hotwords = hotwords or []
        self.task = task if task in ('transcribe', 'translate') else 'transcribe'
        self.log_transcriptions = log_transcriptions
        self.vad_manager = vad_manager
        self.registry = model_registry
        self.logger = logging.getLogger(__name__)
        self.model = None
        self._loading_thread = None
        self._progress_callback = None
        # Same idle unload contract as WhisperEngine (see idle_unload.py).
        self._model_lock = threading.RLock()
        self._last_used = time.monotonic()
        # Called with no arguments after an idle unload or the reload that
        # follows it, so the UI can show/clear the "sleeping" tray icon.
        self.on_model_state_change = None
        self.last_error = None
        # Same contract as WhisperEngine: see dictation_languages.py.
        from .dictation_languages import normalize_languages
        self.allowed_languages = normalize_languages(languages)
        self.last_language_outsider = None

        self._Model = Model
        n_threads = max(1, (os.cpu_count() or 4) - 1)
        self._n_threads = n_threads

        self._load_model()

    def _load_model(self):
        print(f"🧠 Loading whisper.cpp model [{self.model_key}]...")
        print("   (Model files auto-download from ggerganov/whisper.cpp on first use)")
        try:
            self.model = self._create_model(self.model_key)
            print(f"   ✓ whisper.cpp model [{self.model_key}] ready! (threads={self._n_threads})")
            self._warmup()
        except Exception as e:
            self.logger.error(f"Failed to load whisper.cpp model '{self.model_key}': {e}")
            raise

    def _create_model(self, model_key: str):
        return self._Model(model_key, n_threads=self._n_threads)

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
        print(f"💤 whisper.cpp model [{self.model_key}] unloaded after "
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
                print(f"🧠 Reloading whisper.cpp model [{self.model_key}]...")
                self.model = self._create_model(self.model_key)
                self._notify_model_state_change()
                return True
            except Exception as e:
                self.last_error = f"model reload failed: {e}"
                self.logger.error(f"Failed to reload whisper.cpp model: {e}", exc_info=True)
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

    # Mirror of WhisperEngine._language_from_allowed, using pywhispercpp's
    # auto_detect_language(), which returns probabilities for every language.
    def _language_from_allowed(self, audio_data):
        from .dictation_languages import choose_language
        if len(self.allowed_languages) == 1:
            return self.allowed_languages[0]
        try:
            _best, probabilities = self.model.auto_detect_language(audio_data)
        except Exception as e:
            self.logger.debug(f"Language detection failed, using auto: {e}")
            return None
        language, outsider = choose_language(dict(probabilities), self.allowed_languages)
        self.last_language_outsider = outsider
        return language

    def _warmup(self):
        try:
            silent = np.zeros(16000, dtype=np.float32)
            t0 = time.time()
            self.model.transcribe(silent)
            self.logger.info(f"whisper.cpp warmup in {time.time() - t0:.2f}s")
        except Exception as e:
            self.logger.debug(f"whisper.cpp warmup skipped: {e}")

    # Same contract as WhisperEngine.set_hotwords.
    def set_hotwords(self, hotwords: Optional[list]):
        self.hotwords = hotwords or []

    def transcribe_audio(self,
                         audio_data: Optional[np.ndarray]) -> Optional[str]:
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

            if len(audio_data.shape) > 1:
                audio_data = audio_data.flatten()
            audio_data = audio_data.astype(np.float32)

            # Always pass both: pywhispercpp defaults language to 'en' (which
            # translates any other language), and it keeps params set by a
            # previous call, so a per-app translate would stick otherwise.
            self.last_language_outsider = None
            language = self.language
            if language is None and self.allowed_languages:
                language = self._language_from_allowed(audio_data)
            kwargs = {
                'language': language or 'auto',
                'translate': self.task == 'translate',
            }

            t0 = time.time()
            segments = self.model.transcribe(audio_data, **kwargs)

            parts = []
            for s in segments:
                text_chunk = getattr(s, 'text', None) or (s if isinstance(s, str) else '')
                parts.append(text_chunk.strip())
            # whisper.cpp segments carry no leading space, unlike faster-whisper's
            transcribed_text = ' '.join(p for p in parts if p)

            elapsed = time.time() - t0
            print(f"   ✓ Transcription completed in {elapsed:.1f} seconds")

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

    def change_model(self,
                      new_model_key: str,
                      progress_callback: Optional[Callable[[str], None]] = None) -> bool:
        if new_model_key == self.model_key:
            return False
        try:
            if progress_callback:
                progress_callback(f"Loading whisper.cpp model {new_model_key}...")
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
