# platform/macos/gpu.py
# No-op GPU probe. CUDA/ROCm acceleration is Windows/Linux only; Apple Silicon
# users get their speed from the Parakeet backend instead.
# Windows mirror: platform/windows/gpu.py (the real detection).
def detect_and_print(configured_device):
    return (None, None, False)


# No CUDA backend on macOS, so there is never anything missing.
def missing_gpu_libraries():
    return []
