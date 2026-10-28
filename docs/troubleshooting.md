# Troubleshooting

A table of symptoms, causes and fixes. [Back to the README](../README.md). This fork is validated on macOS only. Rows about Windows are marked "not validated yet".

Most issues are caught by `whisper-local --doctor` or `whisper-local --selftest`. Run one of those first.

If you need to file a bug, run:

```bash
whisper-local --bundle-logs
```

This creates a zip with redacted logs, `--doctor` output, and recent crashes. Attach it to your issue.

## Quick reference

The first rows are for macOS. The rows about the Windows `.exe` come after them. This fork has no releases yet, so they apply to older upstream builds.

| Symptom | Most likely cause | Fix |
|---|---|---|
| Hotkeys do nothing | Accessibility access is missing, or another app uses the same keys | Open the dashboard's About page, find **Accessibility access** in the checklist and click **Open Settings**. If the checklist is green, change `hotkey.recording_hotkey` and restart |
| "Mic captured pure silence" or no sound recorded | macOS did not give the microphone to your terminal app | System Settings > Privacy & Security > Microphone. The About checklist has an **Open Settings** button |
| The terminal, VS Code or a password manager still gets text typed in | The shipped rules for these apps list Windows `.exe` names. They do not match macOS apps. | Add a rule with the app's bundle id. See [Per-app rules](per-app-rules.md#the-rules-that-ship-with-the-app) |
| The transform hotkey `win+alt+1` does not respond with Command+Option+1 | The macOS hotkey code ignores `win`, so the key is `option+1` | Press `option+1`, or set your own hotkey in `transforms.yaml` |
| Windows (not validated yet): "Windows protected your PC" (SmartScreen) when running the `.exe` | The app is open-source but not code-signed (certificates cost money) | Click **More info → Run anyway**. Verify the download with the `.sha256` file from the release if you want certainty |
| First `.exe` launch sits for minutes with no window | It is installing the app's libraries from PyPI (one-time; Python itself is built into the `.exe` since 0.22.0) | Wait it out — subsequent launches are instant. Then the model download (141–670 MB, with a progress line) follows |
| `.exe` 0.21.0 or older: `download failed: …python-build-standalone… operation timed out` | Old builds fetched Python from github.com on first launch with their own HTTP client, which ignores the Windows proxy settings | Use 0.22.0 or later, which has Python built in. If a corporate network also blocks PyPI, install from source with your usual proxy settings instead |
| Startup fails with `OSError: [WinError 193]` on a Snapdragon / Windows-on-ARM PC (0.22.0 and older) | The voice-activity library chose its DLL from the PC's CPU (ARM64) instead of Python's (x64, emulated) and loaded the 32-bit one | Fixed in 0.22.1. On a native ARM64 Python, voice activity detection is turned off rather than crashing (`--doctor` shows it), but the speech engine has no ARM64 build yet, so install the **x64** Python from python.org; it runs under emulation |
| One antivirus engine on VirusTotal flags `whisper-local.exe` | The `.exe` is unsigned and unpacks a standard Python runtime, which some heuristics treat as suspicious (e.g. "vcruntime140 sideloading": Python ships that Microsoft runtime DLL next to `python.exe`) | Verify the file instead of trusting the score: `gh attestation verify whisper-local.exe --repo vpr1995/whisper-local` proves it was built by this repo's CI, or compare its SHA-256 with the release's `.sha256` file. Or install from source (see [Getting started](getting-started.md)) |
| Recording starts but no text appears | Default Whisper task changed accidentally | Models > Language = `auto` (or your language); Style > Profile = `Dictation` |
| First word is cut off | (Should be fixed by pre-roll) Mic was slow to wake | Set `audio.host` to `WASAPI` on Windows (not validated yet). On a Mac, check that **Keep microphone open** is on (Settings > Recording options) |
| Hotkey does nothing | Another app holds the same combination | Change the Dictate key in Settings; restart |
| Text appears as ALL CAPS | Caps Lock was on when hotkey pressed | Push-to-talk releases reset modifiers — try toggling Caps Lock off |
| "Mic captured pure silence" in `--selftest` | OS denied mic permission to your shell/terminal | macOS: System Settings > Privacy & Security > Microphone. Windows (not validated yet): Settings > Privacy & Security > Microphone |
| Tray icon missing (Windows, not validated yet) | Windows hides tray icons by default | Click the `^` in the taskbar, drag the Whisper Local icon out |
| App seems frozen on launch | Whisper model is downloading (first run) | Wait — `tiny` is ~75 MB, `large` is ~2.9 GB. Watch `~/.whisperkey/app.log` |
| "Could not register hotkeys" | Another instance is already running, or another app owns the combo | `whisper-local --quit` then relaunch. Or change the hotkey |
| GPU not used despite `device: cuda` | CTranslate2 CUDA runtime not installed | Run `whisper-local --setup` and accept the GPU install prompt |
| Transcription accuracy is poor | Tiny model + noisy environment | Models page: pick `base` or `small`. Settings > Recording options > Reduce background noise (run `pip install -e '.[noise]'` first) |
| `Rephrase failed: Ollama did not answer` in tray | Ollama is the active AI and is not running | `ollama serve` in a terminal, or use Apple Intelligence (dashboard > Style > AI) |
| `Apple Intelligence is getting ready` / `needs Command Line Tools` | The Apple Intelligence helper is not built yet | Run `xcode-select --install`, then restart. Run `--doctor` for the reason |
| Text appears twice in chat apps | `auto_send_key` pressed by accident | The Esc cancel + retry; consider switching off auto-send for that app via `app_rules.yaml` |
| Audio device unplugged mid-recording | USB mic disconnect | App should auto-recover to default input. If not, restart and tell the maintainer |
| Settings change didn't take effect | Hotkeys / device changes require restart | Quit and relaunch the app |
| `pyperclip.PyperclipException` on macOS | Missing `pbcopy`/`pbpaste` permissions | Open System Settings → Privacy → Accessibility, add Terminal |
| Stale lock prevents launch | Previous instance crashed without cleanup | `whisper-local --quit` clears the lock. As a last resort delete the hidden lock file `~/.WhisperKeyLocal.lock` (on Windows, not validated yet: `%APPDATA%\whisperkey\WhisperKeyLocal.pid`) |

## When all else fails

1. **Reset the app to defaults:** use Settings > Reset..., or delete `~/.whisperkey/user_settings.yaml` (on Windows, not validated yet: `%APPDATA%\whisperkey\user_settings.yaml`). The defaults will be regenerated on next launch. Your hotwords/commands/transforms are kept.
2. **Reinstall:** `pip uninstall whisper-local`, then install again from source (see [Getting started](getting-started.md)).
3. **Wipe everything:** delete the entire `~/.whisperkey` directory — this resets settings, dictionary, stats, transcript history.
4. **File a bug:** run `whisper-local --bundle-logs` and attach the resulting zip to a new issue via [bug report template](https://github.com/vpr1995/whisper-local/issues/new?template=bug_report.yml).
