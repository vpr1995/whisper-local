# model_download.py
# First-run model downloads with a visible progress line. faster-whisper turns
# HuggingFace's progress bar off, so a first launch used to look frozen for
# minutes. Here the expected size comes from the Hub's file list, and progress
# is read from bytes on disk, which works whatever transfer backend the Hub
# client picks.

import fnmatch
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.5


# Downloads a HuggingFace snapshot into the shared cache, printing one
# updating line ("45% · 300/670 MB"). Returns the snapshot folder; raises on failure.
def download_snapshot(repo_id: str, label: str, allow_patterns: Optional[list] = None) -> str:
    from huggingface_hub import snapshot_download
    from huggingface_hub.constants import HF_HUB_CACHE

    total_bytes = _expected_bytes(repo_id, allow_patterns)
    repo_folder = os.path.join(HF_HUB_CACHE, "models--" + repo_id.replace("/", "--"))

    result = {}

    def run():
        try:
            result["path"] = snapshot_download(repo_id=repo_id, allow_patterns=allow_patterns)
        except Exception as e:
            result["error"] = e

    # The Hub client's own bars and log lines (token hints, retries) would
    # break the progress line, so they are off while this one runs.
    from huggingface_hub.utils import (are_progress_bars_disabled, disable_progress_bars,
                                       enable_progress_bars)
    bars_were_disabled = are_progress_bars_disabled()
    disable_progress_bars()
    hub_logger = logging.getLogger("huggingface_hub")
    previous_level = hub_logger.level
    hub_logger.setLevel(logging.ERROR)
    worker = threading.Thread(target=run, daemon=True, name="model-download")
    worker.start()
    try:
        while worker.is_alive():
            # Counts files already cached, so a resumed download starts part-way.
            done = _folder_bytes(repo_folder)
            _report(label, min(done, total_bytes) if total_bytes else done, total_bytes)
            worker.join(POLL_SECONDS)
    finally:
        hub_logger.setLevel(previous_level)
        if not bars_were_disabled:
            enable_progress_bars()

    if "error" in result:
        print()
        raise result["error"]
    _report(label, total_bytes, total_bytes)
    print()
    return result["path"]


# Sum of the files this download will fetch, or 0 when the Hub can't be asked
# (then the line shows MB downloaded without a percentage).
def _expected_bytes(repo_id: str, allow_patterns: Optional[list]) -> int:
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(repo_id, files_metadata=True)
        return sum(f.size or 0 for f in info.siblings
                   if not allow_patterns or any(fnmatch.fnmatch(f.rfilename, p) for p in allow_patterns))
    except Exception as e:
        logger.debug(f"Could not read model size for {repo_id}: {e}")
        return 0


# Bytes in the repo's cache folder, counting partial (.incomplete) files and
# skipping symlinks so each blob counts once.
def _folder_bytes(folder: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            if not os.path.islink(path):
                try:
                    total += os.path.getsize(path)
                except OSError:
                    pass
    return total


def _report(label: str, done: int, total: int):
    done_mb = done / 1_000_000
    if total:
        percent = min(100, int(done * 100 / total))
        line = f"⬇  Downloading {label}: {percent:3d}% · {done_mb:,.0f}/{total / 1_000_000:,.0f} MB"
    else:
        line = f"⬇  Downloading {label}: {done_mb:,.0f} MB"
    print(f"\r{line:<70}", end="", flush=True)
