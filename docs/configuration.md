# Configuration

Most settings are in the dashboard. Every setting is also a plain text file that you can edit. This page lists the config files, the key settings and every command-line flag. [Back to the README](../README.md).

**Contents**

- [Config files](#config-files)
- [Edit and reset settings](#edit-and-reset-settings)
- [Key settings](#key-settings)
- [Cleanup levels](#cleanup-levels)
- [Spoken punctuation](#spoken-punctuation)
- [Command-line reference](#command-line-reference)

## Config files

Everything is in `~/.whisperkey` on macOS. (Windows, not validated yet: `%APPDATA%\whisperkey`.) The dashboard's **About > Config folder > Open** opens it.

| File | What it holds |
|---|---|
| `user_settings.yaml` | Your settings. Only the keys you change. |
| `commands.yaml` | Your voice commands. See [Voice commands](voice-commands.md). |
| `transcripts.jsonl`, `stats.jsonl` | History text, and usage numbers. See [Privacy](privacy.md). |
| `app.log`, `dashboard.log` | Logs |
| `apple_intelligence/` | The compiled Apple Intelligence helper |

The shipped defaults are in the package: [`config.defaults.yaml`](../src/whisper_key/config.defaults.yaml) is the full list of settings with comments. Do not edit it. Your files sit on top of the defaults. Updates improve the shipped commands, and your changes stay.

## Edit and reset settings

- **Dashboard.** Settings, Models and Style change the common settings. Most changes to `postprocess.*` and `history.*` apply on your next dictation. Other changes need a restart. The dashboard shows a **Restart now** button.
- **File.** **Settings > Your settings > Open file** opens `user_settings.yaml`. Edit and restart the app.
- **Reset.** **Settings > Reset...** puts every setting back to its default, and keeps your dictionary. Or delete `user_settings.yaml` and restart. Your hotwords and commands are kept.
- **Back up.** `whisper-local --export-settings DIR` and `--import-settings DIR`.

## Key settings

| Setting | Default | Notes |
|---|---|---|
| `whisper.model` | `auto` | A model name, or `auto`. See [Models](models.md). |
| `whisper.backend` | `faster_whisper` | `parakeet` is the other choice |
| `whisper.device` | `cpu` | `cuda` for an NVIDIA or AMD GPU. See [GPU setup](gpu-setup.md). |
| `whisper.compute_type` | `int8` | `int8`, `float16` or `float32` |
| `whisper.language` | `auto` | Auto-detect, or a language code |
| `whisper.languages` | `[]` | The languages you dictate in, such as `[en, fr]` |
| `whisper.hotwords` | `[]` | Names and terms to favor. See [History and vocabulary](history-and-vocabulary.md#hotwords). |
| `whisper.unload_after_minutes` | `0` | Free the model after this many idle minutes. `0` keeps it loaded. |
| `hotkey.recording_hotkey` | `fn+ctrl` | See [Hotkeys](hotkeys.md) for all hotkey settings |
| `hotkey.recording_mode` | `push_to_talk` | `push_to_talk` or `toggle` |
| `hotkey.double_tap_to_lock` | `false` | Hands-free recording |
| `postprocess.cleanup` | `light` | See [Cleanup levels](#cleanup-levels) |
| `postprocess.style` | empty | `formal`, `casual`, `very_casual` or `verbatim` |
| `postprocess.ai.provider` | `auto` | See [AI polish](ai-polish.md) |
| `postprocess.ai.polish` | `false` | Polish every dictation with the AI |
| `postprocess.list_formatting` | `false` | Spoken lists become numbered lists |
| `postprocess.snippets` | `[]` | Spoken shortcuts that expand to text |
| `clipboard.auto_paste` | `true` | `false` copies to the clipboard only |
| `clipboard.paste_preserve_clipboard` | `true` | Put back your clipboard after the paste |
| `audio.input_device` | `default` | A microphone |
| `audio.mic_always_on` | `true` | Keep the microphone open so the first word is not cut |
| `audio.whisper_mode.enabled` | `false` | Boost quiet speech |
| `vad.vad_realtime_enabled` | `true` | Stop the recording after silence |
| `vad.vad_silence_timeout_seconds` | `30` | How long a silence ends the recording |
| `voice_commands.enabled` | `true` | Voice command mode |
| `overlay.enabled` | `true` | The floating pill. `overlay.position` sets where. |
| `history.retention_days` | empty | `0` stores no history. `7` keeps a week. |
| `stats.typing_wpm` | `40` | Your typing speed, for the "time saved" number |
| `update_check.enabled` | `false` | A daily check for a new release |
| `logging.level` | `INFO` | Use `DEBUG` when you report a bug |

The comments in `config.defaults.yaml` describe each key.

On macOS, `clipboard.delivery_method` should stay `paste`. The `type` method works on Windows only.

## Cleanup levels

`postprocess.cleanup` decides how much of what you said gets tidied. All levels run offline, with no AI.

| Level | What changes |
|---|---|
| `none` | Exactly what the model heard |
| `light` (default) | Stutters ("I I think") and "um" and "uh" are dropped |
| `medium` | Light, plus "scratch that" and "at 2, actually 3" |
| `high` | Medium, plus spoken times, emails and web addresses |

| You say | You get | Needs |
|---|---|---|
| "I I think we we should go" | I think we should go | `light` |
| "Let's meet at 2, actually 3" | Let's meet at 3 | `medium` |
| "book the flight, scratch that, cancel it" | cancel it | `medium` |
| "john at example dot com at 3 pm" | john@example.com at 3 PM | `high` |
| "Groceries: first, milk. Second, eggs." | a numbered list | `postprocess.list_formatting` |

You can pin any single toggle on or off in `user_settings.yaml`, whatever the level. "Actually" and similar cues change text only when a number, time, weekday or month follows. So "I actually like it" is never changed.

## Spoken punctuation

Say a punctuation word while you dictate. The app changes the word into the mark. This is on by default, in every writing style and at every cleanup level.

| You say | You get |
|---|---|
| "new paragraph" | A blank line |
| "new line" | A line break |
| "period" or "full stop" | `.` |
| "comma" | `,` |
| "question mark" | `?` |
| "exclamation mark" or "exclamation point" | `!` |
| "colon" / "semicolon" | `:` / `;` |
| "open quote" / "close quote" | `"` |
| "open paren" / "close paren" | `(` / `)` |
| "open bracket" / "close bracket" | `[` / `]` |
| "dash" | ` — ` |
| "hyphen" | `-` |

Example: "Hi Sam comma new paragraph thanks for the notes period" gives:

```
Hi Sam,

thanks for the notes.
```

To stop this, set `postprocess.inline_formatting: false`. The words are English. For other languages, or to add your own words, see the [FAQ](faq.md).

## Command-line reference

The command is `whisper-local`. The short name is `wl`. Run a second copy and it **takes over**: the old one stops cleanly.

| Command | What it does |
|---|---|
| `whisper-local` | Run the app |
| `--doctor` | Run diagnostics |
| `--selftest` | Test the microphone, model, transcription and clipboard |
| `--version` | Print the version |
| `--quit` | Stop the running app |
| `--export-settings DIR` | Back up `user_settings.yaml` and `commands.yaml` |
| `--import-settings DIR` | Restore from a backup |
| `--bundle-logs [PATH]` | Make a redacted zip for a bug report |
| `--enable-autostart`, `--disable-autostart` | Start the app when you log in, or stop doing it |
| `--uninstall` | Remove settings, data and the autostart entry. It asks before it removes models. |
| `--test` | Run a separate test copy of the app |
