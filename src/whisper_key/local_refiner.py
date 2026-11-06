# local_refiner.py
# The "Built-in" AI provider: Gemma 3 270M run in this process on onnxruntime
# (CPU, ~340 MB download, no Ollama or Apple Intelligence needed). It keeps the
# KV cache of the fixed prompt start, so each polish only processes the dictation.
# Experimental: the stock model is weak until the fine-tuned model replaces it.

import logging
import os
import threading
import time
from typing import List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# ── Model files ──────────────────────────────────────────────────────────────

REPO_ID = 'onnx-community/gemma-3-270m-it-ONNX'
LABEL = 'Built-in AI model (Gemma 3 270M)'
MODEL_FILE = 'onnx/model_q4.onnx'
FILES = [MODEL_FILE, 'onnx/model_q4.onnx_data', 'tokenizer.json']

# Gemma chat format and token ids.
BOS_ID = 2
STOP_IDS = frozenset((1, 106))            # <eos>, <end_of_turn>
TURN_START = '<start_of_turn>user\n'
TURN_END = '<end_of_turn>\n<start_of_turn>model\n'
NUM_THREADS = 4
# Output cap: a clean-up is about as long as its input. The cap stops a model
# that loops before it costs seconds.
OUTPUT_CAP_FACTOR = 1.5
OUTPUT_CAP_EXTRA_TOKENS = 32


# Plain files in the app data folder, not the shared HuggingFace cache: in the cache
# the model and its external data are in different blob folders, and onnxruntime
# refuses to load that.
def model_folder() -> str:
    from .utils import get_user_app_data_path
    return os.path.join(get_user_app_data_path(), 'builtin_ai')


# The model folder when every file is there, else None.
def downloaded_folder() -> Optional[str]:
    folder = model_folder()
    if all(os.path.exists(os.path.join(folder, name)) for name in FILES):
        return folder
    return None


# ── Refiner ──────────────────────────────────────────────────────────────────

class LocalRefiner:
    def __init__(self):
        # One generate at a time; also guards load and unload.
        self._lock = threading.Lock()
        self._session = None
        self._tokenizer = None
        self._kv_names: List[str] = []
        # KV of the last prompt start: (text, token ids, kv arrays).
        self._prefix = None
        self._download_thread = None
        # A model that failed to load is not retried each dictation (log spam, delay).
        self._load_failed = False
        self._last_used = time.monotonic()

    # ── Download and load ──

    def is_downloaded(self) -> bool:
        return downloaded_folder() is not None

    def is_downloading(self) -> bool:
        return self._download_thread is not None and self._download_thread.is_alive()

    # Starts the download on a background thread once; the app keeps the plain
    # text until it finishes.
    def download_in_background(self):
        if self.is_downloading() or self.is_downloaded():
            return
        def run():
            from .model_download import download_snapshot
            try:
                logger.info(f"Downloading {LABEL}")
                download_snapshot(REPO_ID, LABEL, allow_patterns=FILES, local_dir=model_folder())
                logger.info(f"{LABEL} downloaded")
            except Exception as error:
                logger.error(f"{LABEL} download failed: {error}")
        self._download_thread = threading.Thread(target=run, daemon=True, name='builtin-ai-download')
        self._download_thread.start()

    # Loads the model when downloaded. False when it is not there or fails to load.
    # Caller holds _lock.
    def _ensure_loaded(self) -> bool:
        if self._session is not None:
            return True
        if self._load_failed:
            return False
        folder = downloaded_folder()
        if folder is None:
            return False
        import onnxruntime as ort
        from tokenizers import Tokenizer
        started = time.monotonic()
        options = ort.SessionOptions()
        options.intra_op_num_threads = NUM_THREADS
        try:
            self._session = ort.InferenceSession(os.path.join(folder, MODEL_FILE), options,
                                                 providers=['CPUExecutionProvider'])
            self._tokenizer = Tokenizer.from_file(os.path.join(folder, 'tokenizer.json'))
        except Exception as error:
            logger.error(f"{LABEL} failed to load: {error}")
            self._session = None
            self._load_failed = True
            return False
        self._kv_names = [i.name for i in self._session.get_inputs() if i.name.startswith('past_key_values.')]
        logger.info(f"{LABEL} loaded in {(time.monotonic() - started) * 1000:.0f} ms")
        return True

    # Loads the model and caches `stable_prefix` so the next generate starts warm.
    # Called on a background thread at hotkey press.
    def prewarm(self, stable_prefix: str = ''):
        with self._lock:
            if self._ensure_loaded():
                self._prefix_state(stable_prefix.rstrip())   # same key as generate()
                self._last_used = time.monotonic()

    def unload_if_idle(self, idle_seconds: float) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if self._session is None or time.monotonic() - self._last_used < idle_seconds:
                return False
            self._session = self._tokenizer = self._prefix = None
        finally:
            self._lock.release()
        logger.info(f"{LABEL} unloaded after idle")
        return True

    # ── Generation ──

    # One forward pass: greedy next-token ids for each input position, and the new KV.
    def _forward(self, ids: Sequence[int], kv: list, past: int):
        feeds = {'input_ids': np.array([list(ids)], dtype=np.int64),
                 'attention_mask': np.ones((1, past + len(ids)), dtype=np.int64)}
        feeds.update(zip(self._kv_names, kv))
        outputs = self._session.run(None, feeds)
        return outputs[0][0].argmax(-1), outputs[1:]

    # (token ids, kv) of the prompt start, computed once per prompt text.
    def _prefix_state(self, stable_prefix: str):
        if self._prefix is None or self._prefix[0] != stable_prefix:
            ids = [BOS_ID] + self._tokenizer.encode(TURN_START + stable_prefix, add_special_tokens=False).ids
            empty = [np.zeros((1, 1, 0, 256), dtype=np.float32)] * len(self._kv_names)
            self._prefix = (stable_prefix, ids, self._forward(ids, empty, 0)[1])
        return self._prefix[1], self._prefix[2]

    # The model's answer to `prompt`, or '' when the model is not downloaded or
    # fails. `stable_prefix` is the start of `prompt` that stays the same between
    # calls (the instructions); its KV is cached.
    def generate(self, prompt: str, stable_prefix: str = '') -> str:
        # A space at the end of the cached part would be its own token, but in the full
        # prompt it joins the next word ("Dictation: so"). Keep it in the body so the
        # tokens, and so the output, match a run without the cache.
        stable_prefix = stable_prefix.rstrip()
        if not prompt.startswith(stable_prefix):
            stable_prefix = ''
        with self._lock:
            if not self._ensure_loaded():
                if not self._load_failed:
                    self.download_in_background()
                return ''
            try:
                return self._generate(prompt[len(stable_prefix):], stable_prefix)
            except Exception as error:
                logger.error(f"{LABEL} generate failed: {error}", exc_info=True)
                return ''
            finally:
                self._last_used = time.monotonic()

    def _generate(self, body: str, stable_prefix: str) -> str:
        prefix_ids, kv = self._prefix_state(stable_prefix)
        body_ids = self._tokenizer.encode(body + TURN_END, add_special_tokens=False).ids
        cap = int(OUTPUT_CAP_FACTOR * len(body_ids) + OUTPUT_CAP_EXTRA_TOKENS)
        predictions, kv = self._forward(body_ids, kv, len(prefix_ids))
        past = len(prefix_ids) + len(body_ids)
        next_id, output = int(predictions[-1]), []
        while next_id not in STOP_IDS and len(output) < cap:
            output.append(next_id)
            predictions, kv = self._forward([next_id], kv, past)
            past += 1
            next_id = int(predictions[-1])
        if len(output) >= cap:
            logger.warning(f"{LABEL} hit the output cap ({cap} tokens)")
        return self._tokenizer.decode(output).strip()


# One refiner for the app: the model loads once and stays until idle unload.
_refiner = LocalRefiner()


def get_refiner() -> LocalRefiner:
    return _refiner
