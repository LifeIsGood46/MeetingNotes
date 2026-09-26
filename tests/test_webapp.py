"""Tests for the job retry endpoint (failed -> staged)."""

import pytest
from fastapi.testclient import TestClient

from meetingnotes.webapp import jobs as jobs_mod
from meetingnotes.webapp import main as webmain


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_mod, "STATE_PATH", tmp_path / "jobs.json")
    queue = jobs_mod.JobQueue()
    monkeypatch.setattr(webmain, "job_queue", queue)
    return TestClient(webmain.create_app()), queue


def _fail(job, queue):
    queue.set_status(job, "failed", error="boom")
    job.stage = "Failed."
    queue.persist()


def test_retry_failed_job_back_to_staged(client):
    http, queue = client
    job = queue.create("a.wav", profile="generic", task="notes", custom_prompt="p")
    _fail(job, queue)

    r = http.post(f"/api/jobs/{job.id}/retry")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    again = queue.get(job.id)
    assert again.status == "staged"
    assert again.error == "" and again.outputs == {} and again.out_dir == ""
    # settings survive the restart
    assert (again.profile, again.task, again.custom_prompt) == ("generic", "notes", "p")


def test_retry_cancelled_job_back_to_staged(client):
    http, queue = client
    job = queue.create("c.wav", profile="generic", task="raw")
    queue.set_status(job, "cancelled")
    queue.persist()

    assert http.post(f"/api/jobs/{job.id}/retry").status_code == 200
    assert queue.get(job.id).status == "staged"


def test_retry_rejects_non_failed(client):
    http, queue = client
    staged = queue.create("b.wav", profile="generic", task="raw")
    assert http.post(f"/api/jobs/{staged.id}/retry").status_code == 400

    queue.set_status(staged, "done")
    queue.persist()
    assert http.post(f"/api/jobs/{staged.id}/retry").status_code == 400
    assert queue.get(staged.id).status == "done"


def test_retry_unknown_job_404(client):
    http, _ = client
    assert http.post("/api/jobs/nope/retry").status_code == 404


# ---------------- cancellation ----------------

def test_cancel_running_job_sets_event(client):
    http, queue = client
    job = queue.create("run.wav", profile="generic", task="raw")
    queue.set_status(job, "running")
    queue.persist()

    assert http.post(f"/api/jobs/{job.id}/cancel").status_code == 200
    ev = queue.cancel_event(job.id)
    assert ev is not None and ev.is_set()


def test_cancel_staged_or_done_rejected(client):
    http, queue = client
    staged = queue.create("s.wav", profile="generic", task="raw")
    queue.set_status(staged, "staged")
    assert http.post(f"/api/jobs/{staged.id}/cancel").status_code == 400
    done = queue.create("d.wav", profile="generic", task="raw")
    queue.set_status(done, "done")
    queue.persist()
    assert http.post(f"/api/jobs/{done.id}/cancel").status_code == 400


def test_start_clears_stale_cancel(client, monkeypatch, tmp_path):
    """A cancel that arrived while queued must not kill a later start."""
    http, queue = client
    monkeypatch.setattr(webmain, "UPLOAD_DIR", tmp_path / "uploads")
    job = queue.create("x.wav", profile="generic", task="raw")
    upload = webmain.UPLOAD_DIR / job.id / job.filename
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"fake")

    queue.set_status(job, "running")
    queue.request_cancel(job.id)          # user pressed Stop…
    queue.set_status(job, "staged")       # …then the job was retried

    enqueued = []
    monkeypatch.setattr(webmain, "_enqueue", lambda j, s: enqueued.append(j.id))
    assert http.post(f"/api/jobs/{job.id}/start").status_code == 200

    assert enqueued == [job.id]
    ev = queue.cancel_event(job.id)
    assert ev is None or not ev.is_set()


def test_run_job_honours_pre_queue_cancel(tmp_path, monkeypatch):
    """The worker checks the cancel flag before touching the pipeline."""
    monkeypatch.setattr(jobs_mod, "STATE_PATH", tmp_path / "jobs.json")
    queue = jobs_mod.JobQueue()
    monkeypatch.setattr(webmain, "job_queue", queue)

    job = queue.create("q.wav", profile="generic", task="raw")
    queue.set_status(job, "queued")
    queue.request_cancel(job.id)

    calls = []
    monkeypatch.setattr(webmain, "run_pipeline", lambda *a, **k: calls.append(1))
    webmain._run_job(job, tmp_path / "q.wav")

    assert calls == [], "pipeline must not run for a cancelled job"
    assert queue.get(job.id).status == "cancelled"


def test_update_job_language_accepted(client):
    http, queue = client
    job = queue.create("l.wav", profile="generic", task="raw")
    queue.set_status(job, "staged")

    r = http.post(f"/api/jobs/{job.id}/update", json={"language": "en"})
    assert r.status_code == 200, r.text
    assert queue.get(job.id).language == "en"

    # clearing back to default is allowed
    assert http.post(f"/api/jobs/{job.id}/update", json={"language": ""}).status_code == 200
    assert queue.get(job.id).language == ""


def test_update_job_language_rejects_unknown(client):
    http, queue = client
    job = queue.create("l2.wav", profile="generic", task="raw")
    queue.set_status(job, "staged")
    r = http.post(f"/api/jobs/{job.id}/update", json={"language": "xx-nope"})
    assert r.status_code == 400


def test_jobs_list_exposes_language_and_model(client):
    http, queue = client
    job = queue.create("m.wav", profile="generic", task="raw")
    job.model_size = "tiny"
    job.language = "de"
    queue.persist()
    data = http.get("/api/jobs").json()["jobs"][0]
    assert data["model_size"] == "tiny" and data["language"] == "de"


# ---------------- browser-session liveness (launcher watchdog) ----------------

def test_client_idle_none_before_any_client():
    from meetingnotes.webapp import main as m

    with m._sse_lock:
        m._sse_subscribers.clear()
    m._client_seen = False
    m._client_last_gone = None
    seen, idle = m.client_idle_seconds()
    assert seen is False and idle is None


def test_client_connected_reports_not_idle():
    from meetingnotes.webapp import main as m

    with m._sse_lock:
        m._sse_subscribers.append(object())
        m._client_last_gone = 0.0  # even with a stale disconnect stamp
    try:
        seen, idle = m.client_idle_seconds()
        assert seen is True and idle is None, "a live tab must never look idle"
    finally:
        with m._sse_lock:
            m._sse_subscribers.clear()
            m._client_last_gone = None


def test_client_idle_after_all_tabs_left():
    import time
    from meetingnotes.webapp import main as m

    m._client_seen = True
    with m._sse_lock:
        m._sse_subscribers.clear()
        m._client_last_gone = time.time() - 30
    try:
        seen, idle = m.client_idle_seconds()
        assert seen is True and idle is not None and idle >= 29
    finally:
        m._client_last_gone = None


def test_client_bye_marks_gone_only_without_subscribers(client):
    from meetingnotes.webapp import main as m

    http, _ = client
    # No connected tabs: beacon records a disconnect.
    with m._sse_lock:
        m._sse_subscribers.clear()
    m._client_last_gone = None
    assert http.post("/api/client-bye").status_code == 200
    assert m._client_last_gone is not None

    # With a tab connected, a stray beacon must not mark the session gone.
    m._client_last_gone = None
    with m._sse_lock:
        m._sse_subscribers.append(object())
    try:
        assert http.post("/api/client-bye").status_code == 200
        assert m._client_last_gone is None
    finally:
        with m._sse_lock:
            m._sse_subscribers.clear()


def test_unique_job_dir_uses_stem(tmp_path):
    from meetingnotes.webapp.main import _unique_job_dir

    assert _unique_job_dir(tmp_path, "Meeting.mp4") == tmp_path / "Meeting"
    assert _unique_job_dir(tmp_path, "noext") == tmp_path / "noext"


def test_unique_job_dir_suffixes_on_collision(tmp_path):
    from meetingnotes.webapp.main import _unique_job_dir

    (tmp_path / "Meeting").mkdir()  # previous job's folder (or user's own)
    assert _unique_job_dir(tmp_path, "Meeting.mp4") == tmp_path / "Meeting (2)"
    (tmp_path / "Meeting (2)").mkdir()
    assert _unique_job_dir(tmp_path, "Meeting.mp4") == tmp_path / "Meeting (3)"
    # a plain file with the same name counts as a collision too
    (tmp_path / "Clip").write_text("mine")
    assert _unique_job_dir(tmp_path, "Clip.mp4") == tmp_path / "Clip (2)"


def test_unique_job_dir_empty_stem_falls_back(tmp_path):
    from meetingnotes.webapp.main import _unique_job_dir

    assert _unique_job_dir(tmp_path, "  .mp4") == tmp_path / "job"
