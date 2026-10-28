# window_launcher.py
# Opens the app's windows in a child process. The dashboard (pywebview) ALWAYS
# runs in one, on every platform, because webview.start() needs the main thread
# that the running app's tray loop owns. The small Tk fallback window only uses a
# child on macOS, where a Tk root on a worker thread aborts the whole process
# (issue #14); elsewhere that's a no-op.
#
# The dashboard child also talks to the running app over its pipes. Both sides
# speak newline-delimited JSON:
#   parent -> child (child's stdin):  line 1 = initial payload {"page": ..., "app_state": ...};
#                                     later {"navigate": page} to re-show it and
#                                     {"app_state": state} when the app changes state
#   child -> parent (child's stdout): {"command": name}, name in APP_COMMANDS
# The Tk fallback window keeps the one-shot protocol (one payload line, then EOF).

import json
import logging
import os
import subprocess
import sys
import threading

# Windows: spawn window children without a console. The app often runs with no
# console of its own (pyapp GUI build), and python.exe would otherwise flash one
# up every time the dashboard opens. Zero elsewhere, where the flag doesn't exist.
_NO_CONSOLE_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0

logger = logging.getLogger(__name__)

# The --window names main.py accepts; keep in sync with its argparse choices.
WINDOW_NAMES = ('fallback', 'dashboard')

# The open child per window name, so a second tray click doesn't stack another
# copy (the in-process windows have the same singleton guard). Fallback windows
# are exempt: each one carries a different transcript.
_open_children = {}
_children_lock = threading.Lock()

# What the dashboard may ask the running app to do. The parent enforces this
# list (the child is untrusted page code), so a new command needs a handler in
# the tray AND an entry here.
APP_COMMANDS = ('restart', 'retry_last_dictation', 'toggle_pause_hotkeys')

# Set by the parent in the dashboard child's environment: "your stdout is the
# command pipe". Without it (dashboard started from a terminal) the child
# leaves stdout alone, so no JSON gets printed at a person.
COMMAND_PIPE_ENV_VAR = 'WHISPER_LOCAL_APP_IPC'

# The app's last state (the same names the tray icon uses: idle, sleeping,
# recording, processing), kept so a dashboard opened later starts with it.
_latest_app_state = None


# ── Parent side ──────────────────────────────────────────────────────────────

# Returns True when the window has been handed to a child process, meaning the
# caller must NOT also open it in-process. Returns False where in-process Tk is
# fine (Windows), so callers keep their normal path. A failed spawn still
# returns True: falling back to in-process Tk on macOS would abort the app.
def open_in_child_process(name: str, payload: dict = None) -> bool:
    from .utils import tk_requires_main_thread
    if name not in WINDOW_NAMES:
        raise ValueError(f"Unknown window: {name}")
    if not tk_requires_main_thread():
        return False
    _spawn_window_child(name, payload)
    return True


# Public entry for the tray and CLI: shows the dashboard on `page` (a sidebar id
# such as "home" or "settings"). If it is already open, that window is told to
# navigate to `page` and come to the front instead of spawning a second one.
# `on_app_command(name)` receives the whitelisted commands the dashboard sends
# back; it runs on a reader thread and is only taken from the spawning call.
def open_dashboard(page: str = "home", on_app_command=None) -> None:
    with _children_lock:
        running = _open_children.get('dashboard')
        if running is not None and running.poll() is None:
            _send_to_child(running, {'navigate': page})
            return
    _spawn_dashboard_child(page, on_app_command)


# Called on every app state change (the same moment the tray icon changes):
# remembers it and forwards it to the open dashboard's status bar. Never raises.
def publish_app_state(state: str) -> None:
    global _latest_app_state
    _latest_app_state = state
    with _children_lock:
        running = _open_children.get('dashboard')
    if running is not None and running.poll() is None:
        _send_to_child(running, {'app_state': state})


# ── Spawning ─────────────────────────────────────────────────────────────────

# Starts `python -m whisper_key.main --window NAME` with the payload on stdin,
# unless a window of that name is already open. Never raises.
def _spawn_window_child(name: str, payload: dict = None):
    with _children_lock:
        running = _open_children.get(name)
        if running is not None and running.poll() is None:
            logger.info(f"The {name} window is already open")
            return
    command = [sys.executable, '-m', 'whisper_key.main', '--window', name]
    try:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, creationflags=_NO_CONSOLE_FLAGS)
        # Over a pipe rather than argv, so a transcript never shows up in `ps`.
        child.stdin.write(json.dumps(payload or {}).encode('utf-8') + b'\n')
        child.stdin.close()
    except Exception as e:
        logger.error(f"Could not open the {name} window: {e}")
        return
    if name != 'fallback':
        with _children_lock:
            _open_children[name] = child
    # Reap the child when it closes so it doesn't linger as a zombie.
    threading.Thread(target=child.wait, daemon=True, name=f'{name}-window-reaper').start()


# The dashboard child, unlike the Tk fallback window, keeps both pipes open for the
# whole session: stdin stays open for later "navigate" messages and stdout
# carries app commands back. Never raises.
def _spawn_dashboard_child(page: str, on_app_command):
    command = [sys.executable, '-m', 'whisper_key.main', '--window', 'dashboard']
    environment = dict(os.environ, **{COMMAND_PIPE_ENV_VAR: '1'})
    try:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 env=environment, creationflags=_NO_CONSOLE_FLAGS)
        child.stdin.write(json.dumps({'page': page, 'app_state': _latest_app_state}).encode('utf-8') + b'\n')
        child.stdin.flush()
    except Exception as e:
        logger.error(f"Could not open the dashboard window: {e}")
        return
    with _children_lock:
        _open_children['dashboard'] = child
    threading.Thread(target=_read_child_commands, args=(child, on_app_command),
                     daemon=True, name='dashboard-command-reader').start()
    threading.Thread(target=child.wait, daemon=True, name='dashboard-window-reaper').start()


# Writes one JSON line to a running child; a closed pipe just means it is
# exiting, so it is logged rather than raised.
def _send_to_child(child, message: dict):
    try:
        child.stdin.write(json.dumps(message).encode('utf-8') + b'\n')
        child.stdin.flush()
    except (OSError, ValueError) as e:
        logger.warning(f"Could not reach the open dashboard window: {e}")


# Reader thread: turns each stdout line from the dashboard into a handler call
# until the pipe closes (the window was closed).
def _read_child_commands(child, on_app_command):
    for raw_line in child.stdout:
        _dispatch_child_line(raw_line, on_app_command)


# Validates one line from the child and hands a whitelisted command to the
# handler. Anything else (garbage, unknown command, handler error) is logged
# and dropped so a misbehaving page can't do more than the whitelist allows.
def _dispatch_child_line(raw_line, on_app_command):
    try:
        message = json.loads(raw_line)
    except (ValueError, TypeError):
        logger.debug(f"Ignoring non-JSON line from the dashboard: {raw_line!r}")
        return
    command = message.get('command') if isinstance(message, dict) else None
    if command not in APP_COMMANDS:
        logger.warning(f"Ignoring unknown dashboard command: {command!r}")
        return
    if on_app_command is None:
        logger.info(f"Dashboard command {command!r} has no handler")
        return
    try:
        on_app_command(command)
    except Exception as e:
        logger.error(f"Dashboard command {command!r} failed: {e}")


# ── Child side ───────────────────────────────────────────────────────────────

# Reads the first stdin line: the JSON payload the parent piped in. Only one
# line (not read-to-EOF) because the dashboard's stdin stays open for later
# messages; the fallback window's single-line payload works the same. A missing or
# malformed payload is treated as empty; each window decides whether it can run
# without one.
def read_payload(stream) -> dict:
    try:
        data = json.loads((stream.readline() if stream else '') or '{}')
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


# Entry point for `--window NAME`: runs one window on this (main) thread and
# returns the process exit code once it closes.
def run_window(name: str, stream) -> int:
    payload = read_payload(stream)
    if name == 'dashboard':
        _claim_command_pipe()
        from .dashboard import run_dashboard
        return run_dashboard(payload.get('page') or 'home', parent_stream=stream,
                             app_state=payload.get('app_state'))
    elif name == 'fallback':
        transcript = payload.get('transcript') or ''
        if not transcript:
            return 1
        from .fallback_window import FallbackWindow
        FallbackWindow()._run_window(transcript, payload.get('reason') or '',
                                     bool(payload.get('allow_clipboard', True)))
    else:
        return 2
    return 0


# ── Child side: talking to the app ───────────────────────────────────────────

# The child's end of the command pipe, or None when there is no parent app.
_command_pipe = None
_command_pipe_lock = threading.Lock()


# Takes a private copy of stdout for commands and points fd 1 at stderr, so a
# stray print() or native-library message can never corrupt the JSON stream.
# Does nothing unless the parent said stdout is its pipe (and it is not a tty).
def _claim_command_pipe():
    global _command_pipe
    if os.environ.get(COMMAND_PIPE_ENV_VAR) != '1':
        return
    try:
        if sys.stdout is None or sys.stdout.isatty():
            return
        sys.stdout.flush()
        command_fd = os.dup(1)
        _command_pipe = os.fdopen(command_fd, 'w', encoding='utf-8')
    except (OSError, ValueError, AttributeError) as e:
        logger.warning(f"Dashboard commands unavailable: {e}")
        return
    # A console-less Windows child may have no valid stderr; fall back to the
    # null device so stray output is dropped instead of breaking the pipe.
    try:
        os.dup2(2, 1)
    except OSError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), 1)


# Asks the running app to perform `command`. Returns False when there is no app
# to ask (standalone dashboard), the command is not whitelisted or the pipe is
# closed; True means the line was written, not that the app finished the job.
def send_command_to_app(command: str) -> bool:
    if command not in APP_COMMANDS or _command_pipe is None:
        return False
    try:
        with _command_pipe_lock:
            _command_pipe.write(json.dumps({'command': command}) + '\n')
            _command_pipe.flush()
        return True
    except (OSError, ValueError):
        return False


# Runs on a daemon thread in the child: applies every later line the app sends
# ("navigate" and "app_state") until stdin closes.
def listen_to_app(stream, on_navigate, on_app_state=None):
    for raw_line in stream:
        handle_app_message(raw_line, on_navigate, on_app_state)


# Applies one parent->child line: `on_navigate(page)` shows that page,
# `on_app_state(state)` updates the status bar.
def handle_app_message(raw_line, on_navigate, on_app_state=None):
    try:
        message = json.loads(raw_line)
    except (ValueError, TypeError):
        return
    if not isinstance(message, dict):
        return
    page = message.get('navigate')
    if isinstance(page, str) and page:
        try:
            on_navigate(page)
        except Exception as e:
            logger.error(f"Could not navigate the dashboard to {page!r}: {e}")
    state = message.get('app_state')
    if on_app_state is not None and isinstance(state, str) and state:
        try:
            on_app_state(state)
        except Exception as e:
            logger.error(f"Could not show app state {state!r} in the dashboard: {e}")
