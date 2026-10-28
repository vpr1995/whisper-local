# dashboard/api/history.py
# Python API for the History page: searchable, paged transcripts from the journal,
# delete, export and "Fix this everywhere" corrections. Also home of
# friendly_app_name(), which turns raw app ids into display names for any page.

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 200


# ── Friendly app names ───────────────────────────────────────────────────────

# Display names for ids whose last segment isn't how people write the app
# (keys are lowercase; macOS bundle ids and Windows exe names without ".exe").
_KNOWN_APP_NAMES = {
    # macOS bundle ids
    'com.apple.terminal': 'Terminal',
    'com.apple.safari': 'Safari',
    'com.apple.mail': 'Mail',
    'com.apple.notes': 'Notes',
    'com.apple.finder': 'Finder',
    'com.apple.messages': 'Messages',
    'com.apple.ichat': 'Messages',
    'com.apple.textedit': 'TextEdit',
    'com.apple.dt.xcode': 'Xcode',
    'com.apple.iwork.pages': 'Pages',
    'com.google.chrome': 'Chrome',
    'com.microsoft.vscode': 'VS Code',
    'com.microsoft.edgemac': 'Edge',
    'com.microsoft.word': 'Word',
    'com.microsoft.excel': 'Excel',
    'com.microsoft.powerpoint': 'PowerPoint',
    'com.microsoft.teams2': 'Teams',
    'com.microsoft.teams': 'Teams',
    'com.tinyspeck.slackmacgap': 'Slack',
    'com.googlecode.iterm2': 'iTerm',
    'com.todesktop.230313mzl4w4u92': 'Cursor',
    'com.hnc.discord': 'Discord',
    'md.obsidian': 'Obsidian',
    'notion.id': 'Notion',
    'org.mozilla.firefox': 'Firefox',
    'ru.keepcoder.telegram': 'Telegram',
    'net.whatsapp.whatsapp': 'WhatsApp',
    # Windows executables
    'code': 'VS Code',
    'msedge': 'Edge',
    'winword': 'Word',
    'powerpnt': 'PowerPoint',
    'wt': 'Windows Terminal',
    'windowsterminal': 'Windows Terminal',
    'explorer': 'File Explorer',
    'cmd': 'Command Prompt',
    'powershell': 'PowerShell',
    'pwsh': 'PowerShell',
    'ms-teams': 'Teams',
    'onenote': 'OneNote',
    'whatsapp': 'WhatsApp',
    'chrome': 'Chrome',
    'idea64': 'IntelliJ IDEA',
    'pycharm64': 'PyCharm',
    'rider64': 'Rider',
}

# Namespace segments that tell nothing about the app ("com.acme.Tool" -> "Tool").
_BUNDLE_PREFIXES = ('com', 'org', 'net', 'io', 'app', 'co', 'de', 'ru', 'md')


# "com.microsoft.outlook" -> "Outlook", "OUTLOOK.EXE" -> "Outlook". Unknown ids
# degrade to the last meaningful segment, capitalised; an empty id stays empty.
def friendly_app_name(app_id: str) -> str:
    raw = (app_id or '').strip()
    if not raw:
        return ''
    lowered = raw.lower()
    if lowered.endswith('.exe'):
        lowered = lowered[:-4]
    if lowered in _KNOWN_APP_NAMES:
        return _KNOWN_APP_NAMES[lowered]

    # Bundle ids have dots ("com.vendor.app"); an exe name was already stripped of its suffix.
    segments = [part for part in lowered.split('.') if part]
    meaningful = [part for part in segments if part not in _BUNDLE_PREFIXES] or segments
    name = meaningful[-1]
    # Separators in exe names ("my_app", "some-tool") read better as spaces.
    words = name.replace('_', ' ').replace('-', ' ').split()
    return ' '.join(word.capitalize() for word in words) or raw


# ── Entry shaping ────────────────────────────────────────────────────────────

# One journal entry in the shape the page renders. `id` is not stored in the
# journal, so the page sends timestamp + text back to identify an entry.
def _present_entry(entry: dict) -> dict:
    text = entry.get('text') or ''
    app_id = entry.get('app') or ''
    return {
        'timestamp': entry.get('timestamp', ''),
        'text': text,
        'app': friendly_app_name(app_id),
        'app_id': app_id,
        'words': len(text.split()),
        'duration_s': entry.get('duration_s') or 0,
    }


# Case-insensitive substring filter over text and app name (so "outlook" finds
# those dictations too). An empty query keeps everything.
def _filter_entries(entries: list, query: str) -> list:
    needle = (query or '').strip().lower()
    if not needle:
        return entries
    return [entry for entry in entries
            if needle in (entry.get('text') or '').lower()
            or needle in friendly_app_name(entry.get('app') or '').lower()]


# Plain-text/Markdown rendering for Export. Markdown gets a heading per entry,
# plain text a "[time] app" line, both newest first like the page.
def _format_export(entries: list, as_markdown: bool) -> str:
    blocks = []
    for entry in entries:
        stamp = (entry.get('timestamp') or '').replace('T', ' ')
        app_name = friendly_app_name(entry.get('app') or '')
        header = f"{stamp}" + (f" · {app_name}" if app_name else '')
        blocks.append(f"### {header}\n\n{entry.get('text', '')}" if as_markdown
                      else f"[{header}]\n{entry.get('text', '')}")
    return '\n\n'.join(blocks) + '\n'


class HistoryApi:
    # ── Reading ──────────────────────────────────────────────────────────────

    # A page of transcripts (newest first) matching `query`. `saving_enabled`
    # is False when history.retention_days is 0 (nothing is stored at all).
    def get_history(self, query: str = '', offset: int = 0, limit: int = DEFAULT_PAGE_SIZE) -> dict:
        from ... import transcript_log
        retention = transcript_log.parse_retention_days(
            (self._load_config().config.get('history') or {}).get('retention_days'))
        # Journal order is append order; sort anyway so day grouping can never interleave.
        all_entries = sorted(transcript_log.load_transcripts(),
                             key=lambda entry: entry.get('timestamp') or '', reverse=True)
        matched = _filter_entries(all_entries, query)
        offset, limit = max(0, int(offset)), max(1, int(limit))
        page = matched[offset:offset + limit]
        return {
            'saving_enabled': retention != 0,
            'total': len(all_entries),
            'matched': len(matched),
            'entries': [_present_entry(entry) for entry in page],
            'has_more': offset + limit < len(matched),
        }

    # ── Editing ──────────────────────────────────────────────────────────────

    # Deletes one journal entry; the page identifies it by timestamp + text.
    def delete_history_entry(self, timestamp: str, text: str) -> bool:
        from ... import transcript_log
        return transcript_log.delete_transcript(timestamp, text)

    # "Fix this everywhere": persists a whole-word, case-insensitive replacement
    # that applies from the next dictation. Returns False for empty/no-op input.
    def add_correction(self, wrong_text: str, right_text: str) -> bool:
        from ...corrections import add_replacement
        return add_replacement(wrong_text, right_text)

    # ── Export ───────────────────────────────────────────────────────────────

    # Saves every transcript matching `query` to a .md/.txt chosen in a native
    # save dialog. Returns {saved, path?, count?}; saved=False means cancelled.
    def export_history(self, query: str = '') -> dict:
        import webview
        from ... import transcript_log
        matched = _filter_entries(transcript_log.load_transcripts(), query)
        if not matched:
            return {'saved': False, 'count': 0}

        save_mode = getattr(getattr(webview, 'FileDialog', None), 'SAVE', None) or webview.SAVE_DIALOG
        chosen = webview.windows[0].create_file_dialog(
            save_mode, save_filename='whisper-local-history.md',
            file_types=('Markdown (*.md)', 'Text (*.txt)'))
        if not chosen:
            return {'saved': False, 'count': len(matched)}
        path = Path(chosen if isinstance(chosen, str) else chosen[0])
        path.write_text(_format_export(matched, as_markdown=path.suffix.lower() != '.txt'), encoding='utf-8')
        return {'saved': True, 'path': str(path), 'count': len(matched)}
