# Writing styles

A style sets capital letters, end punctuation and casing in one choice. [Back to the README](../README.md).

The **Style** page in the dashboard sets the style. The change applies to your next dictation.

| Style | Result |
|---|---|
| `formal` | Capitalized, always ends with punctuation. For email and documents. |
| `casual` | Capitalized, no final period. For chat. |
| `very_casual` | All lowercase, except acronyms and your own terms. No final period. |
| `verbatim` | No automatic capitals, periods or lists. For code and terminals. |

Set the global style on the Style page, or with `postprocess.style`. An empty value means no style. Then the individual toggles apply.

## Style per app

An app can have its own style. All other apps use the global style.

1. Open the dashboard Style page.
2. Under **Style per app**, click **+ Add app**.
3. Click the app in the list. Use the search box to find it.
4. Choose a style, then click **Add**.

On macOS, the list shows all installed apps. On Windows, the list shows the apps that are open now and the apps in your History. To add a Windows app that is not in the list, open the app first.

The app reads the focused app when you press the hotkey. If you switch windows while you speak, the style of the first app applies.

A browser is one app. Gmail and Slack in Chrome get the same style. Windows Store apps share one row.

The rows are in `user_settings.yaml`. The key is the app id: the bundle id on macOS, the exe name on Windows.

```yaml
postprocess:
  app_styles:
    com.tinyspeck.slackmacgap: {name: Slack, style: casual}
    code.exe: {name: Visual Studio Code, style: verbatim}
```

To make your own style, add it under `postprocess.styles`. Use any of these keys: `capitalize_first`, `ensure_punctuation`, `strip_trailing_period`, `inline_formatting`, `lowercase`, `list_formatting`.

```yaml
postprocess:
  styles:
    shouty: {capitalize_first: true, ensure_punctuation: true}
```

The **+ Custom** chip on the Style page opens `user_settings.yaml`, where you add it.
