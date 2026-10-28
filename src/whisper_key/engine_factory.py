# engine_factory.py
# Maps whisper.backend to a transcription engine. The dictation app, the local
# server, self-test and system-audio capture all build their engine here, so a
# new backend is wired in once. Engine imports stay lazy: each backend pulls in
# a different ML runtime.

BACKENDS = ('faster_whisper', 'whisper_cpp', 'parakeet')


# Backend name -> engine class, imported only when chosen.
def engine_class(backend: str):
    if backend == 'whisper_cpp':
        from .whisper_engine_cpp import WhisperEngineCpp
        return WhisperEngineCpp
    if backend == 'parakeet':
        from .parakeet_engine import ParakeetEngine
        return ParakeetEngine
    from .whisper_engine import WhisperEngine
    return WhisperEngine


# (backend, model key) that will actually run; see ModelRegistry.resolve.
def resolve_backend(whisper_cfg: dict, model_registry) -> tuple:
    backend = whisper_cfg.get('backend', 'faster_whisper')
    model_key = whisper_cfg.get('model', 'base')
    if model_registry is None:
        return backend, model_key
    return model_registry.resolve(model_key, backend)


# Builds the configured engine. `overrides` replaces individual engine kwargs,
# e.g. self-test forces beam_size=1 and no prompt.
def create_engine(whisper_cfg: dict, vad_manager, model_registry, **overrides):
    backend, model_key = resolve_backend(whisper_cfg, model_registry)
    engine_kwargs = dict(
        model_key=model_key,
        device=whisper_cfg['device'],
        compute_type=whisper_cfg['compute_type'],
        language=whisper_cfg.get('language', 'auto'),
        beam_size=whisper_cfg.get('beam_size', 5),
        initial_prompt=whisper_cfg.get('initial_prompt', ''),
        hotwords=whisper_cfg.get('hotwords', []),
        task=whisper_cfg.get('task', 'transcribe'),
        vad_manager=vad_manager,
        model_registry=model_registry,
        languages=whisper_cfg.get('languages'),
    )
    engine_kwargs.update(overrides)
    return engine_class(backend)(**engine_kwargs)


# (problem, fix) when the backend's runtime is not importable, else None.
def missing_backend_dependency(backend: str):
    if backend == 'whisper_cpp':
        try:
            import pywhispercpp  # noqa: F401
        except ImportError:
            return ("whisper_cpp backend selected but pywhispercpp not installed",
                    "pip install 'whisper-local[whispercpp]'")
    if backend == 'parakeet':
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            return ("parakeet backend selected but sherpa-onnx not installed",
                    "pip install sherpa-onnx")
    return None
