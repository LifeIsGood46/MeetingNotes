"""Persistent job queue and state for the web app.

Jobs are saved to jobs.json after every meaningful change, so the queue
survives app restarts. "Clear" operations are deliberately conservative:
they only ever touch folders the app itself created (marker-file guarded).
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from meetingnotes import config

STATE_PATH = config.APP_DIR / "jobs.json"
MARKER_NAME = ".meetingnotes-output"


@dataclass
class Job:
    id: str
    filename: str
    profile: str = "generic"
    task: str = "raw"               # raw | clean | notes | actions | testplan | summary | custom
    custom_prompt: str = ""
    status: str = "queued"          # queued | running | done | failed | cancelled
    stage: str = "queued"
    fraction: float | None = None   # 0..1 overall progress (None = unknown)
    model_size: str = ""            # per-job override; blank = profile/settings
    language: str = ""              # per-job override; blank = settings
    progress_log: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    out_dir: str = ""               # where final artifacts were saved on disk
    error: str = ""
    created_at: float = field(default_factory=time.time)


class JobQueue:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._load()

    # ---------------- persistence ----------------

    def _load(self) -> None:
        if not STATE_PATH.is_file():
            return
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            for jd in data.get("jobs", []):
                job = Job(**jd)
                # Jobs interrupted by a restart are dead; staged ones survive.
                if job.status in ("queued", "running"):
                    job.status = "failed"
                    job.error = "interrupted by app restart"
                    job.stage = "failed"
                # Legacy entries stored the error text as the stage too
                # (rendered the message twice); collapse to a neutral stage.
                if job.status == "failed" and job.stage.startswith("ERROR:"):
                    job.stage = "Failed."
                self._jobs[job.id] = job
        except (json.JSONDecodeError, TypeError, KeyError):
            pass  # corrupt state file — start fresh rather than crash

    def _save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"jobs": [asdict(j) for j in self._jobs.values()]}
        STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------------- queue ops ----------------

    def create(self, filename: str, profile: str, task: str, custom_prompt: str = "") -> Job:
        job = Job(id=uuid.uuid4().hex[:12], filename=filename,
                  profile=profile, task=task, custom_prompt=custom_prompt)
        with self._lock:
            self._jobs[job.id] = job
            self._save()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def log(self, job: Job, msg: str, fraction: float | None = None) -> None:
        with self._lock:
            job.progress_log.append(msg)
            job.stage = msg
            if fraction is not None:
                job.fraction = min(1.0, max(0.0, fraction))

    def set_status(self, job: Job, status: str, error: str = "") -> None:
        with self._lock:
            job.status = status
            job.error = error

    def persist(self) -> None:
        with self._lock:
            self._save()

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
            self._cancels.pop(job_id, None)
            self._save()

    def retry(self, job_id: str):
        """Reset a failed/cancelled job to staged so it can start again.

        Profile/task/prompt are kept; error, stage and stale outputs are
        cleared. Returns the job, or None when it isn't retryable.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status not in ("failed", "cancelled"):
                return None
            job.status = "staged"
            job.stage = ""
            job.error = ""
            job.outputs = {}
            job.out_dir = ""
            job.fraction = None
            self._save()
            return job

    # ---------------- cancellation ----------------

    def request_cancel(self, job_id: str) -> bool:
        """Signal a queued/running job to stop. Returns False if not cancellable.

        Set-only (never cleared except on start/retry), so a cancel that
        lands between enqueue and the worker picking the job up still wins.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status not in ("queued", "running"):
                return False
            self._cancels.setdefault(job_id, threading.Event()).set()
            return True

    def cancel_event(self, job_id: str) -> threading.Event | None:
        with self._lock:
            return self._cancels.get(job_id)

    def clear_cancel(self, job_id: str) -> None:
        with self._lock:
            self._cancels.pop(job_id, None)

    # ---------------- clearing ----------------

    def clear(self, delete_artifacts: bool) -> int:
        """Remove all jobs from the list (and memory).

        When delete_artifacts is True, also remove each job's output folder —
        but ONLY folders containing our marker file, and each such folder is
        verified to be a direct, app-created child before removal.
        """
        with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
            self._save()
        removed = 0
        if delete_artifacts:
            for job in jobs:
                if _remove_artifact_dir(job.out_dir):
                    removed += 1
        return removed


def mark_output_dir(path: Path) -> None:
    """Stamp the marker file into a freshly created output folder."""
    marker = path / MARKER_NAME
    try:
        marker.write_text("created by meetingnotes", encoding="utf-8")
        if sys.platform.startswith("win"):
            import ctypes

            FILE_ATTRIBUTE_HIDDEN = 0x2
            ctypes.windll.kernel32.SetFileAttributesW(str(marker), FILE_ATTRIBUTE_HIDDEN)
    except OSError:
        pass


def _remove_artifact_dir(out_dir: str) -> bool:
    """Safely delete one job's artifact folder.

    Safety rules (in order):
      1. Must be non-empty string and an existing directory.
      2. Must contain our marker file.
      3. Must not be (or contain) a filesystem root, home dir, or any of the
         app's own working dirs.
      4. Only removes the folder itself; nothing outside it is touched.
    """
    if not out_dir:
        return False
    folder = Path(out_dir)
    if not folder.is_dir():
        return False
    if not (folder / MARKER_NAME).is_file():
        return False  # not ours — never touch it

    forbidden = {
        Path.home(),
        Path(folder.anchor),  # drive root e.g. C:\
        config.WORK_DIR.resolve(),
        config.WORK_DIR.parent.resolve(),
        config.OUT_DIR,
        config.OUT_DIR.parent,
    }
    resolved = folder.resolve()
    for f in forbidden:
        try:
            if resolved == Path(f).resolve():
                return False
        except OSError:
            return False

    shutil.rmtree(folder)
    return True


job_queue = JobQueue()