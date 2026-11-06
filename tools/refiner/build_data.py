# tools/refiner/build_data.py
# Builds round-1 training data for the Built-in polish model from public datasets.
# Every input goes through the app's own postprocess() (shipped defaults, AI off), so the model
# sees what the app gives it; every target must pass the app's polish guard.
# Run with the APP venv: .venv/bin/python -I tools/refiner/build_data.py <datasets> <out> [rows]

import glob
import hashlib
import json
import os
import random
import re
import sys
from collections import Counter

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
sys.path.insert(0, os.path.join(REPO, 'src'))
sys.path.insert(0, os.path.join(REPO, 'tools', 'refiner', 'eval'))

from whisper_key.ai_provider import only_deletes_words  # noqa: E402
from whisper_key.text_postprocess import postprocess    # noqa: E402
from source_v1 import ROWS as EVAL_ROWS                 # noqa: E402

# The fine-tuned model's own short prompt. local_refiner gets the same text when it ships.
PROMPT = 'Clean up this dictation:\n'
SEED = 1234
MAX_INPUT_CHARS = 1500          # ~400 tokens
RESTRAINT_FRACTION = 0.15       # rows where the input is already clean
DEV_FRACTION = 0.02
LEAK_NGRAM = 8                  # shared word 8-grams with an eval row = leak
# Share of each source in the sample (after filters). aawaaz is small but long and varied.
SOURCE_SHARE = {'sotto': 0.45, 'handy': 0.35, 'aawaaz': 0.20}

# The app's rules before polish: the shipped postprocess defaults with AI off. Each
# row gets a random cleanup level and style, since users run both levels.
def _app_defaults():
    from ruamel.yaml import YAML
    path = os.path.join(REPO, 'src', 'whisper_key', 'config.defaults.yaml')
    with open(path, encoding='utf-8') as f:
        rules = dict(YAML(typ='safe').load(f)['postprocess'])
    rules['ai'] = {'polish': False}
    return rules


APP_RULES = _app_defaults()
CLEANUP_LEVELS = ('light', 'high')
STYLES = ('', 'formal')


def app_rules(rng):
    return {**APP_RULES, 'cleanup': rng.choice(CLEANUP_LEVELS), 'style': rng.choice(STYLES)}


# ── Load ─────────────────────────────────────────────────────────────────────

def _jsonl(path):
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


# (source, raw input, clean target) from each dataset; data files only, no code.
def load_pairs(datasets):
    for row in _jsonl(os.path.join(datasets, 'juanquivilla__sotto-transcript-cleanup', 'train.jsonl')):
        yield 'sotto', row.get('input'), row.get('output')
    for row in _jsonl(os.path.join(datasets, 'MagicNoThief__handy-dictation-editing', 'handy_editor.jsonl')):
        turns = {m.get('role'): m.get('content') for m in row.get('messages', [])}
        yield 'handy', turns.get('user'), turns.get('assistant')
    for path in sorted(glob.glob(os.path.join(datasets, 'shantanugoel__aawaaz-transcript-cleanup-dataset', '*.jsonl'))):
        for row in _jsonl(path):
            yield 'aawaaz', row.get('input'), row.get('output')


# ── Filters ──────────────────────────────────────────────────────────────────

def _words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def _ngrams(words, n=LEAK_NGRAM):
    return {' '.join(words[i:i + n]) for i in range(len(words) - n + 1)}


# Word n-grams of every eval row (spoken and target), and short eval texts whole.
def eval_fingerprints():
    grams, whole = set(), set()
    for rows in EVAL_ROWS.values():
        for spoken, target in rows:
            for text in (spoken, target):
                words = _words(text)
                grams |= _ngrams(words)
                whole.add(' '.join(words))
    return grams, whole


def _english(text):
    return sum(ch.isascii() for ch in text) >= 0.98 * len(text)


# ── Build ────────────────────────────────────────────────────────────────────

def chat_row(user_text, answer):
    return {'messages': [{'role': 'user', 'content': PROMPT + user_text},
                         {'role': 'assistant', 'content': answer}]}


def main(datasets, out, total_rows=40000):
    random.seed(SEED)
    leak_grams, leak_whole = eval_fingerprints()
    drops = Counter()
    kept = {name: [] for name in SOURCE_SHARE}
    seen = set()
    clean_targets = []

    for source, raw, target in load_pairs(datasets):
        if not isinstance(raw, str) or not isinstance(target, str) or not raw.strip() or not target.strip():
            drops['empty'] += 1
            continue
        raw, target = ' '.join(raw.split()), ' '.join(target.split())
        if not (_english(raw) and _english(target)):
            drops['not_english'] += 1
            continue
        if len(raw) > MAX_INPUT_CHARS:
            drops['too_long'] += 1
            continue
        model_input = postprocess(raw, app_rules(random))
        if not model_input.strip():
            drops['rules_emptied'] += 1
            continue
        if not only_deletes_words(model_input, target):
            drops['guard'] += 1
            continue
        key = model_input.lower()
        if key in seen:
            drops['duplicate'] += 1
            continue
        words = _words(model_input) + ['|'] + _words(target)
        if _ngrams(words) & leak_grams or ' '.join(_words(target)) in leak_whole:
            drops['eval_leak'] += 1
            continue
        seen.add(key)
        kept[source].append((model_input, target))
        clean_targets.append(target)

    # Sample by source share; a small source gives all it has.
    normal_rows = int(total_rows * (1 - RESTRAINT_FRACTION))
    sample = []
    for source, share in SOURCE_SHARE.items():
        rows = kept[source]
        random.shuffle(rows)
        sample += [(source, i, t) for i, t in rows[:int(normal_rows * share)]]

    # Restraint rows: clean text in (after the same rules), the same text out.
    used = {t for _, _, t in sample}
    spare = [t for t in clean_targets if t not in used]
    random.shuffle(spare)
    restraint = []
    for target in spare:
        if len(restraint) >= total_rows - len(sample):
            break
        model_input = postprocess(target, app_rules(random))
        if model_input == target or (model_input.rstrip('.') == target.rstrip('.!?')):
            restraint.append(('restraint', target, target))
    sample += restraint
    random.shuffle(sample)

    dev_count = max(1, int(len(sample) * DEV_FRACTION))
    os.makedirs(out, exist_ok=True)
    for name, rows in (('valid', sample[:dev_count]), ('train', sample[dev_count:])):
        with open(os.path.join(out, f'{name}.jsonl'), 'w', encoding='utf-8') as f:
            for _, model_input, target in rows:
                f.write(json.dumps(chat_row(model_input, target), ensure_ascii=False) + '\n')

    stats = {'kept_after_filters': {s: len(r) for s, r in kept.items()},
             'dropped': dict(drops),
             'sample': dict(Counter(s for s, _, _ in sample)),
             'train': len(sample) - dev_count, 'valid': dev_count,
             'train_sha256': hashlib.sha256(open(os.path.join(out, 'train.jsonl'), 'rb').read()).hexdigest()}
    with open(os.path.join(out, 'stats.json'), 'w') as f:
        json.dump(stats, f, indent=2)
    print(json.dumps(stats, indent=2))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 40000)
