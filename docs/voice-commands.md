# Voice Commands

[Back to the README](../README.md) · [Hotkeys](hotkeys.md) · [Configuration](configuration.md)

Voice command mode lets you trigger actions by speaking. Press **Fn+Command** on macOS (default; `Alt+Win` on Windows, not validated yet) to start recording, speak a trigger phrase, then press the stop key. If the transcription matches a trigger, the associated action runs.

## Configuration

Voice commands are configured in `commands.yaml`, located in your config folder:
- **Windows:** `%APPDATA%\whisperkey\commands.yaml`
- **macOS:** `~/.whisperkey/commands.yaml`

You can open this folder from the dashboard's About page: **Config folder**.

## Format

Each command has a **trigger** phrase and exactly one action: `run`, `hotkey`, or `type`.

```yaml
commands:
  # Send a keyboard shortcut
  - trigger: "undo"
    hotkey: "ctrl+z"

  # Deliver pre-written text
  - trigger: "my email"
    type: "user@example.com"

  # Run a shell command
  - trigger: "open notepad"
    run: 'notepad.exe'
```

| Field | Description |
|-------|-------------|
| **trigger** | Phrase to match (case-insensitive, punctuation ignored) |
| **run** | Shell command (`cmd.exe` on Windows, `/bin/sh` on macOS) |
| **hotkey** | Keyboard shortcut to send (e.g. `ctrl+z`, `win+left`) |
| **type** | Pre-written text to deliver to the active window |

## Notes for macOS

The shipped commands have a Windows value and a macOS value, written as `"windows value | macos: mac value"`. The app uses the value for your system. For example, "undo" sends `cmd+z` on a Mac, and "open calculator" runs `open -a Calculator`.

| Trigger | Windows | macOS |
|---|---|---|
| undo / select all / paste that | `ctrl+z` / `ctrl+a` / `ctrl+v` | `cmd+z` / `cmd+a` / `cmd+v` |
| snap left / snap right | `win+left` / `win+right` | `fn+ctrl+left` / `fn+ctrl+right` (macOS 15 or later) |
| maximize / minimize | `win+up` / `win+down` | `fn+ctrl+f` / `cmd+m` |
| show desktop | `win+d` | `f11` |
| open notepad / calculator / browser | `notepad.exe` / `calc.exe` / `start …` | `open -a TextEdit` / `open -a Calculator` / `open …` |
| open downloads / new terminal | `explorer …` / `wt.exe` | `open ~/Downloads` / `open -a Terminal` |
| screenshot / lock screen | `snippingtool` / `LockWorkStation` | `screencapture -i -c` / `pmset displaysleepnow` |

You can use the same `| macos:` form in your own `commands.yaml` and `transforms.yaml`. On a Mac, `win` in a hotkey means the Command key.

The window commands (snap, maximize, show desktop) are not tested yet. If one does not work, change it in your `commands.yaml`.

## Matching

- Triggers are matched as substrings — saying "please open notepad" matches `"open notepad"`
- Longer triggers are checked first, so `"open notepad plus plus"` won't accidentally match `"open notepad"`
- First match wins

## Command Types

### Hotkey commands

Send keyboard shortcuts to the active window. Use `+` to combine keys.

```yaml
  - trigger: "undo"
    hotkey: "ctrl+z"
  - trigger: "select all"
    hotkey: "ctrl+a"
  - trigger: "snap left"
    hotkey: "win+left"
  - trigger: "show desktop"
    hotkey: "win+d"
```

### Type commands

Deliver pre-written text to the active window using the same method as transcription (clipboard paste or direct typing, depending on your `clipboard.delivery_method` setting).

If you stop recording with the **auto-send key** (Option on macOS, Alt on Windows), Enter is sent after the text — useful for chat apps and terminals.

```yaml
  - trigger: "my email"
    type: "user@example.com"
  - trigger: "my address"
    type: "123 Main Street, City, State 12345"
```

### Shell commands

Run any shell command. The command runs asynchronously — it won't block the app.

```yaml
  - trigger: "open notepad"
    run: 'notepad.exe'
  - trigger: "open browser"
    run: 'start https://www.google.com'
  - trigger: "open downloads"
    run: 'explorer "%USERPROFILE%\Downloads"'
  - trigger: "lock screen"
    run: 'rundll32.exe user32.dll,LockWorkStation'
```

## Hotkey

The command hotkey and stop key are configured in your user settings (`user_settings.yaml`):
```yaml
hotkey:
  command_hotkey: "alt+win | macos: fn+command"
  stop_key: "ctrl | macos: fn"
  auto_send_key: "alt | macos: option"
```

- **stop_key** — stops recording and executes the matched command
- **auto_send_key** — same as stop key, but also sends Enter after `type` commands (ignored for `run` and `hotkey`)

Both keys are shared between transcription and command modes.
