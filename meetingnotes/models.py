"""Model management: presence check + download with live progress.

A model counts as "installed" when faster-whisper's snapshot (model.bin) is in
the HF hub cache. Downloads push progress to subscribers via a tqdm subclass
injected into huggingface_hub.snapshot_download's tqdm_class hook.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

try:  # imported at module scope so tests can monkeypatch it
    from huggingface_hub import snapshot_download
except ImportError:  # pragma: no cover
    snapshot_download = None

KNOWN_MODELS = ["tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3"]

_REPO = "Systran/faster-whisper-{size}"


def model_repo(size: str) -> str:
    return _REPO.format(size=size)


def _hub_cache() -> Path:
    import os

    return Path(os.environ.get("HF_HUB_CACHE", Path.home() / ".cache" / "huggingface" / "hub"))


def hub_cache_dir() -> Path:
    """Root of the HuggingFace hub cache (where model snapshots live)."""
    return _hub_cache()


def _tag_dir(size: str) -> Path:
    return _hub_cache() / f"models--Systran--faster-whisper-{size}"


def is_model_downloaded(size: str) -> bool:
    """True if the model snapshot (with model.bin) is in the HF hub cache."""
    snapshots = _tag_dir(size) / "snapshots"
    if not snapshots.exists():
        return False
    return any(snapshots.rglob("model.bin"))


_size_cache: dict[str, int] = {}


def model_size_bytes(size: str) -> int:
    """Disk usage of the cached snapshot (0 if not downloaded). Cached."""
    if size in _size_cache:
        return _size_cache[size]
    tag = _tag_dir(size)
    total = 0
    if tag.is_dir():
        for p in tag.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                pass
    _size_cache[size] = total
    return total


def _invalidate_size_cache(size: str) -> None:
    _size_cache.pop(size, None)


def delete_model(size: str) -> bool:
    """Remove a downloaded model snapshot from the HF cache.

    Only the exact cache dir for a known model size is ever removed.
    Also drops any in-memory copy and resets download state. Idempotent.
    """
    if size not in KNOWN_MODELS:
        return False
    try:
        from . import transcribe as _transcribe

        _transcribe.unload_models(size)
    except Exception:
        pass
    with _lock:
        _downloads.pop(size, None)
    _invalidate_size_cache(size)
    tag = _tag_dir(size)
    if not tag.exists():
        return True
    import shutil

    shutil.rmtree(tag, ignore_errors=True)
    _invalidate_size_cache(size)
    return not tag.exists()


# ------------------------- background model load -------------------------

_load_state: dict[str, str] = {}  # size -> "" | "loading" | "error: ..."


def load_status(size: str) -> str:
    """Current background-load state: "" (idle), "loading", or "error: ..."."""
    with _lock:
        return _load_state.get(size, "")


def start_load(
    size: str,
    device: str,
    compute_type: str,
    *,
    on_update: Callable[[], None] | None = None,
) -> bool:
    """Load a model into memory in the background. False if already loading."""
    with _lock:
        if _load_state.get(size) == "loading":
            return False
        _load_state[size] = "loading"
    if on_update:
        on_update()

    def _run():
        try:
            from . import transcribe as _transcribe

            _transcribe._get_model(size, device, compute_type)
            with _lock:
                _load_state[size] = ""
        except Exception as e:
            with _lock:
                _load_state[size] = f"error: {e}"
        if on_update:
            on_update()

    threading.Thread(target=_run, daemon=True).start()
    return True


class ModelDownload:
    """Handle for a background model download with progress callbacks."""

    def __init__(self, size: str):
        self.size = size
        self.status = "downloading"   # downloading | done | failed
        self.progress = 0.0           # 0..1
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.error = ""
        self._subscribers: list[Callable[["ModelDownload"], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, cb: Callable[["ModelDownload"], None]) -> None:
        with self._lock:
            self._subscribers.append(cb)

    def update(self, downloaded: int, total: int) -> None:
        with self._lock:
            self.downloaded_bytes = downloaded
            if total:
                self.total_bytes = total
            self.progress = (downloaded / self.total_bytes) if self.total_bytes else 0.0
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(self)
            except Exception:
                pass

    def finish(self, ok: bool, error: str = "") -> None:
        self.status = "done" if ok else "failed"
        self.error = error
        if ok:
            self.progress = 1.0
        with self._lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(self)
            except Exception:
                pass


_downloads: dict[str, ModelDownload] = {}
_lock = threading.Lock()


def get_download(size: str) -> ModelDownload | None:
    with _lock:
        return _downloads.get(size)


def _make_progress_tqdm(dl: ModelDownload):
    """Build a tqdm subclass that forwards byte progress to the handle."""
    from huggingface_hub.utils import tqdm as hf_tqdm
    import io
    import os

    class ProgressTqdm(hf_tqdm):
        def __init__(self, *args, **kwargs):
            kwargs["disable"] = True
            # In windowed exe, sys.stderr may be None; avoid write errors
            kwargs["file"] = io.StringIO()
            super().__init__(*args, **kwargs)

        def display(self, *args, **kwargs):
            # Suppress actual rendering; only forward progress
            pass

        def update(self, n=1):
            # Don't call super().update which would try to render
            self.n += n
            total = self.total or 0
            dl.update(int(self.n), int(total))

    return ProgressTqdm


def download_model(
    size: str, *, on_update: Callable[[ModelDownload], None] | None = None
) -> ModelDownload:
    """Start (or reuse) a background download of a model; returns the handle."""
    with _lock:
        existing = _downloads.get(size)
        if existing and existing.status == "downloading":
            if on_update:
                existing.subscribe(on_update)
            return existing
        dl = ModelDownload(size)
        _downloads[size] = dl
    if on_update:
        dl.subscribe(on_update)

    def _run():
        if snapshot_download is None:
            dl.finish(ok=False, error="huggingface_hub not installed")
            return
        try:
            snapshot_download(model_repo(size), tqdm_class=_make_progress_tqdm(dl))
            _invalidate_size_cache(size)
            dl.finish(ok=True)
        except Exception as e:
            dl.finish(ok=False, error=str(e))

    dl._thread = threading.Thread(target=_run, daemon=True)  # noqa: SLF001
    dl._thread.start()
    return dl


def ensure_downloaded(
    size: str,
    progress_cb: Callable[..., None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    """Block until the model snapshot exists locally, with progress messages.

    Used by the pipeline so a first-ever run downloads visibly (with % updates
    and an ETA line) instead of faster-whisper doing it silently inside the
    "Transcribing" stage. Raises RuntimeError with a clear message on failure;
    raises CancelledError when should_cancel() turns true mid-download.
    """
    from .errors import CancelledError

    if is_model_downloaded(size):
        return
    import time

    log = progress_cb or (lambda _msg, _frac=None: None)
    approx_mb = {"tiny": 75, "base": 145, "small": 465, "medium": 1500,
                 "large-v1": 2900, "large-v2": 2900, "large-v3": 2900}.get(size, 0)
    log(f"Model {size} not cached — downloading (~{approx_mb} MB, first run only)...", 0.0)
    dl = download_model(size)
    last_line, last_emit, t0 = "", 0.0, time.time()
    while dl.status == "downloading":
        if should_cancel and should_cancel():
            raise CancelledError("cancelled by user")
        dl._thread.join(timeout=2.0)  # noqa: SLF001
        # Skip metadata-sized files (each file has its own tqdm total, so a
        # 2 KB config would otherwise report "86% (0.0/0 MB, ETA ~0s)").
        if time.time() - last_emit > 15 and dl.total_bytes > 10_000_000:
            frac = dl.downloaded_bytes / dl.total_bytes
            pct = 100 * frac
            elapsed = time.time() - t0
            eta = f", ETA ~{_fmt_seconds(elapsed / max(dl.downloaded_bytes, 1) * (dl.total_bytes - dl.downloaded_bytes))}" if dl.downloaded_bytes > 0 else ""
            last_line = f"      downloading {size}: {pct:.0f}% ({dl.downloaded_bytes / 10**6:.1f}/{dl.total_bytes // 10**6} MB{eta})"
            log(last_line, frac)
            last_emit = time.time()
    if dl.status != "done" or not is_model_downloaded(size):
        raise RuntimeError(f"Model download failed: {dl.error or 'unknown error'}. Check connection and retry.")
    log(f"Model {size} ready.", 1.0)


def _fmt_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _loaded_flag(size: str) -> bool:
    try:
        from . import transcribe as _transcribe

        return _transcribe.is_model_loaded(size)
    except Exception:
        return False


def model_status(size: str) -> dict:
    """Status dict for the UI."""
    base = {
        "size": size,
        "installed": False,
        "downloading": False,
        "loading": load_status(size) == "loading",
        "loaded": _loaded_flag(size),
        "size_mb": round(model_size_bytes(size) / 1e6, 1),
        "progress": 0.0,
        "error": load_status(size) if load_status(size).startswith("error:") else "",
    }
    if is_model_downloaded(size):
        base.update(installed=True, progress=1.0)
        return base
    dl = get_download(size)
    if dl:
        base.update(
            downloading=dl.status == "downloading",
            progress=round(dl.progress, 3),
            error=dl.error,
        )
    return base


def list_models() -> list[dict]:
    return [model_status(s) for s in KNOWN_MODELS]
