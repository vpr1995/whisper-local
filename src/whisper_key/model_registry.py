# model_registry.py
# Single source of truth for which Whisper/streaming models exist, where each one
# resolves to (a HuggingFace repo id or a local directory), and whether it is
# already cached on disk. The cache check is what lets the app tell the user
# "downloading, first run only" instead of appearing to hang on first launch.
# Models that ship as a set of files (sherpa-onnx: streaming models and the
# Parakeet backend) resolve to a HuggingFace snapshot folder plus file names.

import logging
import os
from typing import Optional

from .utils import import_ctranslate2_without_optional_backends
# Before faster_whisper, which imports ctranslate2: see the helper for why.
import_ctranslate2_without_optional_backends()
from faster_whisper.utils import _MODELS  # noqa: E402


class ModelRegistry:
    DEFAULT_CACHE_PREFIX = "models--Systran--faster-whisper-"

    def __init__(self, whisper_models_config: dict = None, streaming_models_config: dict = None,
                 active_backend: str = "faster_whisper"):
        self.whisper_models = {}
        self.streaming_models = {}
        # The model menu only offers models the running engine can load.
        self.active_backend = active_backend
        self.logger = logging.getLogger(__name__)

        if whisper_models_config:
            for key, config in whisper_models_config.items():
                if isinstance(config, dict):
                    self.whisper_models[key] = ModelDefinition(key, config, model_type="whisper")

        if streaming_models_config:
            for key, config in streaming_models_config.items():
                if isinstance(config, dict):
                    self.streaming_models[key] = ModelDefinition(key, config, model_type="streaming")

    def get_model(self, key: str):
        return self.whisper_models.get(key)

    def get_source(self, key: str) -> str:
        model = self.get_model(key)
        return model.source if model else key

    def get_cache_folder(self, key: str) -> str:
        model = self.get_model(key)
        if not model:
            return f"{self.DEFAULT_CACHE_PREFIX}{key}"
        return model.cache_folder

    def get_models_by_group(self, group: str) -> list:
        return [m for m in self.whisper_models.values()
                if m.group == group and m.enabled and self.fits_backend(m, self.active_backend)]

    def get_groups_ordered(self) -> list:
        return ["official", "custom", "parakeet"]

    # Whisper models load in both Whisper backends; Parakeet models only in theirs.
    @staticmethod
    def fits_backend(model: "ModelDefinition", backend: str) -> bool:
        if model.backend == "parakeet":
            return backend == "parakeet"
        return backend != "parakeet"

    # (backend, model key) that will actually run. A Parakeet model always
    # runs on the Parakeet backend, so picking it in Settings is enough; a
    # backend that can't load the configured model falls back to its own
    # first model, so switching whisper.backend alone is enough too.
    def resolve(self, model_key: str, backend: str) -> tuple:
        model = self.get_model(model_key)
        if model is not None and model.backend == "parakeet":
            return "parakeet", model_key
        if model is None or self.fits_backend(model, backend):
            return backend, model_key
        for candidate in self.whisper_models.values():
            if candidate.enabled and self.fits_backend(candidate, backend):
                self.logger.warning(f"Model '{model_key}' does not run on the {backend} "
                                    f"backend; using '{candidate.key}'")
                return backend, candidate.key
        return backend, model_key

    def get_hf_cache_path(self) -> str:
        userprofile = os.environ.get('USERPROFILE')
        if userprofile:
            return os.path.join(userprofile, '.cache', 'huggingface', 'hub')
        return os.path.join(os.path.expanduser('~'), '.cache', 'huggingface', 'hub')

    def is_model_cached(self, key: str) -> bool:
        model = self.get_model(key)
        if model and model.files:
            return self._are_files_cached(model)
        if model and model.is_local_path:
            return os.path.exists(os.path.join(model.source, 'model.bin'))
        cache_folder = self.get_cache_folder(key)
        if not cache_folder:
            return False
        return os.path.exists(os.path.join(self.get_hf_cache_path(), cache_folder))

    # ── File-set models (sherpa-onnx) ─────────────────────────────────────────

    def get_streaming_model_path(self, key: str) -> Optional[tuple]:
        return self._resolve_files(self.streaming_models.get(key))

    def get_model_files_path(self, key: str) -> Optional[tuple]:
        return self._resolve_files(self.get_model(key))

    # (snapshot folder, {role: file name}), downloading the snapshot on first use.
    def _resolve_files(self, model: Optional["ModelDefinition"]) -> Optional[tuple]:
        if not model or not model.files:
            return None
        if not self._are_files_cached(model) and not self._download_snapshot(model):
            return None
        snapshot_path = self._snapshot_path(model)
        if not snapshot_path:
            return None
        return snapshot_path, model.files

    def _are_files_cached(self, model: "ModelDefinition") -> bool:
        snapshot_path = self._snapshot_path(model)
        if not snapshot_path:
            return False
        return all(os.path.exists(os.path.join(snapshot_path, name)) for name in model.files.values())

    def _snapshot_path(self, model: "ModelDefinition") -> Optional[str]:
        if model.is_local_path:
            return model.source
        snapshots_dir = os.path.join(self.get_hf_cache_path(), model.cache_folder, 'snapshots')
        if not os.path.exists(snapshots_dir):
            return None
        snapshots = os.listdir(snapshots_dir)
        if not snapshots:
            return None
        return os.path.join(snapshots_dir, snapshots[0])

    def _download_snapshot(self, model: "ModelDefinition") -> bool:
        if model.is_local_path:
            return False
        try:
            from huggingface_hub import snapshot_download
            self.logger.info(f"Downloading model: {model.source}")
            print(f"   Downloading model: {model.label} (first run only)...")
            snapshot_download(repo_id=model.source)
            self.logger.info(f"Model downloaded: {model.source}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to download model {model.source}: {e}")
            return False


class ModelDefinition:
    def __init__(self, key: str, config: dict, model_type: str = "whisper"):
        self.key = key
        self.model_type = model_type
        self.source = config.get("source", key)
        self.label = config.get("label", key.title())
        self.group = config.get("group", "custom")
        self.enabled = config.get("enabled", True)
        self.files = config.get("files", {})
        # Streaming models only: "transducer" (true streaming, sherpa-onnx
        # OnlineRecognizer) or "moonshine_v2" (offline model re-decoded live).
        self.engine = config.get("engine", "transducer")
        # Main models only: which whisper.backend can load this model.
        # "whisper" (faster_whisper / whisper_cpp) or "parakeet".
        self.backend = config.get("backend", "whisper")
        self.is_local_path = self._check_is_local_path()
        self.cache_folder = self._derive_cache_folder()

    def _check_is_local_path(self) -> bool:
        if self.source.startswith("\\\\") or (len(self.source) > 2 and self.source[1] == ":"):
            return True
        if "/" in self.source:
            return os.path.exists(self.source)
        return False

    def _derive_cache_folder(self) -> str:
        if self.is_local_path:
            return None

        if "/" in self.source:
            return "models--" + self.source.replace("/", "--")

        if self.source in _MODELS:
            repo = _MODELS[self.source]
            return "models--" + repo.replace("/", "--")

        return f"{ModelRegistry.DEFAULT_CACHE_PREFIX}{self.source}"
