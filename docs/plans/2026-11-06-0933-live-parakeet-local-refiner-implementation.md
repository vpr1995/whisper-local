# Live Parakeet Transcription and Built-in Refiner

As a *user* I want **my text to appear in under a second after I release the hotkey, also after a long dictation** so I do not wait for transcription and polish to start at key-up.

## Background

- Today all work starts at key-up. Parakeet transcribes the full recording, then `postprocess()` runs, then AI polish (Apple Intelligence or Ollama) rewrites the full text. Polish is skipped above 1200 characters (`ai_provider.MAX_POLISH_CHARACTERS`). The transcription wait grows faster than the recording length.
- Phonon (github.com/Infatoshi/phonon) does the work while the user speaks. Parakeet re-transcribes the recording in rounds, and its refiner rewrites the transcript so far. At key-up only the last seconds are left.
- A spike (`.temp/gemma270-bench/stream2.py`) tested this approach on an M1 Pro, CPU only, with Parakeet TDT v3 int8 and Gemma 3 270M (q4 ONNX). Both models run on packages the app already has (`sherpa-onnx`, `onnxruntime`, `tokenizers`).

### Spike results

Meeting-style clips, macOS `say` voices, real-time replay, final settings, 1 run each.

| Clip | Today: ASR only (whole file) | Spike: ASR + Gemma polish after key-up | Live: ASR + Gemma polish | Live ASR WER (whole-file WER) | CPU while speaking (ASR + LLM) |
|---|---:|---:|---:|---:|---:|
| 20 s | 0.71 s | 2.7 s | 0.73 s | 0.012 (0.012) | 76% + 79% |
| 1.3 min | 3.6 s | 10.1 s | 0.58 s | 0.011 (0.017) | 67% + 52% |
| 5.2 min | 28.9 s, 3.4 GB RAM | not run | 0.97 s | 0.015 (0.018) | 22% + 30% |
| 15.1 min | not run (RAM grows faster than length) | not run | 0.50 s | 0.018 (not run) | 12% + 22% |

- "Today" is the Parakeet time of today's app. Polish adds to it for text up to 1200 characters (about 1 min of speech). Above that, today's app delivers no polish.
- The live key-up time includes the Gemma polish of the last group. With live ASR alone (no polish), key-up is the release decode: 0.2–0.4 s.

### Findings that drive the design

1. **Cache the instruction prefix.** The prompt start is the same each time. Caching its KV saves 300–400 ms on every polish.
2. **Draft verification.** At key-up, the model checks the last draft in one forward pass. For each group, the output was identical to a rewrite of that group without a draft.
3. **Sentence groups.** A 270M model cannot rewrite a long transcript in one prompt. The spike splits the transcript into groups of about 25 words. It rewrites each closed group once and caches the result. Only the open tail is rewritten at key-up.
4. **Round cadence.** Rounds every 0.4 s for the first 60 s, then every 5 s. This cut ASR CPU from about 95% to 12% on a 15-min clip. Key-up time did not change.
5. **Parakeet skips speech at mid-speech window starts.** Some window starts produced 0 words for 10 s of speech. A leading 0.3 s of silence fixed 21 of 22 bad starts. With the pad, live WER was equal to or lower than whole-file WER where both were measured (20 s to 5.2 min). Every windowed Parakeet decode must add this pad.
6. **Anchor check.** The last committed word must appear again in the next window, near its time. If it does not, the round retries 1 s earlier. In the spike it never fired with the pad on. Keep it as a guard.
7. **Stock Gemma 270M quality is poor.** It kept "no wait" and changed "cache" to "cash". Phase 3 adds the refiner. Phase 7 fine-tunes it.

### Current flow

```
key-down → AudioRecorder buffers audio (Moonshine preview optional)
key-up   → stop_recording() → trims → ParakeetEngine.transcribe_audio(full)
         → postprocess(text): cleanup → formatting → corrections → style → ai_provider.polish(full, ≤ 1200 chars)
         → deliver
```

### Target flow

```
key-down → LiveTranscriber.start(pre-roll) ; AudioRecorder → LiveTranscriber.feed(16 kHz mono chunk)
             ASR worker:  rounds → committed words + open words → version++
             LLM worker:  prepare(text so far) → groups → refine closed groups (cache) → draft for open tail
key-up   → LiveTranscriber.finish(): decode last ~4 s → final transcript
         → prepare(final) → LivePolisher.finish(): cached groups + verify tail draft → guard each group
         → deliver
```

## Implementation Plan

The phases are in delivery order. Phases 1–2 (live ASR) give the latency gain alone, with polish off, Apple Intelligence or Ollama. They are the first checkpoint for a user test.

0. Decisions (answer before phase 1)
- [x] Live transcription: opt-in (experimental) first, or default on for Parakeet?
  - ✅ Opt-in (2026-11-06). Default on after the real-mic tests pass
- [x] Overlay preview: when live transcription is on, show its round text and do not run Moonshine?
  - ✅ Yes, use the live text (2026-11-06)
- [x] Long dictations: keep the 1200-character polish limit for live polish, or polish every group?
  - ✅ Polish every group (2026-11-06). A 15-min meeting gets polish for the first time
- [x] Built-in refiner in `provider: auto`?
  - ✅ Manual choice only until the fine-tuned model passes the Phase 7 gate (2026-11-06)
- [x] Verbatim style: skip the LLM?
  - ✅ Yes (recommended default, not asked)
- [x] `max_duration` stays at 900 s?
  - ✅ Yes for now (recommended default, not asked)
- [ ] Fine-tune hardware: Apple silicon (MLX), Colab, or a rented GPU? (Needed before Phase 7.)

1. Windowed Parakeet decode
- [x] `parakeet_engine.py`: add `decode_window(audio_16k, offset_s) -> list[Word]`. Each `Word` has text and absolute start time. Add 0.3 s leading silence.
  - ✅ Model lock held only to get the recognizer. On a 20 s clip the words equal the plain decode except one comma. The 4 spike windows that gave 0 words now give 12–38 words
- [x] Concurrency: check sherpa-onnx docs and issues for concurrent `decode_stream` calls on one `OfflineRecognizer`. Then run a stress test: 200 parallel decodes, with output compared to sequential decodes.
  - Safe: the key-up decode does not wait for a running round.
  - Not safe: use one lock and measure the key-up wait in Phase 5. If the wait is too long, give the key-up decode a second recognizer (~650 MB more RAM).
  - ✅ Safe. sherpa-onnx's own `non_streaming_server.py` calls `decode_streams` on one recognizer from `nn_pool_size` threads. Stress test: 200/200 identical (text and timestamps), 4 threads 22.0 s vs sequential 47.6 s
- [x] New `live_transcriber.py`, pure helpers first: `words_after(words, kept)` (match the last kept word by text within ±0.3 s; fall back to time + 0.04 s) and `decode_after(...)` (anchor check, retry 1 s and 2 s earlier, count retries).
  - ✅ On real audio the window at 138.0 s still skipped speech with the pad; the anchor retry (1 s earlier) recovered it
- [x] Unit tests with a fake decoder: jitter, short adjacent words, a missing anchor, retry.
  - ✅ `tests/test_live_transcriber.py`: 10 tests pass. Full suite: 584 pass, 5 skipped

2. Live transcription (ASR rounds)
- [x] `LiveTranscriber`: holds a 16 kHz buffer, runs one worker thread and exposes `start(preroll)`, `feed(chunk)`, `finish() -> str` and `cancel()`.
  - Round plan: every 0.4 s, left context 5 s, commit after 15 s while the recording is ≤ 60 s. After that: every 5 s, left context 3 s, commit each round.
  - Commit words that start more than 2 s before the round end.
  - `finish()`: `t_cut = max(commit_t, last_round_end − 2 s)`. Decode `[t_cut − 4 s, end]`. Drop a round that is still running (or wait, per the Phase 1 concurrency result).
  - Publish `(version, committed words, open words)` for the LLM worker and the overlay.
  - ✅ Rounds run in `step()`, so tests and replay drive them inline. `finish()` drops a running round (decodes are safe in parallel). Text goes out through an `on_text` callback; the LLM hook comes in Phase 4. Replay of the spike clips: 5.2 min WER 0.0151 (spike 0.0151), 1.3 min 0.0105 inline and 0.0174 real-time (spike 0.0174), key-up 215–293 ms
- [x] `audio_recorder.py`:
  - At `start_recording()`, seed `LiveTranscriber.start()` with the pre-roll buffer (~500 ms), resampled to 16 kHz mono. Without it the first word gets clipped again.
  - For each chunk while recording: flatten channels to mono, resample to 16 kHz (reuse `_resample_audio`), call `live_transcriber.feed()`.
  - ✅ `start_recording(live_sink=)`. The callback only makes mono and enqueues. The worker resamples with `soxr.ResampleStream` (per-chunk `resample` puts artifacts at chunk edges). Pre-roll seed and `is_recording = True` are in one lock block. Moonshine is skipped while a live sink runs. **The WASAPI resample path is not tested on a real mic yet (Windows test).**
- [x] `state_manager.py`: in `_transcription_pipeline`, use `live_transcriber.finish()` when it is active and healthy. Otherwise use `transcribe_audio(full)` and log why.
  - Live path is off for: non-Parakeet backend, whisper mode, command mode, rephrase, and commit-on-endpoint delivery.
  - ✅ Gate: `hasattr(engine, 'decode_window')`, so Phase 6 needs no gate change. Empty live text = "No speech detected". Live text of ≤ 3 words gets the VAD check, like `transcribe_audio`. Cancel and every recording start (dictation, command, rephrase) end a left-over session
- [x] `config.defaults.yaml`: `streaming.live_transcription` (default from decision 0.1) with a comment on the CPU cost. Keep cadence numbers as constants in code.
- [x] `idle_unload.py`: never unload the engine while a live session runs.
  - ✅ No change needed: `is_app_idle` is `state == "idle"`, false while recording and processing
- [x] Overlay: show the live text (decision 0.2).
- [x] Log per dictation (no text): key-up → text ms, release decode ms, key-up wait, rounds, retries, lag at key-up, CPU busy %.
  - ✅ `Live transcription: {keyup_ms, duration_s, rounds, retries, release_anchored, lag_s, busy_pct}`. Measured CPU in the first minute: ~280–310% of one core (Parakeet uses 4 threads)
- [ ] Settings page toggle for `live_transcription` (YAML only for now).
- [ ] **Checkpoint:** live ASR bench (Phase 5 tool, ASR part) + user real-mic test with polish off and with Apple Intelligence.

3. Built-in refiner: Gemma 3 270M on ONNX (no live polish yet)
- [ ] Add a `refiner` model group to `model_registry.py`: source `onnx-community/gemma-3-270m-it-ONNX`, files `onnx/model_q4.onnx`, `onnx/model_q4.onnx_data`, `tokenizer.json` (~340 MB). Download through `model_download.py` with the progress line.
- [ ] New `local_refiner.py`: `LocalRefiner` with lazy load, one ONNX session, a lock (one generate at a time), `refine(text, draft=(), abort=None) -> str`, output cap `1.5 × input tokens + 32`, stop on `<eos>` / `<end_of_turn>`, `unload()`.
- [ ] Prefix KV cache: tokenize the prompt part before `{text}` once per template. Keep its KV. Recompute when the template changes.
- [ ] `ai_provider.py`: add `PROVIDER_LOCAL = 'local'` ("Built-in"). Route `_generate`, `prewarm_in_background`, `describe_status` and `polish_failure_reason` to it. Keep the `only_deletes_words` guard.
- [ ] `idle_unload.py`: also unload the refiner after idle.
- [ ] Settings page: add "Built-in" to the provider choice. Show download state on the Models page.
- [ ] `doctor.py`: report refiner files and load time.
- [ ] Tests: prefix-cache output equals a no-cache run (skip when the model is not downloaded). Cap and stop-token handling with a fake session.
- [ ] **Measure:** polish time per sentence vs Ollama and Apple Intelligence on the same 20 dictations.

4. Rewrite while talking
- [ ] `text_postprocess.py`: split `postprocess()` into `prepare(text, config)` (all steps before polish) and the polish step. No behavior change. Existing tests must pass.
- [ ] New `live_polish.py`:
  - `sentence_groups(text, min_words=25) -> (closed, open)`: a pure function with tests.
  - `LivePolisher`: `on_transcript(text)` runs `prepare()`, refines closed groups not in the cache (oldest first), then refines the open tail with the last draft. Abort between tokens at key-up.
  - `finish(prepared_final)`: per group, use the cache hit, the tail draft, or refine with draft verification. Run the guard on each group. A rejected group keeps its prepared text.
  - `prepare()` is not always stable when text is appended (list formatting, snippets can change earlier text). Then the group cache misses. This costs latency, not correctness.
- [ ] Active only with the Built-in provider and polish on (Apple and Ollama give no draft control). Character limit per decision 0.3.
- [ ] `state_manager.py`: when a live polish exists, deliver its output instead of calling `ai_provider.polish()`.
- [ ] Tests: groups are stable when text is appended; cache hits; tail draft reuse; a guard reject keeps the group's text.

5. Measure and harden
- [ ] Add `tools/live_bench.py`: replay a WAV at real time through the real `LiveTranscriber` + `LivePolisher` classes. Print the spike table columns.
- [ ] Port the meeting-clip generator (`make_meeting.py`) to `tools/` for repeatable clips with a reference script.
- [ ] **Check:** in-app numbers are within 20% of the spike numbers on the same clips.
- [ ] **Manual test (user):** real mic on macOS, dictations of 5 s, 30 s, 2 min and 10 min. Check text, latency and fan noise.
- [ ] **Manual test (user):** Windows, same set (CPU only).
- [ ] Update `docs/project-index.md` (new modules) and `CHANGELOG.md`.

6. Whisper backend (after Parakeet is stable)
- [ ] `whisper_engine.decode_window()` with faster-whisper `word_timestamps=True`.
- [ ] Slow cadence from the start: Whisper encodes a 30 s window each round.
- [ ] Use Whisper's own guards (previous-text prompt, skip silence). The 0.3 s pad is Parakeet-only.
- [ ] Measure separately: CPU `small`, GPU `large-v3-turbo`.

7. Training data and fine-tune (write a separate plan when we reach it)
- [ ] Pair logging (opt-in, local only): raw ASR, prepared text, polished text, delivered text, style, app. Include History "Fix this everywhere" edits.
- [ ] Frozen eval set (~300 rows) before any training. Metrics: WER vs target, damage rate (rows worse than prepared text), fillers left per 100 words, self-corrections resolved, negations kept, runaway rate, latency.
- [ ] Public data (see table below). Check each license before use.
- [ ] Own data, generated in reverse so the target is exact:
  1. An LLM writes the intended text for a scenario (chat, email, AI prompt, notes, code talk) in one style.
  2. An LLM writes how a person would say it: fillers, restarts, "no wait" corrections, spoken punctuation.
  3. TTS speaks it in several voices (macOS `say`, Kokoro or Piper).
  4. Parakeet transcribes it, so the input has real ASR errors.
  5. Row = `{raw, style, target}`. About 15% "restraint" rows where the clean input must stay unchanged.
- [ ] Style is one prompt line (for example `Style: casual`), the same in training and in the app.
- [ ] Fine-tune `gemma-3-270m-it` (full fine-tune or LoRA). Also train a ~350M–1B model on the same data and compare.
- [ ] Export to ONNX with the same inputs and outputs (`past_key_values.*`), quantize to q4, and check agreement with the PyTorch output.
- [ ] Gate: lower damage rate than no polish and than stock 270M on the eval set. Then replace the delete-only guard with a length guard (fall back when output > 2× or < 0.4× input words).

## Implementation Details

### Example training rows

| style | raw (Parakeet) | target |
|---|---|---|
| formal | `um hi sarah I wanted to uh follow up on the the invoice from tuesday no wait wednesday` | `Hi Sarah, I wanted to follow up on the invoice from Wednesday.` |
| casual | `yeah so uh the build's broken again can you take a look` | `Yeah so the build's broken again, can you take a look?` |
| very_casual | `lol okay um I'll be there in like ten minutes` | `lol okay i'll be there in like 10 minutes` |
| verbatim | `git commit dash m fix login bug` | LLM skipped (decision 0.5) |
| restraint | `The deploy finished and all tests passed.` | `The deploy finished and all tests passed.` |

### Public datasets found on Hugging Face

| Dataset | Rows | License | Notes |
|---|---:|---|---|
| `juanquivilla/sotto-transcript-cleanup` | 135k + 7k | MIT | Synthetic pairs. Its authors fine-tuned LFM2.5-350M on it for a local macOS app |
| `MagicNoThief/handy-dictation-editing` | 90k + 2k | CC-BY-4.0 | Built for a model under 500M parameters. Strong on self-corrections |
| `shantanugoel/aawaaz-transcript-cleanup-dataset` | 22.8k | MIT | 10 categories (email, casual, meeting, code, …). Keeps the speaker's voice |
| `JacobLinCool/ami-disfluent` | 24k | CC-BY-4.0 | Real meeting speech (AMI) |
| `google-research-datasets/disfl_qa` | 12k | CC-BY-4.0 | Disfluent questions → fluent questions |
| `GenSEC-LLM/SLT-Task1-Post-ASR-Text-Correction` | 257k | Apache-2.0 | ASR n-best lists → reference text |
| `ductai199x/dictation-to-text` | 89k | CC-BY-SA-4.0 | Share-alike. Avoid unless that is acceptable |

These rows are text-only synthetic. Phonon found that text-only corruption did not beat raw ASR for fixing technical terms, so our own audio-based rows must be part of the mix.

### Architecture decisions

- **One recognizer.** Round decodes and the key-up decode share the Parakeet recognizer. Whether the key-up decode may run beside a running round depends on the Phase 1 concurrency check. The spike ran them in parallel without errors and faster than in sequence, but that does not prove thread safety.
- **No trims on the live path.** `_trim_long_pauses` changes timestamps, so the live path decodes untrimmed audio. The 0.3 s pad and the anchor check cover the start-of-window risk.
- **Groups, not one prompt.** Group polish loses a self-correction that crosses a group boundary ("Tuesday. No wait, Wednesday."). A group closes only at a sentence end, and the open tail is always polished with its full text.
- **Deterministic logic in code.** Cadence, window sizes, group size and guard limits are constants in code, not prompt text.

### Scope

| File | Change |
|---|---|
| `live_transcriber.py` | New: ASR rounds, commit, anchor check, key-up release window |
| `local_refiner.py` | New: ONNX Gemma session, prefix cache, draft-verified greedy decode |
| `live_polish.py` | New: sentence groups, group cache, tail draft |
| `parakeet_engine.py` | `decode_window()` with 0.3 s pad |
| `audio_recorder.py` | Pre-roll seed; feed 16 kHz mono chunks to the live transcriber |
| `state_manager.py` | Use live results at key-up, fall back to the full path |
| `text_postprocess.py` | Split `prepare()` from the polish step |
| `ai_provider.py` | `local` provider |
| `model_registry.py`, `model_download.py` | Refiner model entry and download |
| `idle_unload.py` | Refiner unload, no unload during a live session |
| `config.defaults.yaml` | `streaming.live_transcription`, provider option |
| `dashboard/` | Provider option, refiner download state |
| `tools/live_bench.py`, `tools/make_meeting.py` | Replay benchmark and clip generator |
| `tests/` | Helpers, groups, refiner cache, postprocess split |

## Success Criteria

- [ ] Live ASR alone: key-up → text ≤ 0.5 s for 5 s, 1 min, 5 min and 15 min dictations on an M1 Pro (CPU).
- [ ] Live ASR + Built-in polish: key-up → text ≤ 1 s for the same set.
- [ ] Live ASR WER is within 0.005 of whole-file WER where whole-file is measured (up to 5 min).
- [ ] CPU while speaking ≤ 40% (ASR + LLM) after the first 60 s.
- [ ] Per group, polish with a draft gives the same output as polish without a draft.
- [ ] The first word is not clipped (pre-roll seed).
- [ ] A live-path failure falls back to the full path and the log says why.
- [ ] Existing tests pass. New helper tests pass.
- [ ] The user's real-mic test on macOS and Windows passes before commit.

## Risks

| Risk | Mitigation |
|---|---|
| Real mics and voices differ from `say` clips | Real-mic test in Phase 2 and Phase 5. Keep the anchor-check counters in logs |
| Concurrent decodes on one recognizer are not thread-safe | Phase 1 check. Lock, or a second recognizer for the key-up decode |
| Windows CPUs are slower than an M1 Pro | Measure in Phase 5. Lengthen the slow cadence if rounds fall behind |
| Stock Gemma 270M output is poor | Phase 7 fine-tune. Keep the delete-only guard until then |
| Fine-tuned ONNX export changes input/output names | Check the signature in the export step. The spike code reads names from the session |
| CPU load makes the fan run in long sessions | Slow cadence after 60 s. Decision 0.1 keeps the feature opt-in |
| The tail rewrite at key-up is slow when the stock model rewrites from the start | Fine-tuned model accepts more of the draft. Groups keep the tail short |

## References

- Spike: `.temp/gemma270-bench/` (copied from `/tmp`): `stream2.py`, `make_meeting.py`, `wholefile.py`, `sweep.log`, `slow2.log`
- Phonon `SPEC.md` (streaming refine, writing modes), `ml/SPEC.md` (refiner training)
- Model: `onnx-community/gemma-3-270m-it-ONNX` (q4)
