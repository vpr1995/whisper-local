# stats.py
# Usage metrics only — character counts, durations and the app dictated into,
# appended to `stats.jsonl`. Deliberately stores NO transcript text (that lives
# in transcript_log.py) so this file stays safe to share in a bug report. Backs
# the dashboard Home stat tiles, the once-a-day summary notification and streak
# tracking. Writes are lock-serialised because deliveries can land from several
# threads at once.

import datetime
import json
import logging
import threading
from pathlib import Path
from typing import Optional

from .utils import get_user_app_data_path

STATS_FILE = "stats.jsonl"
DAILY_NOTIFY_MARKER = "stats-last-notify.txt"

# Serialise appends — back-to-back deliveries (continuous mode) can write from
# different threads. Append-only, so this is lighter than transcript_log's lock.
_write_lock = threading.Lock()

# Fallback for stats.typing_wpm: a typical average typing speed.
DEFAULT_TYPING_WPM = 40
CHARS_PER_WORD = 5

logger = logging.getLogger(__name__)


# Time it would have taken to type `words` at `typing_wpm`, minus the time
# spent speaking them. Never negative: a slow dictation saved nothing.
def _minutes_saved(words: int, speaking_seconds: float, typing_wpm: float) -> float:
    return max(0.0, (words / typing_wpm) - (speaking_seconds / 60))


def _safe_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def record_transcription(char_count: int, duration_seconds: float, app: Optional[str] = None):
    if char_count <= 0:
        return
    try:
        entry = {
            'ts': datetime.datetime.now().isoformat(timespec='seconds'),
            'chars': char_count,
            'duration_s': round(float(duration_seconds), 2),
            'app': (app or '').lower(),
        }
        path = Path(get_user_app_data_path()) / STATS_FILE
        with _write_lock:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + '\n')
    except Exception as e:
        logger.debug(f"Stats record failed: {e}")


def maybe_show_daily_summary(notify_callback, typing_wpm: float = DEFAULT_TYPING_WPM) -> Optional[str]:
    today = datetime.date.today().isoformat()
    marker = Path(get_user_app_data_path()) / DAILY_NOTIFY_MARKER

    try:
        last = marker.read_text(encoding='utf-8').strip()
    except OSError:
        last = ''

    if last == today:
        return None

    path = Path(get_user_app_data_path()) / STATS_FILE
    if not path.exists():
        try:
            marker.write_text(today, encoding='utf-8')
        except OSError:
            pass
        return None

    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    chars = 0
    count = 0
    seconds = 0.0
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get('ts', '').startswith(yesterday):
                    count += 1
                    chars += _safe_int(entry.get('chars'))
                    seconds += _safe_float(entry.get('duration_s'))
    except OSError:
        return None

    try:
        marker.write_text(today, encoding='utf-8')
    except OSError:
        pass

    if count == 0:
        return None

    words = chars // CHARS_PER_WORD
    minutes_saved = _minutes_saved(words, seconds, typing_wpm)
    msg = f"Yesterday: {count} dictations, {words:,} words, ~{minutes_saved:.0f} min saved"
    try:
        notify_callback(msg)
    except Exception:
        pass
    return msg


def _compute_streaks(active_days: set, today: datetime.date) -> tuple:
    if not active_days:
        return 0, 0
    sorted_days = sorted(active_days)
    longest = 1
    current = 1
    for i in range(1, len(sorted_days)):
        prev = datetime.date.fromisoformat(sorted_days[i - 1])
        curr = datetime.date.fromisoformat(sorted_days[i])
        if (curr - prev).days == 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1

    if today.isoformat() in active_days:
        cs = 1
        d = today
        while True:
            d -= datetime.timedelta(days=1)
            if d.isoformat() in active_days:
                cs += 1
            else:
                break
        return cs, longest
    yesterday = (today - datetime.timedelta(days=1)).isoformat()
    if yesterday in active_days:
        cs = 1
        d = today - datetime.timedelta(days=1)
        while True:
            d -= datetime.timedelta(days=1)
            if d.isoformat() in active_days:
                cs += 1
            else:
                break
        return cs, longest
    return 0, longest


# ── Structured summary (dashboard Home page) ─────────────────────────────────

# Every parsable line of stats.jsonl, oldest first. Empty when the file is
# missing; malformed lines are skipped so one bad write can't hide the rest.
def load_entries() -> list:
    path = Path(get_user_app_data_path()) / STATS_FILE
    entries = []
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


# The headline numbers as a plain dict, in a shape a UI can render. Words use
# the CHARS_PER_WORD convention used everywhere else in this module, so the
# Home tiles and the daily summary always agree.
def summarize_usage(typing_wpm: float = DEFAULT_TYPING_WPM, today: Optional[datetime.date] = None) -> dict:
    today = today or datetime.date.today()
    entries = load_entries()

    total_chars = sum(_safe_int(e.get('chars')) for e in entries)
    total_seconds = sum(_safe_float(e.get('duration_s')) for e in entries)
    today_prefix = today.isoformat()
    today_chars = sum(_safe_int(e.get('chars')) for e in entries
                      if str(e.get('ts', '')).startswith(today_prefix))
    active_days = {str(e['ts'])[:10] for e in entries if e.get('ts')}
    streak, longest_streak = _compute_streaks(active_days, today)

    total_words = total_chars // CHARS_PER_WORD
    return {
        'sessions': len(entries),
        'total_words': total_words,
        'today_words': today_chars // CHARS_PER_WORD,
        'active_days': len(active_days),
        'streak_days': streak,
        'longest_streak_days': longest_streak,
        'minutes_saved': round(_minutes_saved(total_words, total_seconds, typing_wpm), 1),
    }
