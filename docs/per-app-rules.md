# Profiles, per-app rules and styles

Different apps need different behavior. A chat app wants a casual message that sends at once. An email wants full sentences. A code editor wants no automatic capital letters. This page explains profiles, per-app rules and writing styles. [Back to the README](../README.md).

**Contents**

- [Profiles](#profiles)
- [Writing styles](#writing-styles)
- [Per-app rules](#per-app-rules)
- [The rules that ship with the app](#the-rules-that-ship-with-the-app)
- [Write your own rule](#write-your-own-rule)

The **Style** page in the dashboard sets profiles and writing styles. Its **Per-app rules** card lists your rules and has an **Edit file** link.

![The Style page](screenshots/dashboard-style.png)

## Profiles

A profile is a preset for the whole app. Click one on the Style page.

| Profile | What it sets |
|---|---|
| Dictation | General voice typing. Auto-paste on. Voice commands on. Model `base`. |
| Chat | Push-to-talk. Auto-paste on. A casual prompt for the model. |
| Code | Copy only, no auto-paste. A prompt for identifiers and file paths. |
| Notes | Copy only. Voice commands off. Model `base`. A notes prompt. |
| Translate | Translates any spoken language into English. Auto-paste on. |

**A profile overwrites those settings, including the model.** Dictation and Notes set `whisper.model: base`, so applying one replaces `auto` or your own choice. The Style page warns you with "Applying one overwrites those settings, including the model." Translate needs a Whisper model. Parakeet cannot translate.

**Add your own** opens `profiles.yaml` in `~/.whisperkey`. The file holds `active:` and a `profiles:` list. Each profile has a `description` and `overrides`, which use the same keys as `user_settings.yaml`.

## Writing styles

A style sets capital letters, end punctuation and casing in one choice.

| Style | Result |
|---|---|
| `formal` | Capitalized, always ends with punctuation. For email and documents. |
| `casual` | Capitalized, no final period. For chat. |
| `very_casual` | All lowercase, except acronyms and your own terms. No final period. |
| `verbatim` | No automatic capitals, periods or lists. For code and terminals. |

Set a style for every app with `postprocess.style`, or on the Style page. An empty value means no style, and the individual toggles apply. A per-app rule can pick a different style.

To make your own style, add it under `postprocess.styles` with any of these keys: `capitalize_first`, `ensure_punctuation`, `strip_trailing_period`, `inline_formatting`, `lowercase`, `list_formatting`.

```yaml
postprocess:
  styles:
    shouty: {capitalize_first: true, ensure_punctuation: true}
```

## Per-app rules

Before the app delivers a dictation, it checks which app is in front. If a rule matches, the rule overrides your global behavior for that dictation. The app reads `app_rules.yaml` again on each dictation, so edits apply at once.

A rule can set:

| Key | Effect |
|---|---|
| `auto_send` | `true` presses Enter after the paste |
| `auto_paste` | `false` copies only, `true` pastes |
| `suppress` | `true` skips delivery (the dictation is only logged) |
| `style` | A writing style for this app |
| `polish` | `true` or `false`: the AI polish for this app. See [AI polish](ai-polish.md#polish). |
| `capitalize_first`, `ensure_punctuation`, `strip_trailing_period`, `inline_formatting`, `lowercase`, `list_formatting` | Formatting toggles. They win over the rule's `style`. |
| `initial_prompt` | A hint for Whisper about this app |
| `language`, `task` | A language, or `translate` |

**How a rule matches.** A rule has a `match` list. A rule applies when any word in the list is part of the app's identity. The check ignores case. On macOS the identity is the **bundle id** (for example `com.microsoft.outlook`) and the **app name** (for example `Slack`). The app does not see window titles on macOS. So it cannot tell a web page in a browser from another page.

The app checks rules from the top. The first match wins. Your own rules come before the shipped rules.

To find the bundle id of an app, run this command in a terminal:

```bash
osascript -e 'id of app "Slack"'
```

## The rules that ship with the app

The shipped rules are `password-managers`, `terminals`, `code-editors`, `chat-apps` and `email-clients`. They sit under your `app_rules.yaml`, so updates improve them.

| Rule id | Settings | Matches on macOS today |
|---|---|---|
| `password-managers` | `suppress: true` | Yes: 1Password, Bitwarden, KeePassXC, Apple Passwords |
| `terminals` | `auto_paste: false`, `style: verbatim`, `polish: false` | Yes: Terminal, iTerm2, Alacritty, WezTerm, Warp, Ghostty, kitty |
| `code-editors` | `auto_paste: false`, `style: verbatim`, `polish: false`, a code prompt | Yes: VS Code, Cursor, Windsurf, JetBrains IDEs, Zed, Sublime Text, Xcode |
| `chat-apps` | `auto_send: true`, `style: casual`, `list_formatting: false` | Yes: Slack, Discord, Telegram, WhatsApp |
| `email-clients` | `style: formal`, `polish: true` | Yes: Apple Mail, Outlook, Thunderbird |

Each shipped rule lists the Windows program names and the macOS bundle ids. If your app is not in the list, add a rule with its bundle id. The next section shows how.

## Write your own rule

Edit `~/.whisperkey/app_rules.yaml`. Use the **Edit file** link on the Style page to open it. The file hot-reloads.

```yaml
rules:
  # An app the shipped rules do not know: copy only, verbatim, no AI polish
  - match: ["com.example.myeditor"]
    auto_paste: false
    style: verbatim
    polish: false

  # Change one thing in a shipped rule: name its id
  - id: chat-apps
    auto_send: false

  # Turn off a shipped rule
  - id: terminals
    disabled: true
```

Run `whisper-local --doctor` to see how many rules are in effect.

Check the bundle id of your app with the `osascript` command above.
