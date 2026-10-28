# ai_provider.py
# One front door for every AI text step (per-dictation polish, transforms, rephrase).
# Picks Apple Intelligence (on-device, macOS 26+) or Ollama from `postprocess.ai.provider`
# and guards polish against the model rewriting or answering the dictation.

import json
import logging
import re
import threading
import urllib.error
import urllib.request
from collections import Counter

from .utils import OnDeviceAIError

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

PROVIDER_AUTO = 'auto'
PROVIDER_APPLE = 'apple_intelligence'
PROVIDER_OLLAMA = 'ollama'

PROVIDER_LABELS = {PROVIDER_APPLE: 'Apple Intelligence', PROVIDER_OLLAMA: 'Ollama'}

DEFAULT_POLISH_PROMPT = (
    "Polish this dictation. Fix punctuation and capitalization only. Do not change wording or add "
    "anything. Output ONLY the polished text:\n\n{text}")
DEFAULT_OLLAMA_ENDPOINT = 'http://localhost:11434'
DEFAULT_OLLAMA_MODEL = 'llama3.2'
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 5

# Apple's model has no per-call timeout setting, so the app picks one: short for
# polish (a hang would delay every dictation), longer for a deliberate rewrite.
APPLE_POLISH_TIMEOUT_SECONDS = 10
APPLE_REWRITE_TIMEOUT_SECONDS = 30

# Polish safety. Longer dictations are left alone: the model gets slower and drifts more.
MAX_POLISH_CHARACTERS = 1200
MIN_PRESERVED_WORD_FRACTION = 0.8      # of the raw words that must survive in the output
MAX_ADDED_WORD_FRACTION = 0.1          # new words allowed, as a fraction of the raw word count
MAX_OUTPUT_TO_INPUT_WORD_RATIO = 1.5

# Why Apple Intelligence is unavailable, in words the user can act on. The single
# place these live: the dashboard, the doctor and the logs all read this dict.
APPLE_REASON_TEXT = {
    'apple_intelligence_off': "Apple Intelligence is turned off in System Settings",
    'device_not_eligible': "This Mac does not support Apple Intelligence",
    'model_not_ready': "Apple Intelligence is still downloading its model",
    'needs_macos_26': "Apple Intelligence needs macOS 26 or later",
    'needs_apple_silicon': "Apple Intelligence needs a Mac with Apple silicon",
    'needs_command_line_tools': "Apple Intelligence setup needs Apple's Command Line Tools (run xcode-select --install)",
    'not_built': "Apple Intelligence is getting ready",
    'build_failed': "Apple Intelligence setup failed (see the log)",
    'not_macos': "Apple Intelligence is only on Macs",
    'unknown': "Apple Intelligence is not available",
}


# ── Apple Intelligence access ────────────────────────────────────────────────

# The platform module, or None on an OS that has none (Linux CI).
def _apple_module():
    try:
        from .platform import apple_intelligence
        return apple_intelligence
    except ImportError:
        return None


# Cheap (cached) answer: (is Apple Intelligence usable now, reason code).
# `force_refresh` asks again instead of using the cached answer.
def apple_availability(force_refresh: bool = False) -> tuple:
    module = _apple_module()
    return module.availability(force_refresh) if module else (False, 'not_macos')


# Compiles the Apple Intelligence helper in the background so it is ready before the
# first AI step. Never called from the dictation path.
def prepare_in_background():
    module = _apple_module()
    if module is not None:
        threading.Thread(target=module.prepare, name='apple-intelligence-prepare', daemon=True).start()


# ── Provider resolution ──────────────────────────────────────────────────────

_warned_apple_unavailable = set()   # reasons already logged, so a dictation loop doesn't spam


def _ai_settings(config) -> dict:
    ai_settings = (config or {}).get('ai')
    return ai_settings if isinstance(ai_settings, dict) else {}


def _ollama_settings(config) -> dict:
    ollama_settings = (config or {}).get('ollama')
    return ollama_settings if isinstance(ollama_settings, dict) else {}


# 'apple_intelligence' or 'ollama': what will answer right now for this config.
# `auto` and an explicit Apple choice both fall back to Ollama when Apple is not usable.
def active_provider(config) -> str:
    setting = _ai_settings(config).get('provider', PROVIDER_AUTO)
    if setting == PROVIDER_OLLAMA:
        return PROVIDER_OLLAMA
    available, reason = apple_availability()
    if available:
        return PROVIDER_APPLE
    if setting == PROVIDER_APPLE and reason not in _warned_apple_unavailable:
        _warned_apple_unavailable.add(reason)
        logger.warning(f"{APPLE_REASON_TEXT.get(reason, reason)}; using Ollama instead")
    return PROVIDER_OLLAMA


def active_provider_label(config) -> str:
    return PROVIDER_LABELS[active_provider(config)]


# True when the user relies on Ollama: they chose it, or it answers the per-dictation
# polish. A Windows user who never turned AI on is not "using" it, so nothing nags them.
def ollama_in_use(config) -> bool:
    if _ai_settings(config).get('provider', PROVIDER_AUTO) == PROVIDER_OLLAMA:
        return True
    return bool(_ai_settings(config).get('polish')) and active_provider(config) == PROVIDER_OLLAMA


# Asks the Ollama server which models it has: (reachable, model names). Shared by the
# doctor and the dashboard checklist; `timeout_seconds` overrides the configured one.
def probe_ollama(config, timeout_seconds=None) -> tuple:
    ollama_settings = _ollama_settings(config)
    endpoint = (ollama_settings.get('endpoint') or DEFAULT_OLLAMA_ENDPOINT).rstrip('/')
    timeout = float(timeout_seconds if timeout_seconds is not None
                    else ollama_settings.get('timeout', DEFAULT_OLLAMA_TIMEOUT_SECONDS))
    try:
        with urllib.request.urlopen(f"{endpoint}/api/tags", timeout=timeout) as response:
            data = json.loads(response.read())
        return True, [model.get('name', '') for model in data.get('models', [])]
    except (urllib.error.URLError, OSError, ValueError):
        return False, []


# What the dashboard shows about the AI provider.
def describe_status(config) -> dict:
    available, reason = apple_availability()
    return {
        'provider_setting': _ai_settings(config).get('provider', PROVIDER_AUTO),
        'active_provider': active_provider(config),
        'apple_available': available,
        'apple_reason_text': '' if available else APPLE_REASON_TEXT.get(reason, APPLE_REASON_TEXT['unknown']),
    }


# Why a polish gave nothing, in one short phrase for the "Polish unavailable" notice.
# Names Apple Intelligence's reason when the app fell back from it to Ollama; the
# answering provider either did not reply in time or changed the words.
def polish_failure_reason(config) -> str:
    status = describe_status(config)
    label = PROVIDER_LABELS[status['active_provider']]
    fell_back_from_apple = (status['active_provider'] == PROVIDER_OLLAMA
                            and status['provider_setting'] != PROVIDER_OLLAMA)
    if fell_back_from_apple and status['apple_reason_text']:
        return f"{status['apple_reason_text']}, and {label} gave no usable answer"
    return f"{label} gave no usable answer"


# ── The two providers ────────────────────────────────────────────────────────

# One Ollama call. '' on any failure, so callers degrade instead of raising.
def _ollama_generate(prompt: str, ollama_settings: dict, model=None, timeout_seconds=None) -> str:
    endpoint = (ollama_settings.get('endpoint') or DEFAULT_OLLAMA_ENDPOINT).rstrip('/')
    payload = {
        'model': model or ollama_settings.get('model') or DEFAULT_OLLAMA_MODEL,
        'prompt': prompt,
        'stream': False,
    }
    try:
        timeout = float(timeout_seconds if timeout_seconds is not None
                        else ollama_settings.get('timeout', DEFAULT_OLLAMA_TIMEOUT_SECONDS))
        request = urllib.request.Request(
            f"{endpoint}/api/generate",
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
        return (data.get('response') or '').strip()
    except (urllib.error.URLError, OSError, ValueError) as error:
        logger.warning(f"Ollama unavailable ({error})")
        return ''


# One Apple Intelligence call. '' on any failure, so callers degrade instead of raising.
def _apple_generate(prompt: str, timeout_seconds: float) -> str:
    try:
        return _apple_module().generate(prompt, timeout_seconds).strip()
    except OnDeviceAIError as error:
        logger.warning(f"Apple Intelligence could not answer ({error})")
        return ''


# Sends a finished prompt to whichever provider is active.
def _generate(prompt: str, config, apple_timeout_seconds: float, model=None, timeout_seconds=None) -> str:
    if active_provider(config) == PROVIDER_APPLE:
        return _apple_generate(prompt, timeout_seconds or apple_timeout_seconds)
    return _ollama_generate(prompt, _ollama_settings(config), model, timeout_seconds)


# ── Rewrite (transforms, rephrase) ───────────────────────────────────────────

# The prompt for "rewrite this selection as the user said". Shared by both rephrase paths.
def build_rephrase_prompt(instruction: str, selection: str) -> str:
    return (f"{instruction}\n\n"
            "Output ONLY the rewritten text with no preamble, no quotes, no commentary.\n\n"
            f"Input:\n{selection}")


# Free-form rewrite with a finished prompt. Not checked against the input: the user
# asked for a rewrite on purpose. '' on any failure; never raises. `model` and
# `timeout_seconds` are per-call overrides (a transform can set them).
def rewrite(prompt: str, config, model=None, timeout_seconds=None) -> str:
    return _generate(prompt, config, APPLE_REWRITE_TIMEOUT_SECONDS, model, timeout_seconds)


# ── Polish (every dictation) ─────────────────────────────────────────────────

# Words compared without case, punctuation or apostrophes ("didnt" == "didn't").
def _comparable_words(text: str) -> list:
    return re.findall(r"\w+", text.lower().replace("'", '').replace('’', ''))


# True when the output is the same text with only formatting changes. The model
# sometimes answers the dictation, obeys it, or refuses; all three change the words.
def preserves_wording(raw_text: str, polished_text: str) -> bool:
    raw_words = _comparable_words(raw_text)
    polished_words = _comparable_words(polished_text)
    if not raw_words or not polished_words:
        return False
    shared_word_count = sum((Counter(raw_words) & Counter(polished_words)).values())
    preserved_fraction = shared_word_count / len(raw_words)
    added_word_count = len(polished_words) - shared_word_count
    return (preserved_fraction >= MIN_PRESERVED_WORD_FRACTION
            and added_word_count <= int(MAX_ADDED_WORD_FRACTION * len(raw_words))
            and len(polished_words) <= MAX_OUTPUT_TO_INPUT_WORD_RATIO * len(raw_words))


# Fixes punctuation and capitalization of one dictation. Returns '' when the caller
# should keep the raw text: too long, provider failed, or the output changed the words.
def polish(text: str, config) -> str:
    if len(text) > MAX_POLISH_CHARACTERS:
        logger.info(f"AI polish skipped: {len(text)} characters is over the {MAX_POLISH_CHARACTERS} limit")
        return ''
    prompt_template = _ai_settings(config).get('polish_prompt') or DEFAULT_POLISH_PROMPT
    # str.replace, not format(): the dictation or the prompt may contain curly braces.
    prompt = prompt_template.replace('{text}', text) if '{text}' in prompt_template \
        else f"{prompt_template}\n\n{text}"

    polished = _generate(prompt, config, APPLE_POLISH_TIMEOUT_SECONDS)
    if not polished:
        return ''
    if not preserves_wording(text, polished):
        # No text in the log: dictations are private unless the user opted into logging them.
        logger.warning(f"AI polish rejected: it changed the words ({len(text)} characters in, "
                       f"{len(polished)} out). Kept the raw text.")
        return ''
    return polished
