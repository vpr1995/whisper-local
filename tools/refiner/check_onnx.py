# tools/refiner/check_onnx.py
# Parity checks for an exported model folder before the app uses it. Runs the app's
# own LocalRefiner, so the KV-cache path is tested too. Compares against an HF folder
# (transformers, fp32) and, optionally, another ONNX folder (e.g. stock onnx-community).
# Usage: check_onnx.py <onnx_folder> <hf_folder> [other_onnx_folder]

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from whisper_key import local_refiner  # noqa: E402

PROMPTS = [
    'so we we should ship the build on Friday',
    'can you can you send me the notes from the meeting',
    'the deadline is Monday sorry Tuesday',
    'I think the new design looks good but the buttons are too small',
    'please book a table for four at seven',
    'we need eggs milk bread and coffee',
    'it is not ready for production yet',
    'what is the capital of France',
    'thanks for the update I will take a look this afternoon',
    'the the server crashed again last night',
]
PREFIX = 'Clean up this dictation:\n'


# A LocalRefiner that loads `folder` instead of the app data folder.
def refiner_for(folder: str):
    local_refiner.model_folder = lambda: folder
    refiner = local_refiner.LocalRefiner()
    with refiner._lock:
        assert refiner._ensure_loaded(), f'failed to load {folder}'
    return refiner


# Raw (logits per position, kv) for `ids` after `past` cached tokens.
def run_logits(refiner, ids, kv, past):
    feeds = {'input_ids': np.array([list(ids)], dtype=np.int64),
             'attention_mask': np.ones((1, past + len(ids)), dtype=np.int64)}
    feeds.update(zip(refiner._kv_names, kv))
    outputs = refiner._session.run(None, feeds)
    return outputs[0][0], outputs[1:]


# Top-1 next-token ids at every position of one full forward pass.
def onnx_top1(refiner, ids):
    empty = [np.zeros((1, 1, 0, 256), dtype=np.float32)] * len(refiner._kv_names)
    return refiner._forward(ids, empty, 0)[0]


def main(onnx_folder, hf_folder, other_folder=None):
    import torch
    from transformers import AutoModelForCausalLM
    ours = refiner_for(onnx_folder)
    failures = 0

    # 1. KV cache: cached prompt start must give the same text as no cache.
    for text in PROMPTS[:5]:
        cached = ours.generate(PREFIX + text, stable_prefix=PREFIX)
        ours._prefix = None
        plain = ours.generate(PREFIX + text)
        if cached != plain or not cached:
            failures += 1
            print(f'KV MISMATCH: {text!r}: {cached!r} vs {plain!r}')
    print('1. KV cache path: ' + ('ok' if failures == 0 else 'FAIL'))

    # 1b. Decode drift: one full pass vs a short start plus one token at a time must
    #     give the same last logits (int8 matmul activations once broke this).
    ids = [local_refiner.BOS_ID] + ours._tokenizer.encode(local_refiner.TURN_START + PREFIX + PROMPTS[2],
                                                          add_special_tokens=False).ids
    empty = [np.zeros((1, 1, 0, 256), dtype=np.float32)] * len(ours._kv_names)
    one_pass = run_logits(ours, ids, empty, 0)[0]
    stepped = run_logits(ours, ids[:4], empty, 0)
    for i, token in enumerate(ids[4:]):
        stepped = run_logits(ours, [token], stepped[1], 4 + i)
    drift = float(np.abs(one_pass[-1] - stepped[0][-1]).max())
    print(f'1b. Decode drift (max logit diff): {drift:.4f}')
    if drift > 0.01:
        failures += 1

    # 2. Long input (>512 tokens, so sliding-window layers matter): top-1 agreement
    #    with transformers fp32, at the positions after token 512.
    hf = AutoModelForCausalLM.from_pretrained(hf_folder, dtype=torch.float32).eval()
    long_text = ' '.join(PROMPTS * 12)
    ids = [local_refiner.BOS_ID] + ours._tokenizer.encode(local_refiner.TURN_START + PREFIX + long_text,
                                                          add_special_tokens=False).ids
    with torch.no_grad():
        reference = hf(torch.tensor([ids])).logits[0].argmax(-1).numpy()
    tail = slice(512, len(ids))
    agree = float((onnx_top1(ours, ids)[tail] == reference[tail]).mean())
    print(f'2. Long input ({len(ids)} tokens), top-1 agreement after 512 vs fp32: {agree:.1%}')
    if other_folder:
        other = refiner_for(other_folder)
        other_agree = float((onnx_top1(other, ids)[tail] == reference[tail]).mean())
        print(f'   other model, same check: {other_agree:.1%}')
        if agree < other_agree - 0.05:
            failures += 1

    # 3. Greedy outputs side by side with the other model.
    if other_folder:
        same = 0
        for text in PROMPTS:
            a = ours.generate(PREFIX + text, stable_prefix=PREFIX)
            b = other.generate(PREFIX + text, stable_prefix=PREFIX)
            same += a == b
            print(f'   {text!r}\n     ours : {a!r}\n     other: {b!r}')
        print(f'3. Same greedy output as other model: {same}/{len(PROMPTS)}')
    print('RESULT: ' + ('PASS' if failures == 0 else f'FAIL ({failures})'))
    return failures


if __name__ == '__main__':
    sys.exit(1 if main(*sys.argv[1:]) else 0)
