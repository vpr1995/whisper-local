# Privacy

Your voice stays on your computer. Whisper Local runs the speech model locally, and during dictation it makes no network call. This page lists every network call the app can make. [Back to the README](../README.md).

## The only network calls

| # | When | What goes out | Where | Can you turn it off? |
|---|---|---|---|---|
| 1 | The first time you use a model | A request to download the model files | `huggingface.co` | After the download, the model is cached and never downloaded again. |
| 2 | If you accept the GPU setup prompt | `pip install` of GPU runtime packages | PyPI, or `repo.radeon.com` for AMD | Opt-in. Not used on a Mac. |
| 3 | If you turn on **Check for updates** | The request for the latest release. Only the app version is in it. | `api.github.com` | Off by default. `update_check.enabled: false`. |
| 4 | If Ollama is the AI in use | The text of a polish, transform or rephrase | The Ollama server you set. The default is `localhost`. | Choose Apple Intelligence, or turn the AI steps off. |
| 5 | Live preview, first use only | A request to download the small streaming model | `huggingface.co` | Live preview is off by default. |

Apple Intelligence makes no network call. It runs on your Mac.

During dictation there is **zero network traffic**. The entry points are in [`onboarding.py`](../src/whisper_key/onboarding.py), [`update_check.py`](../src/whisper_key/update_check.py), [`ai_provider.py`](../src/whisper_key/ai_provider.py) and the model download code. Each is behind a setting or a prompt. Search the source for `urllib` if you want to check.

The local API server (`--serve`) listens on your own computer. It does not call out. See [Local API](local-api.md).

## What the app stores

All files are in `~/.whisperkey` on your Mac.

| File | Holds |
|---|---|
| `transcripts.jsonl` | The text of your last 2000 dictations. This feeds the History page. |
| `stats.jsonl` | Counts, durations and app names. Never the text. |
| `app.log` | Logs. It holds your dictated text only if `logging.log_transcriptions` is `true`. The default is `false`. |
| `audit.log` | Off by default. Records when text was delivered, without the text. |

The "Retry last dictation" feature keeps your last recording in memory only. The app never writes it to disk.

Set `history.retention_days` to keep history for fewer days, or to `0` to store none. Delete any of these files at any time. `whisper-local --uninstall` removes your settings, data and the autostart entry. See [History and vocabulary](history-and-vocabulary.md#how-long-history-is-kept).

## Bug reports

`whisper-local --bundle-logs` makes a zip with redacted logs and diagnostics. Look inside the zip before you share it.
