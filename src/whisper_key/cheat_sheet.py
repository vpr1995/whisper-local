# cheat_sheet.py
# Builds the rows behind the dashboard's hotkey cheat sheet (Home and About pages).
# Reads the live config, not the defaults, so what users see is what the app
# actually responds to. Pure: no UI, no I/O.

# The configured hotkeys as plain rows {label, key, help}, in display order.
# `key` is None when the user left that hotkey unset.
def collect_hotkey_rows(config: dict) -> list:
    hk = (config or {}).get('hotkey', {}) or {}

    record_help = "Hold to start recording (release to stop in push-to-talk mode)"
    if hk.get('double_tap_to_lock') and hk.get('recording_mode', 'push_to_talk') == 'push_to_talk':
        record_help += ". Double-tap to keep recording hands-free; tap again to stop"
    entries = [
        ("Record / dictate", hk.get('recording_hotkey'), record_help),
        ("Stop & paste", hk.get('stop_key'),
         "Stop recording and deliver text to the cursor"),
        ("Stop & auto-send (Enter)", hk.get('auto_send_key'),
         "Stop, paste, and press Enter — perfect for chat apps"),
        ("Cancel recording", hk.get('cancel_combination'),
         "Discard the current recording without transcribing"),
        ("Voice command mode", hk.get('command_hotkey'),
         "Say a trigger phrase from commands.yaml to run shortcuts/macros"),
        ("AI rephrase (PTT)", hk.get('rephrase_hotkey'),
         "Select text, hold, speak your instruction, release — local Ollama rewrites it"),
        ("Paste last dictation", hk.get('paste_last_hotkey'),
         "Type your last dictation again, e.g. after it landed in the wrong window"),
        ("Pause all hotkeys", hk.get('pause_hotkey'),
         "Disable every Whisper Local hotkey until pressed again"),
    ]
    return [{'label': label, 'key': key or None, 'help': help_text}
            for label, key, help_text in entries]


# Transform hotkeys as rows {label, key, help}; transforms without a hotkey are
# skipped. Takes the transform dicts (TransformsManager.list_transforms()).
def collect_transform_hotkey_rows(transforms: list) -> list:
    rows = []
    for transform in transforms or []:
        if not transform.get('hotkey'):
            continue
        prompt = transform.get('prompt', '') or ''
        rows.append({
            'label': (transform.get('name') or '').title(),
            'key': transform['hotkey'],
            'help': prompt[:60] + ('…' if len(prompt) >= 60 else ''),
        })
    return rows
