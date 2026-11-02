# Writing styles

A style sets capital letters, end punctuation and casing in one choice. [Back to the README](../README.md).

The **Style** page in the dashboard sets the style. The change applies to your next dictation.

| Style | Result |
|---|---|
| `formal` | Capitalized, always ends with punctuation. For email and documents. |
| `casual` | Capitalized, no final period. For chat. |
| `very_casual` | All lowercase, except acronyms and your own terms. No final period. |
| `verbatim` | No automatic capitals, periods or lists. For code and terminals. |

The style applies to every app. Set it on the Style page, or with `postprocess.style`. An empty value means no style. Then the individual toggles apply.

To make your own style, add it under `postprocess.styles`. Use any of these keys: `capitalize_first`, `ensure_punctuation`, `strip_trailing_period`, `inline_formatting`, `lowercase`, `list_formatting`.

```yaml
postprocess:
  styles:
    shouty: {capitalize_first: true, ensure_punctuation: true}
```

The **+ Custom** chip on the Style page opens `user_settings.yaml`, where you add it.
