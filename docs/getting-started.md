# Getting started

This page shows how to install Whisper Local, start it, give macOS the permissions it needs, and check that it works. [Back to the README](../README.md).

**Contents**

- [Platform status](#platform-status)
- [Install](#install)
- [First run](#first-run)
- [macOS permissions](#macos-permissions)
- [The "What Whisper Local needs" checklist](#the-what-whisper-local-needs-checklist)
- [Verify it works](#verify-it-works)
- [Start on login](#start-on-login)
- [Windows (not validated yet)](#windows-not-validated-yet)

## Platform status

This fork is validated on macOS only. It was tested on Apple silicon with macOS 26.

| Part | What is tested |
|---|---|
| Dictation, dashboard, models, history | macOS 26, Apple silicon |
| Apple Intelligence features | Need macOS 26 or later, Apple silicon, and Apple Intelligence turned on |
| Other macOS versions and Intel Macs | Not tested. The project sets no minimum macOS version. |
| Windows | The code exists. It is **not validated yet**. |

## Install

You need Python 3.11, 3.12 or 3.13. This fork has no releases and no PyPI package yet. Install it from source or with pipx from GitHub.

**From source** (best if you want to change the code):

```bash
git clone https://github.com/vpr1995/whisper-local.git
cd whisper-local
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
whisper-local
```

`wl` is a short name for the same command.

**With pipx** (the app gets its own environment):

```bash
pipx install git+https://github.com/vpr1995/whisper-local
whisper-local
```

## First run

1. Run `whisper-local` in a terminal.
2. The app picks a speech model for your computer and language. It prints the choice and the reason. For English on a Mac, the choice is Parakeet v3 (about 670 MB).
3. The app downloads the model. One progress line shows the percentage and the megabytes.
4. A small welcome window opens on the first start only. It has three tips.
5. Open the dashboard from the menu bar icon: **Open Whisper Local**. Or run `whisper-local --settings`.
6. Click in any text field. Hold `fn+ctrl`, speak, and release.

After the download, the app works offline. To pick a model yourself, see [Models](models.md). To use the app without access to huggingface.co, see [Offline models](offline-models.md).

## macOS permissions

macOS blocks keyboard hooks and typing into other apps until you allow them.

| Permission | Why the app needs it | When macOS asks |
|---|---|---|
| Accessibility | To see your hotkeys and to type text into other apps | On the first start. The app keeps running. When you allow it, the hotkeys start with no restart. |
| Microphone | To hear you | The first time you record |

When you start the app from a terminal, macOS lists the terminal app (Terminal, iTerm, or another one). Turn that app on. Open **System Settings > Privacy & Security** to change a permission later.

The Accessibility and Microphone rows on the About page have an **Open Settings** button. It opens the correct System Settings pane.

## The "What Whisper Local needs" checklist

The dashboard's **About** page has a checklist. It shows if each part is ready. Each missing part has a button that fixes it.

![The About page: Microphone, Accessibility, Speech model and Apple Intelligence all show a green check](screenshots/dashboard-about.png)

| Row | Ready when | If it is not ready |
|---|---|---|
| Microphone access | macOS allows the microphone. Before the first recording, the row says macOS has not asked yet. | **Open Settings** opens the Microphone pane |
| Accessibility access | The app is in the Accessibility list | **Open Settings** opens the Accessibility pane |
| Speech model | The model files are on disk | **Open Models** opens the Models page |
| Apple Intelligence | Your Mac supports it and the helper is built. This row is optional. Dictation works without it. | **Install tools** starts Apple's Command Line Tools install. **Open Settings** opens the Apple Intelligence pane. |
| Ollama | Ollama answers. This row shows only when Ollama is the AI in use. | **Get Ollama** opens the download page |

Click **Check again** after you fix an item.

## Verify it works

```bash
whisper-local --doctor
```

`--doctor` checks the Python version, packages, config files, audio devices, the model cache, the hotkey backend, Accessibility, the AI provider, per-app rules and recent errors. It does not change your settings. It exits with 0 when all checks pass.

```bash
whisper-local --selftest
```

`--selftest` records half a second from the microphone, loads the model, transcribes the recording, and tests the clipboard. Run it when dictation does nothing. It prints a fix hint for each failed step. It uses the clipboard, so do not run it while you copy something important.

The About page has the same checks. Its Diagnostics section has a **Health check** button (`--doctor`), a **Self-test** button and a **Bundle logs** button.

If something fails, see [Troubleshooting](troubleshooting.md). To send a bug report, run `whisper-local --bundle-logs` and attach the zip file.

## Start on login

Turn on **Start on login** in the dashboard's Settings page. Or run `whisper-local --enable-autostart`. Run `whisper-local --disable-autostart` to undo it. The [FAQ](faq.md#how-do-i-autostart-on-login) explains the macOS helper app.

## Windows (not validated yet)

The code has Windows support. The maintainer has not tested it on a current build. Treat every Windows note in these docs as unverified. The Windows defaults are `Ctrl+Win` to record and `Alt+Win` for voice commands. Config files live in `%APPDATA%\whisperkey`.

Next: take the [dashboard tour](dashboard.md) or see all [hotkeys](hotkeys.md).
