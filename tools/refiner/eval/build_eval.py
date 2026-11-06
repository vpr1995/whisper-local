# tools/refiner/eval/build_eval.py
# Turns the hand-written rows (source_v1.py) into the frozen eval set eval_v1.jsonl:
# macOS `say` speaks each row (3 voices, in turn), Parakeet transcribes it, and the
# app's shipped rules run on the result. Rows whose target no longer passes the polish
# guard (Parakeet heard a different word) are dropped and counted.
# Run with the APP venv: .venv/bin/python -I tools/refiner/eval/build_eval.py <work_dir>

import hashlib
import json
import os
import subprocess
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, '..', '..', '..')
sys.path.insert(0, os.path.join(REPO, 'src'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))

from whisper_key.ai_provider import only_deletes_words   # noqa: E402
from whisper_key.config_manager import ConfigManager     # noqa: E402
from whisper_key.engine_factory import create_engine     # noqa: E402
from whisper_key.model_registry import ModelRegistry     # noqa: E402
from whisper_key.text_postprocess import postprocess     # noqa: E402
from build_data import APP_RULES                         # noqa: E402
from source_v1 import ROWS                               # noqa: E402

# US, UK and Australian English, so the set is not tuned to one voice.
VOICES = ('Samantha', 'Daniel', 'Karen')
OUT_FILE = os.path.join(HERE, 'eval_v1.jsonl')


def parakeet_engine():
    config = {**ConfigManager(quiet=True).get_whisper_config(), 'backend': 'parakeet', 'model': 'parakeet-v3'}
    registry = ModelRegistry(whisper_models_config=config.get('models', {}), streaming_models_config={})
    return create_engine(config, None, registry, language='en', beam_size=1, initial_prompt='',
                         hotwords=[], task='transcribe', languages=None)


# 16 kHz mono float audio of `text` spoken by `voice`.
def speak(text, voice, work_dir):
    aiff, wav = os.path.join(work_dir, 'row.aiff'), os.path.join(work_dir, 'row.wav')
    subprocess.run(['say', '-v', voice, '-o', aiff, text], check=True)
    subprocess.run(['afconvert', '-f', 'WAVE', '-d', 'LEI16@16000', aiff, wav], check=True)
    with wave.open(wav) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def main(work_dir):
    os.makedirs(work_dir, exist_ok=True)
    engine = parakeet_engine()
    # Shipped defaults exactly (cleanup light, no style): what most users run.
    rules = {**APP_RULES}
    kept, dropped, index = [], [], 0
    for category, rows in ROWS.items():
        for number, (spoken, target) in enumerate(rows):
            voice = VOICES[index % len(VOICES)]
            index += 1
            heard = engine.transcribe_audio(speak(spoken, voice, work_dir)) or ''
            model_input = postprocess(heard, rules)
            row = {'id': f'{category}-{number:02d}', 'category': category, 'voice': voice,
                   'spoken': spoken, 'asr': heard, 'input': model_input, 'target': target}
            (kept if model_input and only_deletes_words(model_input, target) else dropped).append(row)
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        for row in kept:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    with open(os.path.join(work_dir, 'eval_v1_dropped.jsonl'), 'w', encoding='utf-8') as f:
        for row in dropped:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    digest = hashlib.sha256(open(OUT_FILE, 'rb').read()).hexdigest()
    with open(OUT_FILE + '.sha256', 'w') as f:
        f.write(digest + '\n')
    print(f'kept {len(kept)}, dropped {len(dropped)} (Parakeet changed a word), sha256 {digest}')


if __name__ == '__main__':
    main(sys.argv[1])
