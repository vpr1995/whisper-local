# Hotkeys

Whisper Local has global shortcuts. They work in every app. This page lists each one with its default on macOS, and explains push-to-talk, hands-free lock and the stop keys. [Back to the README](../README.md).

**Contents**

- [All shortcuts](#all-shortcuts)
- [Push-to-talk and tap mode](#push-to-talk-and-tap-mode)
- [Hands-free lock](#hands-free-lock)
- [Stop, Enter, polish and cancel](#stop-enter-polish-and-cancel)
- [Other shortcuts](#other-shortcuts)
- [Change a shortcut](#change-a-shortcut)
- [Windows defaults (not validated yet)](#windows-defaults-not-validated-yet)

## All shortcuts

The dashboard shows your current shortcuts on Home ("Your hotkeys") and on About ("Hotkey cheat sheet"). The command `whisper-local --cheat-sheet` opens that list.

| Action | Setting | macOS default | What it does |
|---|---|---|---|
| Dictate | `hotkey.recording_hotkey` | `fn+ctrl` | Hold to record. Release to stop. |
| Stop | `hotkey.stop_key` | `fn` | Stops the recording and delivers the text. |
| Stop and press Enter | `hotkey.auto_send_key` | `option` | Stops, pastes, and presses Enter. Needs auto-paste on. |
| Stop and polish | `hotkey.stop_polish_key` | off | Stops and polishes this one dictation with the AI. |
| Cancel | `hotkey.cancel_combination` | `shift` | Throws the recording away. |
| Voice command | `hotkey.command_hotkey` | `fn+command` | Records a spoken command instead of text. |
| Rephrase selection | `hotkey.rephrase_hotkey` | `ctrl+option+command` | Rewrites selected text with a spoken instruction. |
| Paste last dictation | `hotkey.paste_last_hotkey` | `ctrl+option+v` | Types your last dictation again. |
| Pause all hotkeys | `hotkey.pause_hotkey` | `ctrl+option+command+space` | Switches every shortcut off or on. |
| Transforms | `hotkey` in `transforms.yaml` | see [AI polish](ai-polish.md#transforms) | Rewrite selected text with a stored prompt. |

`fn` is the Globe key on a Mac keyboard. On macOS the Escape key is reserved by the system, so the cancel key is `shift`.

If a shortcut is empty, it is off. You cannot turn off the Dictate key and the Stop key.

![The More shortcuts view in Settings](screenshots/settings-more-shortcuts.png)

## Push-to-talk and tap mode

The default mode is **push-to-talk**. Hold `fn+ctrl` and speak. Release the keys to stop. The text goes to the cursor.

You can switch to **tap mode** in Settings (**How it works: Tap to start and stop**), or set `hotkey.recording_mode: toggle`. In tap mode you press `fn+ctrl` once to start. You press the stop key (`fn`) to stop.

## Hands-free lock

Hands-free lock lets you record without holding the keys. It works in push-to-talk mode only. It is off by default.

1. Turn on **Hands-free lock** in Settings, or set `hotkey.double_tap_to_lock: true`.
2. Double-tap the Dictate shortcut. The recording keeps going when you let go.
3. Tap the shortcut once more to stop.

A single hold works as before. `hotkey.double_tap_window_ms` (default 400) sets how fast the second tap must follow the first. Find it under **Settings > More shortcuts > Hands-free lock**.

## Stop, Enter, polish and cancel

While you record, three keys can end the recording, and one can cancel it.

- **Stop** (`fn`). Delivers the text. In push-to-talk mode, releasing the Dictate keys does the same.
- **Stop and press Enter** (`option`). Delivers the text, then presses Enter. Use it in chat apps. It needs **Paste automatically** on. A per-app rule can also send Enter for you. See [Per-app rules](per-app-rules.md).
- **Stop and polish** (off by default). Delivers the text after an AI polish, even where polish is off. See [AI polish](ai-polish.md#stop-and-polish).
- **Cancel** (`shift`). Discards the recording. Nothing is typed.

## Other shortcuts

- **Voice command.** Press the shortcut, say a trigger phrase, and stop the recording. If the phrase matches a command in `commands.yaml`, the command runs. See [Voice commands](voice-commands.md).
- **Rephrase selection.** Select text, hold the shortcut, say how to change it, and release. See [AI polish](ai-polish.md#rephrase).
- **Paste last dictation.** Types your last dictation again. Use it when the text went to the wrong window.
- **Pause all hotkeys.** Press it once to turn every Whisper Local shortcut off. Another app can then use the same keys. Press it again to turn them on. The **Pause / resume** button on the About page does the same.

## Change a shortcut

Open **Settings** (the main page for the Dictate key, **More shortcuts** for the others). Click **Change** and press the new keys. Or edit `user_settings.yaml`:

```yaml
hotkey:
  recording_hotkey: "fn+ctrl"
  stop_key: "fn"
  auto_send_key: "option"
```

Write keys in lowercase, joined by `+`. The macOS modifier names are `cmd` (or `command`), `ctrl`, `shift`, `option` (or `alt`) and `fn`. **Restart the app after you change a hotkey.** The dashboard asks you to restart.

The macOS hotkey code ignores the name `win`. Use `cmd` for the Command key.

If a shortcut does nothing, another app may own the same keys. Change the key and restart. See [Troubleshooting](troubleshooting.md).

## Windows defaults (not validated yet)

These defaults exist in the code. They are not validated on a current build.

| Action | Windows default |
|---|---|
| Dictate | `ctrl+win` |
| Stop | `ctrl` |
| Stop and press Enter | `alt` |
| Cancel | `esc` |
| Voice command | `alt+win` |
| Rephrase selection | `ctrl+shift+win` |
| Paste last dictation | `alt+shift+z` |
| Pause all hotkeys | `ctrl+alt+win` |
