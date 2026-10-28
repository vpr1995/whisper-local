# dashboard/__init__.py
# The single Whisper Local dashboard window (pywebview + vanilla HTML/CSS/JS).
# run_dashboard() is what window_launcher.run_window calls inside the child
# process; it must run on that process's main thread (a pywebview requirement).


# Opens the dashboard on `page` and blocks until the user closes the window.
# `parent_stream` is the still-open stdin the running app sends "navigate" and
# "app_state" on; `app_state` is the app's state when the window was opened.
def run_dashboard(page: str = 'home', parent_stream=None, app_state=None) -> int:
    from .window import run_dashboard_window
    return run_dashboard_window(page, parent_stream, app_state)
