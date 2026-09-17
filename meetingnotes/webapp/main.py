"""FastAPI web app: settings, model management, job queue, SSE live updates."""

from __future__ import annotations

import asyncio
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from meetingnotes import config
from meetingnotes import __version__
from meetingnotes import models as model_mgr
from meetingnotes.pipeline import PipelineOptions, run_pipeline
from meetingnotes.polish import available_tasks, task_default_prompts
from meetingnotes.profiles import list_profiles
from meetingnotes.settings import load_settings, update_settings

from .jobs import _remove_artifact_dir, job_queue, Job, mark_output_dir

UPLOAD_DIR = config.WORK_DIR / "_uploads"
APP_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(APP_DIR / "templates"))
STATIC_DIR = APP_DIR / "static"
_started_at = time.strftime("%Y-%m-%d %H:%M:%S")

# SSE subscribers (asyncio queues; see /api/events why this must be async)
_sse_subscribers: list = []
_sse_lock = threading.Lock()
_sse_loop: asyncio.AbstractEventLoop | None = None
_llm_models_cache: dict = {}  # 60s TTL cache for model listing


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _offer(q, item) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass


def _publish(event: str, data: dict) -> None:
    """Fan out an event to all SSE subscribers from sync or async context."""
    with _sse_lock:
        subs = list(_sse_subscribers)
    if not subs:
        return
    for q in subs:
        try:
            if _sse_loop is not None and _sse_loop.is_running():
                _sse_loop.call_soon_threadsafe(_offer, q, (event, data))
            else:
                _offer(q, (event, data))
        except RuntimeError:
            pass


def _safe_name(filename: str) -> str:
    base = re.sub(r"[^\w.\- ]", "_", Path(filename).name, flags=re.UNICODE).strip()
    return base or "upload"


def _persist_upload(file: UploadFile, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)


def _resolve_out_dir(settings) -> Path:
    custom = (settings.output_dir or "").strip()
    return Path(custom) if custom else config.OUT_DIR


def _resolve_key(value: str | None, saved: str) -> str:
    """Form value wins; masked placeholder falls back to the saved key."""
    if value and not value.startswith("********"):
        return value.strip()
    return saved or ""


def _open_in_explorer(path: Path, select_file: str = "") -> None:
    # explorer parses its own command line, so /select needs the path quoted
    # INSIDE a single raw argument. Passing a list makes subprocess re-quote
    # it (escaped quotes) and explorer silently falls back to Documents.
    target = f'/select,"{path / select_file}"' if select_file else f'"{path}"'
    subprocess.Popen(  # raw command line; a list would double-quote and break explorer
        f'explorer {target}',
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0,
    )


# --------------------------------------------------------------------------
# job worker (serial: transcription is GPU-bound)
# --------------------------------------------------------------------------

_pending: queue.Queue = queue.Queue()
_worker_started = False


def _worker_loop() -> None:
    while True:
        job, source = _pending.get()
        try:
            _run_job(job, source)
        except Exception:
            pass  # already recorded on the job
        finally:
            _pending.task_done()


def _enqueue(job: Job, source: Path) -> None:
    global _worker_started
    with _sse_lock:
        if not _worker_started:
            threading.Thread(target=_worker_loop, daemon=True).start()
            _worker_started = True
    _pending.put((job, source))


def _run_job(job: Job, source: Path) -> None:
    job_queue.set_status(job, "running")
    _publish("job", {"id": job.id, "status": "running"})

    def progress(msg: str) -> None:
        job_queue.log(job, msg)
        _publish("job", {"id": job.id, "status": job.status, "stage": msg})

    try:
        settings = load_settings()
        task, prompt = job.task, job.custom_prompt.strip()
        # A user-supplied prompt governs the LLM doc stage; preset-prefilled
        # text counts too. Empty custom prompt on "custom" = plain cleanup.
        if task not in ("raw", "clean") and prompt:
            task = "custom"
        elif task == "custom" and not prompt:
            task = "clean"

        # Fail fast on bad LLM credentials before transcription burns minutes.
        if task != "raw":
            progress("Checking LLM credentials...")
            from meetingnotes.pipeline import _resolve_llm

            _resolve_llm(PipelineOptions(task=task)).verify_credentials()

        out_root = _resolve_out_dir(settings)
        try:
            out_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise RuntimeError(
                f"Cannot create output folder: {out_root}. Check Settings → Save folder."
            )
        opts = PipelineOptions(
            profile=job.profile, task=task, custom_prompt=prompt,
            output_name=job.task if job.task != "raw" else None,
            model_size=settings.model_size, device=settings.device,
            compute_type=settings.compute_type, language=settings.language,
        )
        result = run_pipeline(
            source, config.WORK_DIR / job.id, out_root / job.filename,
            opts, progress_cb=progress,
        )
        mark_output_dir(out_root / job.filename)
        job.outputs = {name: str(p) for name, p in result.outputs.items()}
        job.out_dir = str(out_root / job.filename)
        job_queue.set_status(job, "done")
        job_queue.persist()
        _publish("job", {"id": job.id, "status": "done",
                         "outputs": job.outputs, "out_dir": job.out_dir})
    except Exception as e:
        job_queue.set_status(job, "failed", error=str(e))
        job_queue.log(job, "Failed.")
        job_queue.persist()
        _publish("job", {"id": job.id, "status": "failed", "error": str(e)})


# --------------------------------------------------------------------------
# LLM model listing (per provider, form values override saved settings)
# --------------------------------------------------------------------------

def _llm_models_response(provider: str, host: str | None, key: str | None) -> dict:
    """Unified model listing. Only ever returns models/error/key_invalid."""
    settings = load_settings()
    provider = (provider or settings.llm.provider or "none").lower()
    if provider in ("none", "no_llm", "off"):
        return {"models": []}
    if provider == "openai":
        return {"models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o1-mini"]}
    if provider == "anthropic":
        return {"models": ["claude-sonnet-4-5", "claude-3-5-sonnet", "claude-3-haiku"]}

    from meetingnotes.llm import get_provider

    try:
        if provider == "compat":
            p = get_provider("compat", model=settings.llm.model or "",
                             base_url=(host or "").strip() or settings.llm.compat_base_url,
                             api_key=_resolve_key(key, settings.llm.compat_api_key) or None)
        elif provider == "ollama":
            p = get_provider("ollama", model=settings.llm.model or "gemma4:31b",
                             host=(host or "").strip() or settings.llm.ollama_host,
                             api_key=_resolve_key(key, settings.llm.ollama_api_key) or None)
            if p.api_key:
                _verify_with_fallback(p, "gemma4:31b")
        elif provider == "openrouter":
            p = get_provider("openrouter", model=settings.llm.model or "google/gemma-4-31b-it:free",
                             api_key=_resolve_key(key, settings.llm.openrouter_api_key) or None)
            _verify_with_fallback(p, "google/gemma-4-31b-it:free")
        else:
            return {"models": []}
        return {"models": p.list_models()}
    except Exception as e:
        return _llm_error_response(e)


def _verify_with_fallback(p, fallback_model: str) -> None:
    """Verify credentials; a 400 (unknown model id) re-probes with a known one."""
    from meetingnotes.llm.base import LLMError

    try:
        p.verify_credentials(timeout=8)
    except LLMError as e:
        if getattr(e, "http_code", None) == 400:
            p.model = fallback_model
            p.verify_credentials(timeout=8)
        else:
            raise


def _llm_error_response(e: Exception) -> dict:
    code = getattr(e, "http_code", None)
    if code in (401, 403):
        return {"models": [], "key_invalid": True, "error": str(e)}
    return {"models": [], "error": str(e)}


# --------------------------------------------------------------------------
# app factory
# --------------------------------------------------------------------------

class NoCacheStaticFiles(StaticFiles):
    """Serve static files without caching (local app, files change between runs)."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


def create_app() -> FastAPI:
    app = FastAPI(title="meetingnotes", version=__version__)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if STATIC_DIR.is_dir():
        app.mount("/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return TEMPLATES.TemplateResponse(request, "index.html")

    @app.get("/api/version")
    def version():
        return {"version": __version__, "started_at": _started_at}

    # ------------------------------ settings -------------------------------

    @app.get("/api/settings")
    def get_settings():
        return load_settings().to_public_dict()

    @app.post("/api/settings")
    def post_settings(payload: dict):
        return update_settings(payload).to_public_dict()

    @app.get("/api/languages")
    def list_languages():
        from meetingnotes.transcribe import supported_languages

        return {"languages": supported_languages()}

    # ------------------------- whisper models ------------------------------

    def _known(size: str) -> None:
        if size not in model_mgr.KNOWN_MODELS:
            raise HTTPException(404, "unknown model size")

    @app.get("/api/models")
    def get_models():
        return {"models": model_mgr.list_models()}

    @app.get("/api/models/{size}/status")
    def model_status(size: str):
        _known(size)
        return model_mgr.model_status(size)

    @app.post("/api/models/{size}/download")
    def model_download(size: str):
        _known(size)
        dl = model_mgr.download_model(size, on_update=lambda _: _publish("model", model_mgr.model_status(size)))
        return {"size": size, "status": dl.status}

    @app.post("/api/models/{size}/load")
    def model_load(size: str):
        _known(size)
        if not model_mgr.is_model_downloaded(size):
            raise HTTPException(400, "model not downloaded yet")
        s = load_settings()
        started = model_mgr.start_load(size, s.device, s.compute_type,
                                       on_update=lambda: _publish("model", model_mgr.model_status(size)))
        return {"size": size, "started": started}

    @app.post("/api/models/{size}/unload")
    def model_unload(size: str):
        _known(size)
        from meetingnotes import transcribe

        return {"size": size, "unloaded": transcribe.unload_models(size)}

    @app.post("/api/models/{size}/delete")
    def model_delete(size: str):
        _known(size)
        ok = model_mgr.delete_model(size)
        _publish("model", model_mgr.model_status(size))
        return {"size": size, "deleted": ok}

    @app.post("/api/models/open-folder")
    def models_folder():
        folder = model_mgr.hub_cache_dir()
        if not folder.is_dir():
            raise HTTPException(404, "model cache folder does not exist yet")
        _open_in_explorer(folder)
        return {"ok": True, "folder": str(folder)}

    # --------------------------- LLM models --------------------------------

    @app.get("/api/llm/models")
    def llm_models(provider: str | None = None, host: str | None = None, key: str | None = None):
        """Model list for the given (or saved) provider; 60s cache, never blocks the UI."""
        settings = load_settings()
        provider = (provider or settings.llm.provider or "none").lower()
        if provider in ("none", "openai", "anthropic"):  # static or empty lists
            return _llm_models_response(provider, host, key)
        cache_key = (provider, host or "", (key or "")[-8:], settings.llm.model or "")
        cached = _llm_models_cache.get(cache_key)
        if cached and time.time() - cached[0] < 60:
            return cached[1]
        result = _llm_models_response(provider, host, key)
        _llm_models_cache[cache_key] = (time.time(), result)
        return result

    # ------------------------------ profiles -------------------------------

    @app.get("/api/profiles")
    def get_profiles():
        return {
            "profiles": [{"name": p.name, "description": p.description} for p in list_profiles()],
            "tasks": ["raw", *available_tasks(), "custom"],
            "task_prompts": task_default_prompts(),
        }

    # ------------------------------- jobs ----------------------------------

    @app.post("/api/jobs")
    async def create_jobs(files: list[UploadFile] = File(...)):
        """Stage uploads; nothing runs until /start."""
        settings = load_settings()
        presets = task_default_prompts()
        created = []
        for file in files:
            orig = _safe_name(file.filename or "upload")
            job = job_queue.create(orig, profile=settings.default_profile,
                                   task=settings.default_task,
                                   custom_prompt=presets.get(settings.default_task, ""))
            dest = UPLOAD_DIR / job.id / orig
            _persist_upload(file, dest)
            job_queue.set_status(job, "staged")
            job_queue.persist()
            _publish("job", {"id": job.id, "filename": job.filename, "status": "staged"})
            created.append({"id": job.id, "filename": job.filename})
        return {"created": created}

    @app.get("/api/jobs")
    def list_jobs():
        return {"jobs": [
            {"id": j.id, "filename": j.filename, "profile": j.profile, "task": j.task,
             "custom_prompt": j.custom_prompt, "status": j.status, "stage": j.stage,
             "error": j.error, "outputs": list(j.outputs.keys()), "out_dir": j.out_dir,
             "created_at": j.created_at}
            for j in job_queue.list()
        ]}

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str):
        j = _job_or_404(job_id)
        return {"id": j.id, "filename": j.filename, "profile": j.profile, "task": j.task,
                "status": j.status, "stage": j.stage, "error": j.error,
                "log": j.progress_log, "outputs": list(j.outputs.keys()),
                "out_dir": j.out_dir}

    @app.post("/api/jobs/{job_id}/start")
    def start_job(job_id: str):
        job = _job_or_404(job_id)
        if job.status != "staged":
            raise HTTPException(400, f"job is {job.status}, only staged jobs can start")
        src = UPLOAD_DIR / job.id / job.filename
        if not src.is_file():
            raise HTTPException(404, "uploaded file missing")
        job_queue.set_status(job, "queued")
        _publish("job", {"id": job.id, "status": "queued"})
        _enqueue(job, src)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/update")
    def update_job(job_id: str, payload: dict):
        """Edit a staged job's settings before it starts."""
        job = _job_or_404(job_id)
        if job.status != "staged":
            raise HTTPException(400, "only staged jobs can be edited")
        if "profile" in payload:
            if payload["profile"] not in [p.name for p in list_profiles()]:
                raise HTTPException(400, "unknown profile")
            job.profile = payload["profile"]
        if "task" in payload:
            task = payload["task"]
            if task not in ["raw", *available_tasks(), "custom"]:
                raise HTTPException(400, "unknown task")
            job.task = task
            job.custom_prompt = "" if task in ("raw", "custom") else task_default_prompts().get(task, "")
        if "custom_prompt" in payload:
            job.custom_prompt = str(payload["custom_prompt"])
        job_queue.persist()
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/delete")
    def delete_job(job_id: str, delete_artifacts: bool = False):
        """Remove one job; optionally delete its output folder (marker-guarded)."""
        job = _job_or_404(job_id)
        if job.status == "running":
            raise HTTPException(400, "cannot delete a running job")
        removed = _remove_artifact_dir(job.out_dir) if delete_artifacts else False
        job_queue.remove(job.id)
        _publish("job", {"id": job.id, "status": "deleted"})
        return {"ok": True, "removed_dir": removed}

    @app.get("/api/jobs/{job_id}/download/{name}")
    def download(job_id: str, name: str):
        job = _job_or_404(job_id)
        if name not in job.outputs:
            raise HTTPException(404, "download not found")
        path = Path(job.outputs[name])
        if not path.is_file():
            raise HTTPException(404, "file missing")
        stem, ext = Path(job.filename).stem, path.suffix
        media = {".txt": "text/plain; charset=utf-8", ".json": "application/json",
                 ".md": "text/markdown; charset=utf-8"}.get(ext, "application/octet-stream")
        return FileResponse(path, filename=path.name if name == "document" else f"{stem}.{name.split('_', 1)[-1]}{ext}",
                            media_type=media, content_disposition_type="attachment")

    @app.post("/api/jobs/{job_id}/open-folder")
    def open_folder(job_id: str):
        job = _job_or_404(job_id)
        if not job.out_dir:
            raise HTTPException(404, "no output folder for this job")
        folder = Path(job.out_dir)
        if not folder.is_dir():
            raise HTTPException(404, f"folder no longer exists: {folder}")
        try:
            _open_in_explorer(folder, _first_output(job))
        except OSError as e:
            raise HTTPException(500, f"Explorer failed to start: {e}")
        return {"ok": True, "folder": str(folder)}

    # ------------------------------ shutdown / events ----------------------

    @app.post("/api/shutdown")
    def shutdown():
        threading.Thread(target=lambda: (time.sleep(0.3), _kill()), daemon=True).start()
        return {"ok": True}

    @app.get("/api/events")
    async def events():
        """Live update stream (SSE). Async is essential: idle streams park on
        the event loop; a sync generator would eat one thread-pool worker per
        open tab until the pool starves and the whole app hangs."""
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        _bind_sse_loop(asyncio.get_running_loop())
        with _sse_lock:
            _sse_subscribers.append(q)

        async def stream():
            try:
                while True:
                    event, data = await q.get()
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                with _sse_lock:
                    if q in _sse_subscribers:
                        _sse_subscribers.remove(q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


# --------------------------------------------------------------------------
# small shared pieces
# --------------------------------------------------------------------------

def _bind_sse_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _sse_loop
    if _sse_loop is None:
        _sse_loop = loop


def _job_or_404(job_id: str) -> Job:
    job = job_queue.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def _first_output(job: Job) -> str:
    """Any existing file in the job's output folder, for Explorer to select."""
    folder = Path(job.out_dir)
    if not folder.is_dir():
        return ""
    for p in sorted(folder.iterdir()):
        if p.is_file():
            return p.name
    return ""


def _kill() -> None:
    import os
    import signal

    os.kill(os.getpid(), signal.SIGTERM)