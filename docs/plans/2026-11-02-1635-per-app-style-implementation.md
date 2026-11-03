# Per-App Writing Style

As a *user* I want **a writing style for each app** so Slack gets casual text, Mail gets formal text and my terminal gets verbatim text, without a manual switch.

## Background

- Today `postprocess.style` is one global value. All apps get the same style.
- The user must select each app and its style. The app ships no default mapping.
- An app without a row uses the global style.

### Current flow

```
hotkey press → _begin_recording()
release      → transcribe → postprocess(text, cfg)   # cfg['style'] = global style
             → deliver → foreground.get_foreground_app()  # read for history only
```

### Target flow

```
hotkey press → _begin_recording() → self._recording_app_id = foreground exe id
release      → transcribe
             → cfg = with_app_style(cfg, self._recording_app_id)   # per-app style or global
             → postprocess(text, cfg) → deliver
```

The app reads the app id at hotkey press. If the user switches windows while they speak, the style still follows the app where they started.

## Implementation Plan

1. Config and style resolution
- [x] Add `app_styles: {}` under `postprocess` in `config.defaults.yaml`, with a comment that shows the format (`update_user_setting` writes only keys that exist)
  - ✅ Added below `styles: {}` with a macOS and a Windows example
- [x] Add `with_app_style(postprocess_cfg, app_id) -> dict` to `styles.py`. It returns a copy of the config with `style` set from the app row, or the same config when no row matches
  - ✅ Lowercases the app id. Ignores a missing table, a non-dict row and a row without a style
- [x] Unit tests: row match, no row, empty app id, case of the id, a row with an unknown style (`resolve_style` already logs it and leaves formatting unchanged)
  - ✅ `tests/test_app_styles.py`: 7 tests pass. Full suite passes

2. Capture the app at hotkey press
- [x] In `state_manager._begin_recording()`, read `foreground.get_foreground_app()['exe']` first, before the overlay shows, and keep it in `self._recording_app_id`
- [x] In the dictation path (`state_manager.py:636`), call `with_app_style()` before `postprocess()`
- [x] Retry path (`_retry_worker`) and rephrase: no change. They use the global style
- [x] Log the resolved style at debug level: `"Style for com.tinyspeck.slackmacgap: casual"`

3. Platform app lists: `platform/{macos,windows}/apps.py`
- [x] Mirrored API: `list_pickable_apps() -> list[{id, name}]`, sorted by name, no duplicate ids
- [x] macOS: scan `/Applications`, `/Applications/Utilities`, `/System/Applications`, `/System/Applications/Utilities` and `~/Applications`, plus one level of subfolders (vendor folders such as `Adobe Photoshop 2026/`). Read `Info.plist` with stdlib `plistlib`. id = `CFBundleIdentifier` in lowercase. name = `CFBundleDisplayName`, then `CFBundleName`, then the folder stem. Skip bundles that have no id
- [x] Windows: list the running processes that have a visible, titled top-level window (`EnumWindows`, ctypes, the same way as `foreground.py`). Add the exe names from `transcript_log.load_transcripts()`. Exclude the app's own process and the shell exes (`explorer.exe`, `progman.exe`, ...). id = exe basename in lowercase. name = the exe's FileDescription (pywin32 `GetFileVersionInfo`), then the exe stem
- [x] Register `apps` in `platform/__init__.py` for both platforms
- [x] Unit test for the macOS scanner with fake `.app` folders in a temp dir (scan roots are a parameter)

4. Dashboard API: `dashboard/api/style.py`
- [x] `get_app_styles()` → `{rows: [{id, name, style}], styles: [...]}`
- [x] `list_pickable_apps()` → the platform list without the apps that already have a row
- [x] `set_app_style(app_id, name, style_id)`: validate the style against `available_styles()`, then write the full `postprocess.app_styles` dict
- [x] `remove_app_style(app_id)`
- [x] Tests in `tests/test_dashboard_models_style_about.py`

5. Style page: `dashboard/web/pages/style.js`
- [x] New section "Style per app" below "Writing style". Caption: "Apps you don't add here use the writing style above."
- [x] One row per app: app name, style dropdown, remove button. A change saves at once (same as the other Style controls)
- [x] "Add app" button opens a picker with a search box and the app list. The user clicks an app, then selects its style
- [x] Empty state: "No apps yet. Add an app to give it its own style."
- [x] Note under the section: "Browsers count as one app. Gmail and Slack in Chrome get the same style."

6. Docs and manual test
- [x] `docs/project-index.md`: update the Styles row ("one global choice" → global style plus per-app styles) and add `apps.py` to the platform tree
- [x] Move the story to `docs/roadmap/completed.md`
- [ ] **Manual test on macOS:** add Slack = casual, Notes = formal. Dictate into each app. Each app gets its style. An app without a row gets the global style
- [ ] **Manual test on Windows:** the picker lists running apps and apps from History. Do the same dictation test


### Progress notes
- ✅ `config_manager.EXTENSIBLE_PATHS` now has `postprocess.app_styles`. Without it, a save dropped every row (the round-trip test proves it)
- ✅ macOS scan: 99 apps in < 0.4 s on the dev Mac
- ✅ Docs: `writing-styles.md`, `dashboard.md`, CHANGELOG (Added, and the Removed note from `3e166d9` corrected)
- ✅ Test fixture in `test_smoke.py` sets `_recording_app_id`. New pipeline test: the app style replaces the global style
- ✅ 574 tests pass
- ⏳ Windows `apps.py` is not run yet (no Windows machine here)

## Implementation Details

### Why not inside `text_postprocess.py`

The style override goes in `state_manager`, not in `postprocess()`. `postprocess()` is a pure text function, and the dashboard and tests call it without an app. The state manager knows the app, so it changes the config before the call. `postprocess()` still expands the style name at line 122 as it does now.

```python
# state_manager.py, dictation path
postprocess_cfg = self.config_manager.get_postprocess_config()
postprocess_cfg = with_app_style(postprocess_cfg, self._recording_app_id)
```

```python
# styles.py
# The config with the style the user chose for this app, or unchanged when the
# app has no row (the global style then applies).
def with_app_style(postprocess_cfg: dict, app_id: str) -> dict:
    row = (postprocess_cfg.get('app_styles') or {}).get((app_id or '').lower())
    if not isinstance(row, dict) or not row.get('style'):
        return postprocess_cfg
    return {**postprocess_cfg, 'style': row['style']}
```

### Config format

```yaml
postprocess:
  app_styles:
    com.tinyspeck.slackmacgap: {name: Slack, style: casual}
    slack.exe: {name: Slack, style: casual}
```

The config keeps the display name, so the Style page shows "Slack", not the bundle id. The app also keeps the name when the app is not installed.

### Decisions

- **App id = `foreground.get_foreground_app()['exe']`.** This is the bundle id on macOS and the exe name on Windows. History already stores this value, so the picker and the foreground check use the same id.
- **Different picker sources per OS.** macOS: all installed apps (a plist scan is easy and complete). Windows: running apps plus History (installed apps on Windows need Start Menu shortcut parsing, and Store apps share one host exe).
- **No file browser.** The lists cover almost all cases.
- **Fallback window:** no special case. The text uses the style of the app where the recording started.
- **Live streaming delivery** types raw text and does not call `postprocess()`. No change to that path. The global style does not apply there either.

## Scope

| File | Changes |
|------|---------|
| `config.defaults.yaml` | `postprocess.app_styles: {}` with comment |
| `styles.py` | `with_app_style()` |
| `state_manager.py` | Capture the app id at hotkey press. Apply `with_app_style()` before `postprocess()` |
| `platform/macos/apps.py` | New: installed-app scan |
| `platform/windows/apps.py` | New: running apps plus History |
| `platform/__init__.py` | Import `apps` |
| `dashboard/api/style.py` | 4 new methods |
| `dashboard/web/pages/style.js` | "Style per app" section and app picker |
| `tests/` | Style resolution, macOS scanner, Style API |
| `docs/project-index.md`, `docs/roadmap/completed.md` | Docs |

## Success Criteria

- [ ] An app with a row gets its style. All other apps get the global style
- [ ] The style follows the app where the hotkey was pressed, also when the user switches windows while they speak
- [ ] macOS picker lists the installed apps, also apps that are not running
- [ ] Windows picker lists the running apps and the apps from History, with no duplicates
- [ ] A change on the Style page applies to the next dictation with no restart (postprocess hot-reload)
- [ ] A row with a deleted custom style does not cause an error. Formatting stays as configured
- [ ] All tests pass

## Risks

| Risk | Mitigation |
|------|------------|
| `_save_user_overrides` does not write nested dicts correctly | `postprocess.styles` already is a nested dict and saves. Test the round trip in phase 4 |
| The overlay takes focus before the app reads the foreground app | Read the app id as the first line of `_begin_recording()` |
| A macOS scan of a large Applications folder is slow | Read only `Info.plist`, one level deep. Expect < 300 ms. Measure it |
| Windows UWP apps all show as `applicationframehost.exe` | Known limit. These apps share one row |
