# platform/windows/apple_intelligence.py
# Apple Intelligence only exists on Macs, so every call here reports "not available".
# macOS mirror: platform/macos/apple_intelligence.py (the real implementation).
from ...utils import OnDeviceAIError


# Always unavailable: the AI provider falls back to Ollama.
def availability(force_refresh: bool = False) -> tuple:
    return False, 'not_macos'


# Nothing to build on Windows.
def prepare():
    pass


# Apple's Command Line Tools are a Mac thing: nothing is missing on Windows.
def command_line_tools_installed() -> bool:
    return True


def install_command_line_tools() -> bool:
    return False


def generate(prompt: str, timeout_seconds: float) -> str:
    raise OnDeviceAIError('not_macos')
