# dashboard/api/about.py
# Python API for the About page: app links, the "What Whisper Local needs" checklist,
# the full hotkey cheat sheet, and the diagnostics tools (doctor report, log bundle,
# self-test, open log/config folders).
# The doctor and the log bundler print to stdout, so their output is captured here
# and turned into structured rows for the page.

import contextlib
import datetime
import io
import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_URL = "https://github.com/vpr1995/whisper-local"
ABOUT_LINKS = {
    'github': REPO_URL,
    'issues': f"{REPO_URL}/issues",
    'discussions': f"{REPO_URL}/discussions",
}

# doctor.py prints "[OK]", "[!]", "[X]" or "[i]" before each check; map them to row statuses.
DOCTOR_STATUS = {'OK': 'ok', '!': 'warn', 'X': 'fail', 'i': 'info'}
ANSI_PATTERN = re.compile(r'\x1b\[[0-9;]*m')
DOCTOR_CHECK_PATTERN = re.compile(r'^\[(OK|!|X|i)\]\s+(.*)$')
# Lines the doctor prints that are neither a section header nor a check.
DOCTOR_NOISE_PATTERN = re.compile(r'^(Whisper Local\s+.\s+Doctor|=+|All checks passed\.|\d+ issue\(s\) found\.)')


# ── Pure helpers ─────────────────────────────────────────────────────────────

# Turns the doctor's coloured console report into
# [{section, checks: [{status, name, detail}]}]. Indented lines under a check
# (e.g. the recent log errors) are appended to that check's detail.
def parse_doctor_report(report_text: str) -> list:
    sections = []
    for raw_line in ANSI_PATTERN.sub('', report_text).splitlines():
        if not raw_line.strip() or DOCTOR_NOISE_PATTERN.match(raw_line.strip()):
            continue
        check_match = DOCTOR_CHECK_PATTERN.match(raw_line)
        if check_match and sections:
            name, detail = _split_name_and_detail(check_match.group(2))
            sections[-1]['checks'].append({'status': DOCTOR_STATUS[check_match.group(1)], 'name': name, 'detail': detail})
        elif raw_line[0].isspace() and sections and sections[-1]['checks']:
            last_check = sections[-1]['checks'][-1]
            last_check['detail'] = (last_check['detail'] + '\n' + raw_line.strip()).strip()
        elif not raw_line[0].isspace():
            sections.append({'section': raw_line.strip(), 'checks': []})
    return [section for section in sections if section['checks']]


# "Python version  3.12.1" -> ("Python version", "3.12.1"): the doctor separates a
# check's name from its detail with two or more spaces.
def _split_name_and_detail(text: str) -> tuple:
    parts = re.split(r'\s{2,}', text.strip(), maxsplit=1)
    return parts[0], (parts[1] if len(parts) > 1 else '')


# {ok, warn, fail, info} counts across a parsed report.
def count_doctor_statuses(sections: list) -> dict:
    counts = {'ok': 0, 'warn': 0, 'fail': 0, 'info': 0}
    for section in sections:
        for check in section['checks']:
            counts[check['status']] += 1
    return counts


# Where the log bundle goes: the Desktop when there is one, else the home folder.
def bundle_output_folder() -> Path:
    desktop = Path.home() / 'Desktop'
    return desktop if desktop.is_dir() else Path.home()


# ── Self-test run state ──────────────────────────────────────────────────────

# Progress of the one self-test run, written by its thread and polled from JS.
class SelfTestRun:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {'state': 'idle', 'stages': []}

    # Runs `stages` ([(name, title, fn)]) on a daemon thread. False if already running.
    def start(self, stages: list) -> bool:
        with self._lock:
            if self._state['state'] == 'running':
                return False
            self._state = {'state': 'running', 'stages': [
                {'name': name, 'title': title, 'status': 'pending', 'detail': '', 'fix': ''}
                for name, title, _fn in stages]}
        threading.Thread(target=self._run, args=(stages,), daemon=True, name='dashboard-selftest').start()
        return True

    def _run(self, stages: list):
        for index, (_name, _title, run_stage) in enumerate(stages):
            self._set_stage(index, status='running')
            try:
                ok, message = run_stage()
            except Exception as error:  # a crashing stage is a failed stage, not a dead run
                ok, message = False, (f"Crashed: {error}", '')
            reason, fix = (message, '') if ok else message
            self._set_stage(index, status='ok' if ok else 'fail', detail=reason, fix=fix)
        with self._lock:
            self._state['state'] = 'done'

    def _set_stage(self, index: int, **changes):
        with self._lock:
            self._state['stages'][index].update(changes)

    def snapshot(self) -> dict:
        with self._lock:
            return {'state': self._state['state'], 'stages': [dict(stage) for stage in self._state['stages']]}


_selftest_run = SelfTestRun()
_diagnostics_lock = threading.Lock()  # stdout capture is process-wide: one doctor/bundle at a time


class AboutApi:
    # ── Identity ─────────────────────────────────────────────────────────────

    def get_about_info(self) -> dict:
        from ...utils import get_user_app_data_path, get_version
        return {'version': get_version(), 'links': ABOUT_LINKS, 'config_folder': get_user_app_data_path()}

    # ── Requirements checklist ───────────────────────────────────────────────

    # The checklist rows (see requirements_check.py). `check_again` makes Apple
    # Intelligence ask again instead of answering from its cache.
    def get_requirements(self, check_again: bool = False) -> list:
        from ...requirements_check import collect_requirements
        return collect_requirements(self._load_config(), force_refresh=bool(check_again))

    # Runs one of the checklist's system fixes by name (only names in requirements_check
    # do anything). Returns False for an unknown name or a fix that did not start.
    def run_requirement_fix(self, fix_id: str) -> bool:
        from ...requirements_check import run_fix
        return run_fix(fix_id)

    # ── Hotkey cheat sheet ───────────────────────────────────────────────────

    # Every hotkey, including unset ones (keys == []), as {label, keys, help, group}.
    def get_hotkey_cheat_sheet(self) -> list:
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
            'keys': [part.strip() for part in (row['key'] or '').split('+') if part.strip()],
            'help': row['help'],
            'group': row['group'],
        } for row in rows]

    # ── Diagnostics ──────────────────────────────────────────────────────────

    # Runs the --doctor checks and returns them as rows: {sections, counts}.
    # Takes a few seconds (it probes audio devices), which is fine: pywebview runs
    # each API call on its own thread, so the page stays responsive.
    def run_diagnostics(self) -> dict:
        from ...doctor import run_doctor
        buffer = io.StringIO()
        with _diagnostics_lock, contextlib.redirect_stdout(buffer):
            run_doctor()
        sections = parse_doctor_report(buffer.getvalue())
        return {'sections': sections, 'counts': count_doctor_statuses(sections)}

    # Writes the redacted bug-report zip to the Desktop. Returns {ok, path, folder}.
    def bundle_logs_to_desktop(self) -> dict:
        from ...bundle_logs import bundle_logs
        folder = bundle_output_folder()
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        output_path = folder / f"whisper-local-bundle-{stamp}.zip"
        try:
            with _diagnostics_lock, contextlib.redirect_stdout(io.StringIO()):
                bundle_logs(str(output_path))
        except Exception as e:
            logger.error(f"Bundling logs failed: {e}")
            return {'ok': False, 'error': str(e)}
        return {'ok': True, 'path': str(output_path), 'folder': str(folder)}

    # Opens the folder the bundle was written to. No path argument on purpose: the
    # page must not be able to ask for arbitrary folders.
    def show_bundle_folder(self) -> bool:
        from ...utils import open_file
        open_file(bundle_output_folder())
        return True

    def open_log_file(self) -> bool:
        from ...utils import open_file
        log_path = Path(self._load_config().get_log_file_path())
        if not log_path.exists():
            return False
        open_file(log_path)
        return True

    def open_config_folder(self) -> bool:
        from ...utils import get_user_app_data_path, open_file
        open_file(get_user_app_data_path())
        return True

    # ── Self-test ────────────────────────────────────────────────────────────

    # Starts the mic / model / transcription / clipboard checks on a thread; poll
    # get_selftest_progress(). Refuses when the model is not downloaded yet, because
    # the transcription stage would otherwise start a silent multi-hundred-MB download.
    def start_selftest(self) -> dict:
        from ...selftest import STAGES
        if not self._selected_model_is_cached():
            return {'ok': False, 'error': "Download the speech model first (Models page), then run the self-test."}
        if not _selftest_run.start(STAGES):
            return {'ok': False, 'error': "A self-test is already running."}
        return {'ok': True}

    def get_selftest_progress(self) -> dict:
        return _selftest_run.snapshot()

    # True when the model the app would load is already on disk.
    def _selected_model_is_cached(self) -> bool:
        from ...requirements_check import selected_speech_model
        _model_key, is_cached = selected_speech_model(self._load_config().get_whisper_config())
        return is_cached
