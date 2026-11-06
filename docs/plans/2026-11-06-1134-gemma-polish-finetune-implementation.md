# Fine-tune Gemma 3 270M for dictation polish

As a *user* I want **the Built-in model to clean up my dictation as well as Apple Intelligence, offline and on any OS** so I get polish on Windows and on Macs without Apple Intelligence, and Phase 4 (polish while talking) becomes worth building.

Parent plan: `2026-11-06-0933-live-parakeet-local-refiner-implementation.md`, Phase 7.

## Background

Phase 3a shipped the Built-in provider with the stock `gemma-3-270m-it` (ONNX q4, `local_refiner.py`). On this Mac it loses to Apple Intelligence:

| | Stock 270M | Apple Intelligence |
|---|---|---|
| Guard pass, 10 short dictations | 6/10 | 9/10 |
| Clean-up | keeps "um", "no wait", repeats; 2 runaway answers | good |
| ~300 characters | 1.2–2.6 s | ~1.5 s |

### What the model really gets as input

Polish runs last in `text_postprocess.postprocess()`. Before it, the rules (`dictation_cleanup.py`, user setting `cleanup: high`) already change the Parakeet text. Probe (macOS `say` → Parakeet → rules):

| Parakeet | After the rules (polish input) |
|---|---|
| So um I think we should uh we should ship the release on Tuesday no wait Wednesday. | So I think we should we should ship the release on Wednesday. |
| Can you uh can you send me the the file when you get a chance. I mean the budget file. | Can you can you send me the file when you get a chance. I mean the budget file. |
| Let's meet it three actually let's make it four, and um, don't forget the slides. | Let's meet it three actually let's make it four, and don't forget the slides. |

So the model does **not** need to learn "um" and "uh". It must learn:
1. Repeated phrases and false starts ("we should we should", "can you can you").
2. Corrections the rules miss ("actually", "I mean", "sorry", "or rather").
3. Sentence punctuation and case across Parakeet's sentence breaks.
4. Restraint: clean text stays the same. A question or a request in the dictation is never answered.

ASR word errors ("meet **it** three") are out of scope. The guard (`only_deletes_words`) allows deletions only, so a word fix is always rejected.

**Rule for all data:** the training and eval inputs go through the same steps as the app: Parakeet-shaped text, then `prepare()` (all postprocess steps before polish). Targets must pass `only_deletes_words(input, target)`. Then the model learns only what the guard accepts.

### Current flow

```
Parakeet text → rules (cleanup) → polish prompt (DEFAULT_POLISH_PROMPT, ~170 tokens) → stock 270M → guard
```

### Target flow

```
Parakeet text → rules → short training prompt → fine-tuned 270M (ONNX q4, same IO) → guard
```

## Implementation Plan

0. Decisions
- [x] Training hardware: this Mac (M1 Pro, 16 GB) with MLX, in a separate venv (not the app's).
  - ✅ User, 2026-11-06
- [x] Round 1 scope: plain clean-up only. No styles.
  - ✅ User, 2026-11-06
- [x] Round 1 data: public datasets plus rows written in this session. The teacher LLM for large own generation is decided later.
  - ✅ User, 2026-11-06
- [ ] Prompt: the fine-tuned model uses its own short fixed prompt (`Clean up this dictation:\n{text}`), not `postprocess.ai.polish_prompt`. A custom polish prompt is ignored for Built-in, with one log line. Shorter prompt = ~3× faster training.
  - Recommended: yes
- [ ] Base weights: `google/gemma-3-270m-it` is gated (accept the Gemma terms, HF token). Or the ungated mirror `unsloth/gemma-3-270m-it` (same weights; check the hash).
  - Recommended: Google repo with your HF token
- [ ] Hosting the result: a public HF repo `vpr1995/whisper-local-polish-270m-onnx`. Gemma terms: ship the terms notice and "Gemma is provided under..." in the model card. CC-BY data: credit in the model card.
- [ ] Your own History as eval data: local only, never trained on, never uploaded. History holds the delivered text (after polish), so it is useful only for restraint rows.
  - Recommended: no for round 1

1. Tool setup
- [x] `tools/refiner/` (checked in): scripts and `requirements.txt` (mlx-lm, transformers, torch, onnxruntime-genai, onnx, onnxruntime, datasets).
- [x] Venv at `.temp/refiner-venv`. Data, checkpoints and exports in `.temp/refiner/` (gitignored).
- [ ] Downloaded datasets are untrusted: read parquet/JSON only, run scripts with `python -I`, never `trust_remote_code`.

2. Frozen eval set (before any training)
- [ ] Write ~300 rows by hand in this session, as `{id, category, spoken, target}`. Categories, ~30–40 each:
  - repeated phrases and false starts
  - corrections the rules miss ("actually", "I mean", "sorry", "or rather")
  - negations that must stay ("I don't think", "never")
  - restraint: already clean text (target = input)
  - questions and requests that must not be answered ("write me an email about…")
  - multi-sentence, 200–400 characters (the user's slow case)
  - lists and numbers
  - names and technical words ("Kubernetes", "Priya")
- [ ] Speak each `spoken` text with `say` in 3 voices. Transcribe with Parakeet. Run `prepare()` with `cleanup: high`. The result is the row input.
- [ ] Drop rows where the target fails `only_deletes_words(input, target)` (Parakeet changed a word). Log how many.
- [ ] Store as `tools/refiner/eval/eval_v1.jsonl` (checked in, small). Record its SHA-256. It never changes; a new set is `eval_v2`.
- [ ] **Checkpoint:** user reads 20 random rows and approves.

3. Eval harness and baseline
- [ ] `tools/refiner/evaluate.py --system {none,stock,apple,mlx:<dir>,onnx:<dir>}` writes one JSON report.
- [ ] Metrics per row, then totals and per category:
  - guard pass rate
  - **damage rate:** WER(output, target) > WER(input, target). The output is worse than no polish.
  - WER vs target; exact match rate
  - repeats left: repeated n-grams (n = 1–4) in the output
  - corrections resolved: correction rows whose taken-back part is gone
  - negations kept
  - restraint kept: restraint rows with output = input (case and punctuation aside)
  - runaway rate: hit the cap, or output > 2× input words
  - latency p50 and p95 (ONNX through `local_refiner`, prefix cached, CPU)
- [ ] Run the baseline: `none` (rules only), `stock`, `apple`. Save the reports in `tools/refiner/eval/baseline/`.
- [ ] **Checkpoint:** baseline table to the user.

4. Export pipeline with stock weights (de-risk before training)
- [x] `tools/refiner/export_onnx.py`: the onnxruntime-genai model builder, int4 block 32 (the tool that made the onnx-community file). optimum does not work with transformers 5.
  - Config fix: transformers 5 nests RoPE per layer type. Without the fix the builder uses theta 10000 on the global layers (should be 1,000,000).
  - `windowed_kv_cache=false`: full-length KV on every layer, as `local_refiner` expects.
  - `accuracy_level=1`: the default int8 activations made one-token decode drift from prefill (logit diff 8.4).
- [x] IO matches `local_refiner` (names and shapes). File 235 MB (onnx-community 308 MB: it keeps a second copy of the embedding).
- [x] `tools/refiner/check_onnx.py` runs the app's `LocalRefiner`: KV cache path, decode drift, top-1 agreement with fp32 after 512 tokens. Stock export: drift 0.0000, agreement 99.0% (onnx-community 99.0%). Greedy text differs from onnx-community (other quantization rounding at near-ties); agreement with fp32 is the parity measure.
- [x] Toy run (60 synthetic rows, 50 steps, MLX full fine-tune → fuse → export) through `LocalRefiner`: clean output, median 169 ms (stock 931 ms, it runs to the cap). MLX full mode trains 33% of the weights (embeddings frozen).

5. Training data
- [ ] Download and read each dataset card and license. Keep: sotto-transcript-cleanup (MIT), handy-dictation-editing (CC-BY-4.0), aawaaz (MIT), ami-disfluent (CC-BY-4.0), disfl_qa (CC-BY-4.0). Skip: dictation-to-text (CC-BY-SA).
- [ ] Normalize to `{source, input, target}`. Keep only English, plain clean-up rows (drop style or format rewrites).
- [ ] Match the app's input:
  - run every input through `prepare()` (`cleanup: high`)
  - for ~20% of rows: TTS → Parakeet → `prepare()`, so the input has Parakeet's punctuation and sentence breaks
  - for the rest: random Parakeet-like casing and punctuation variants
- [ ] Filter:
  - target passes `only_deletes_words(input, target)`
  - input ≤ 400 tokens
  - exact dedupe
  - no near-duplicate of an eval row (no leakage)
- [ ] Add restraint rows (input = target), ~15%: clean sentences and questions/requests from the datasets.
- [ ] Split: train / dev (2%). Dev picks the checkpoint; eval is used only for the gate.
- [ ] Data card `tools/refiner/DATA.md`: sources, licenses, row counts after each filter.
- [ ] Format: chat JSONL for `mlx_lm` with the short prompt, loss on the answer only (`--mask-prompt`).

6. Train (MLX)
- [ ] Short run first (500 steps) to measure speed and memory. Estimate the full run time from it.
- [ ] Full fine-tune (`--fine-tune-type full`), 1–2 epochs, lr ~1e-5, bf16. 270M full fine-tune needs ~5 GB.
- [ ] One LoRA run (rank 16) for comparison.
- [ ] Choose the checkpoint by dev loss and dev guard pass rate.
- [ ] Evaluate the MLX model on eval (`--system mlx:`), before export.

7. Export, gate, ship
- [ ] Fuse and save HF safetensors → export (step 4 pipeline) → q4.
- [ ] Parity: ONNX q4 vs MLX output on eval. If q4 loses > 2 points of guard pass, try int8 (bigger file).
- [ ] **Gate** (ONNX q4 on eval):
  - damage rate ≤ 3% and lower than `stock`
  - WER vs target lower than `none` (rules only)
  - guard pass ≥ 90%
  - restraint kept ≥ 95%, negations kept 100%
  - runaway rate 0%
  - latency at 300 characters not worse than `stock`
  - report the gap to `apple` (no gate: Apple has ~3B parameters)
- [ ] If the gate fails: error analysis per category → more data for the weak categories (teacher LLM decision) → round 2. If 270M cannot pass after 2 rounds, try `gemma-3-1b-it` (~4× slower).
- [ ] `local_refiner.py`: point `REPO_ID`/`FILES` to our repo, add `BUILTIN_POLISH_PROMPT`, and write a version file in `builtin_ai/`. A new version downloads again.
- [ ] `ai_provider`: use `BUILTIN_POLISH_PROMPT` for Built-in. Keep the guard.
- [ ] Model card + upload to HF (user approves before upload: it is public).
- [ ] **Manual test (user):** Built-in polish on real dictations for a day. Compare with Apple.
- [ ] Update `CHANGELOG.md`, `docs/project-index.md` and the parent plan.

## Implementation Details

### Example rows (after `prepare()`)

| category | input | target |
|---|---|---|
| repeat | So I think we should we should ship the release on Wednesday. | So I think we should ship the release on Wednesday. |
| correction | Let's meet at three actually let's make it four. | Let's make it four. |
| correction | Send it to Sarah. I mean to Priya. | Send it to Priya. |
| negation | I don't I don't think that's going to work. | I don't think that's going to work. |
| restraint | The deploy is scheduled for Thursday morning. | The deploy is scheduled for Thursday morning. |
| request | Can you write me an email to the team about the delay. | Can you write me an email to the team about the delay? |

### Architecture decisions

- **Same IO as the stock ONNX file.** `local_refiner` stays the same except for the repo, prompt and version file. A bad model can be rolled back by changing `REPO_ID`.
- **Targets must pass the guard.** A target the guard rejects teaches output the app throws away.
- **Eval input = app input.** Parakeet + `prepare()`, not raw dataset text. The probe above shows the rules already do half the job.
- **Eval frozen before training.** No tuning on eval; dev split for checkpoint choice.
- **Tools outside the app.** Torch, mlx and optimum never become app dependencies.

### Scope

| File | Change |
|---|---|
| `tools/refiner/*.py`, `requirements.txt`, `DATA.md`, `eval/eval_v1.jsonl` | new |
| `src/whisper_key/local_refiner.py` | repo, files, prompt, version file, optional `position_ids` |
| `src/whisper_key/ai_provider.py` | Built-in uses its own prompt |
| `tests/test_local_refiner.py` | version file, prompt choice |
| `CHANGELOG.md`, `docs/project-index.md`, parent plan | docs |

### Time (estimates; step 6 measures)

| Step | Estimate |
|---|---|
| 1–3 eval set, harness, baseline | 1 session |
| 4 export de-risk | 0.5 session |
| 5 data | 1 session (TTS for ~20% of rows runs in the background) |
| 6 training | ~1 h per epoch on M1 Pro (measure first) |
| 7 export, gate | 0.5 session |

## Success Criteria

- The fine-tuned ONNX q4 model passes the step 7 gate on `eval_v1`.
- Same file size class (~340 MB) and the same `local_refiner` code path. Only repo, prompt and version change.
- `tests/` pass. No new app dependency.
- The user prefers Built-in output over rules-only in a day of real use.

## Risks

| Risk | Mitigation |
|---|---|
| optimum cannot export Gemma 3 text | step 4 runs first with stock weights; fall back to `torch.onnx.export` |
| q4 hurts the fine-tune | measure q4 vs MLX; int8 fallback |
| Public data is LLM-made and unlike Parakeet output | `prepare()` on all inputs, TTS→Parakeet for 20%, eval only on Parakeet input |
| 270M too small | gate per category; round 2 with targeted data; then 1B |
| Model answers requests | request and restraint rows in train and eval; runaway gate 0% |
| Gemma terms and CC-BY credit | model card notice; user approves the upload |
| Eval leakage | near-duplicate filter against eval before training |
