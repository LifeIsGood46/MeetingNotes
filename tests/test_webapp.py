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
