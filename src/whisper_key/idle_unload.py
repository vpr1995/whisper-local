# idle_unload.py
# Frees the Whisper model's memory after a stretch of no dictation
# (`whisper.unload_after_minutes`). The engine reloads it on the next
# recording start, in the background while the user speaks.

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)


class IdleModelUnloader:
    # `engine` needs is_loading() and unload_if_idle(seconds); `is_app_idle`
    # reports whether the app is between recordings (not recording, not
    # transcribing, not switching models).
    def __init__(self,
                 engine,
                 timeout_minutes: float,
                 is_app_idle: Callable[[], bool],
                 poll_seconds: float = 30.0):
        self.engine = engine
        self.timeout_seconds = max(0.0, float(timeout_minutes or 0)) * 60.0
        self.is_app_idle = is_app_idle
        # Never poll slower than the timeout, so a short timeout is honoured.
        self.poll_seconds = min(poll_seconds, self.timeout_seconds) or poll_seconds
        self._stop_event = threading.Event()
        self._thread = None

    @property
    def enabled(self) -> bool:
        return self.timeout_seconds > 0

    def start(self):
        if not self.enabled or self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name='idle-unload')
        self._thread.start()
        logger.info(f"Idle model unload enabled: after {self.timeout_seconds / 60:g} min")

    def stop(self):
        self._stop_event.set()

    # One policy check. The engine makes the final "idle long enough" decision
    # under its model lock, so a transcription that starts between this check
    # and the unload can never lose its model mid-decode.
    def check_now(self) -> bool:
        if not self.enabled:
            return False
        if not self.is_app_idle() or self.engine.is_loading():
            return False
        return self.engine.unload_if_idle(self.timeout_seconds)

    def _run(self):
        while not self._stop_event.wait(self.poll_seconds):
            try:
                self.check_now()
            except Exception as e:
                logger.warning(f"Idle model unload check failed: {e}")
