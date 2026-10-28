# dashboard/api/home.py
# Python API for the Home page: usage stat tiles, configured hotkeys and the most
# recent transcripts. Read-only; everything comes straight from files on disk.

import logging

logger = logging.getLogger(__name__)

RECENT_TRANSCRIPT_LIMIT = 5


class HomeApi:
    # Headline numbers for the stat tiles (words today/total, streak, time saved).
    def get_home_stats(self) -> dict:
        from ... import stats
        try:
            typing_wpm = self._load_config().config.get('stats', {}).get('typing_wpm')
            return stats.summarize_usage(typing_wpm or stats.DEFAULT_TYPING_WPM)
        except Exception as e:
            logger.warning(f"Could not summarise stats: {e}")
            return stats.summarize_usage()

    # Configured hotkeys as {label, keys, help, group}. `keys` is the combo split
    # into parts (e.g. ["ctrl", "option"]) so the UI can draw one keycap each.
    def get_hotkeys(self) -> list:
        from ...cheat_sheet import collect_hotkey_rows, collect_transform_hotkey_rows
        rows = [dict(row, group='core') for row in collect_hotkey_rows(self._load_config().config)]
        try:
            from ...transforms import TransformsManager
            rows += [dict(row, group='transform')
                     for row in collect_transform_hotkey_rows(TransformsManager().list_transforms())]
        except Exception as e:
            logger.debug(f"Transform hotkeys unavailable: {e}")
        return [{
            'label': row['label'],
            'keys': [part.strip() for part in row['key'].split('+') if part.strip()],
            'help': row['help'],
            'group': row['group'],
        } for row in rows if row['key']]

    # Newest-first transcripts for the Recent card. Text is the full transcript
    # (the UI clamps it); timestamps are the journal's local ISO strings.
    def get_recent_transcripts(self, limit: int = RECENT_TRANSCRIPT_LIMIT) -> list:
        from ... import transcript_log
        from .history import friendly_app_name
        return [{
            'text': entry.get('text', ''),
            'app': friendly_app_name(entry.get('app', '')),
            'timestamp': entry.get('timestamp', ''),
            'words': len((entry.get('text') or '').split()),
        } for entry in transcript_log.load_transcripts()[:max(1, int(limit))]]
