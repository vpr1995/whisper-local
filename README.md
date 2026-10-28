<div align="center">

# Whisper Local

### Offline AI dictation for your Mac

**Hold a key. Speak. Your words appear at the cursor.**
Your voice stays on your computer. No account. No cloud. No telemetry.

[![Tests](https://github.com/vpr1995/whisper-local/actions/workflows/test.yml/badge.svg)](https://github.com/vpr1995/whisper-local/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](docs/getting-started.md)
[![Code of Conduct](https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg)](CODE_OF_CONDUCT.md)

</div>

<p align="center">
  <img src="docs/screenshots/dashboard-home.png" width="49%" alt="The Home page: your dictation shortcut, usage numbers and recent dictations">
  <img src="docs/screenshots/dashboard-history.png" width="49%" alt="The History page: search of past dictations, grouped by day">
</p>
<p align="center">
  <img src="docs/screenshots/style-ai.png" width="49%" alt="The Style page: AI provider, polish per app type, AI transforms and per-app rules">
  <img src="docs/screenshots/dashboard-about.png" width="49%" alt="The About page: a checklist of what Whisper Local needs, all items ready">
</p>

Whisper Local turns speech into text in any app. You hold a shortcut, you talk, and the text appears where your cursor is. A speech model runs on your Mac to do this. It uses [Whisper](https://github.com/openai/whisper) through [faster-whisper](https://github.com/SYSTRAN/faster-whisper), or NVIDIA Parakeet.

## Platform status

> **This fork is validated on macOS only.** Tested on Apple silicon with macOS 26. Apple Intelligence needs macOS 26. The other features do not need it, but no other macOS version is tested.
> The code has Windows support. **It is not validated yet.** The maintainer will test it on a Windows PC later. Windows notes in the docs are marked "not validated yet".
> This fork has no releases yet. Install it from source (see below).

## Install

Needs Python 3.11, 3.12 or 3.13.

```bash
git clone https://github.com/vpr1995/whisper-local.git
cd whisper-local
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
whisper-local            # or the short name: wl
```

Or install the command with pipx:

```bash
pipx install git+https://github.com/vpr1995/whisper-local
whisper-local
```

On the first start the app downloads one speech model, then works offline. macOS asks for Accessibility and Microphone access. [Getting started](docs/getting-started.md) shows every step.

## What it does

- **Dictate in any app.** Hold `fn+ctrl`, talk, release. Cancel with `shift`. [Hotkeys](docs/hotkeys.md)
- **A dashboard window.** Home, History, Models, Vocabulary, Style, Settings and About, with a live status bar. [Dashboard tour](docs/dashboard.md)
- **Picks a speech model for you.** Parakeet v3 for English and 24 other European languages, Whisper `large-v3-turbo` on a GPU, Whisper `base` for other languages. [Models](docs/models.md)
- **Clean text.** Stutters and "um" go. "Scratch that" and "at 2, actually 3" work. Spoken lists, times and emails get formatted. [Configuration](docs/configuration.md)
- **AI polish, transforms and rephrase.** They run on Apple Intelligence on your Mac, or on Ollama. Polish is on for email apps by default. [AI polish](docs/ai-polish.md)
- **Different behavior per app.** Casual in chat, formal in email, auto-send in Slack. Profiles and writing styles. [Per-app rules](docs/per-app-rules.md)
- **Searchable history.** Search, copy, delete many at once, and fix a misheard word everywhere. [History and vocabulary](docs/history-and-vocabulary.md)
- **Your own words.** Hotwords, replacements and snippets. [History and vocabulary](docs/history-and-vocabulary.md)
- **Voice commands.** Say a phrase to press a shortcut, type text or run a command. [Voice commands](docs/voice-commands.md)
- **Live preview.** See words in the floating pill while you speak (experimental). [Streaming](docs/streaming.md)
- **A local Whisper API.** `whisper-local --serve` gives an OpenAI-style endpoint on your machine. [Local API](docs/local-api.md)
- **Checks and logs.** `--doctor`, `--selftest` and `--bundle-logs` find problems fast. [Troubleshooting](docs/troubleshooting.md)

## Documentation

| Page | What it covers |
|---|---|
| [Getting started](docs/getting-started.md) | Install, first run, macOS permissions, checks |
| [Dashboard](docs/dashboard.md) | A tour of every dashboard page |
| [Hotkeys](docs/hotkeys.md) | Every shortcut and its default |
| [Models](docs/models.md) | Model choice, GPU, idle unload, live preview |
| [AI polish](docs/ai-polish.md) | Apple Intelligence, Ollama, transforms, rephrase |
| [History and vocabulary](docs/history-and-vocabulary.md) | History, hotwords, replacements, snippets |
| [Per-app rules](docs/per-app-rules.md) | Profiles, per-app behavior, writing styles |
| [Voice commands](docs/voice-commands.md) | The `commands.yaml` format |
| [Configuration](docs/configuration.md) | Config files, key settings, command-line flags |
| [Local API](docs/local-api.md) | The OpenAI-style server |
| [Privacy](docs/privacy.md) | Every network call the app can make |
| [FAQ](docs/faq.md) and [Troubleshooting](docs/troubleshooting.md) | Questions and fixes |
| [GPU setup](docs/gpu-setup.md), [Streaming](docs/streaming.md), [Offline models](docs/offline-models.md) | Special setups |
| [CHANGELOG](CHANGELOG.md) | Release notes |

For maintainers: [distribution](docs/distribution.md), [publishing to PyPI](docs/publishing-to-pypi.md), [project index](docs/project-index.md), [CONTRIBUTING](CONTRIBUTING.md).

## Privacy

Your audio never leaves your computer. During dictation the app makes no network call. The only calls are the one-time model download and a few opt-in features. [Privacy](docs/privacy.md) lists all of them.

## Contributing

Bug reports, fixes and docs are welcome. This project has no support SLA. See [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and [SECURITY.md](SECURITY.md). All contributions are MIT licensed.

## Credit

Whisper Local builds on a lot of open-source work. The full list is in [AUTHORS.md](AUTHORS.md). MIT licensed, see [LICENSE](LICENSE).

---

<div align="center">

**Maintained by [Prudhvi Reddy Vemireddy](https://github.com/vpr1995) ([@vpr1995](https://github.com/vpr1995))**
<sub>Fork of drajb/whisper-local by Rohit Burani, itself based on whisper-key-local by Pin Wang.</sub>

[GitHub](https://github.com/vpr1995) · [Discussions](https://github.com/vpr1995/whisper-local/discussions) · [Report a bug](https://github.com/vpr1995/whisper-local/issues/new?template=bug_report.yml) · [Request a feature](https://github.com/vpr1995/whisper-local/issues/new?template=feature_request.yml)

</div>
