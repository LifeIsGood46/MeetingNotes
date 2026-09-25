"""Tests for model management: install check + download progress wiring."""

from unittest import mock

import pytest

from meetingnotes import models


def test_known_models_nonempty():
    assert "large-v3" in models.KNOWN_MODELS


def test_is_model_downloaded_false_when_no_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert models.is_model_downloaded("large-v3") is False


def test_is_model_downloaded_true_when_model_bin_present(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    snap = tmp_path / "models--Systran--faster-whisper-large-v3" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"xx")
    assert models.is_model_downloaded("large-v3") is True


def test_download_reports_progress_and_done(monkeypatch, tmp_path):
    updates = []

    def fake_snapshot(repo_id, tqdm_class=None, **kwargs):
        # simulate two progress ticks then completion
        t = tqdm_class(total=100, unit="B")
        t.update(50)
        t.update(50)
        # mark model present so model_status reflects installed after finish
        snap = tmp_path / f"models--Systran--faster-whisper-medium" / "snapshots" / "x"
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "model.bin").write_bytes(b"xx")
        return str(snap)

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr(models, "snapshot_download", fake_snapshot)

    dl = models.download_model("medium", on_update=updates.append)
    dl._thread.join(timeout=30)

    assert dl.status == "done"
    assert updates, "expected progress updates"
    assert updates[-1].progress == 1.0
    assert models.is_model_downloaded("medium") is True


def test_download_failure_records_error(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr(models, "snapshot_download", boom)
    dl = models.download_model("medium")
    dl._thread.join(timeout=30)
    assert dl.status == "failed"
    assert "network down" in dl.error


def _make_snapshot(tmp_path, size="tiny"):
    snap = tmp_path / f"models--Systran--faster-whisper-{size}" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x" * 1024)
    return snap


def test_delete_model_removes_only_its_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _make_snapshot(tmp_path, "tiny")
    other = tmp_path / "models--Systran--faster-whisper-base" / "snapshots" / "abc"
    other.mkdir(parents=True)
    (other / "model.bin").write_bytes(b"y")

    assert models.delete_model("tiny") is True
    assert not (tmp_path / "models--Systran--faster-whisper-tiny").exists()
    # sibling model untouched
    assert (other / "model.bin").exists()


def test_delete_model_unknown_size_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert models.delete_model("../../etc") is False
    assert models.delete_model("huge-v9") is False


def test_delete_model_missing_is_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert models.delete_model("tiny") is True


def test_model_size_bytes_counts_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert models.model_size_bytes("tiny") == 0
    _make_snapshot(tmp_path, "tiny")
    models._invalidate_size_cache("tiny")
    assert models.model_size_bytes("tiny") == 1024


def test_unload_models_clears_cache():
    from meetingnotes import transcribe as t

    t._MODEL_CACHE[("tiny", "cpu", "int8")] = object()
    t._MODEL_CACHE[("base", "cpu", "int8")] = object()
    try:
        assert t.unload_models("tiny") == 1
        assert ("tiny", "cpu", "int8") not in t._MODEL_CACHE
        assert ("base", "cpu", "int8") in t._MODEL_CACHE
        assert t.unload_models() == 1
        assert t._MODEL_CACHE == {}
    finally:
        t._MODEL_CACHE.clear()


def test_is_model_loaded_flag():
    from meetingnotes import transcribe as t

    try:
        assert t.is_model_loaded("tiny") is False
        t._MODEL_CACHE[("tiny", "cpu", "int8")] = object()
        assert t.is_model_loaded("tiny") is True
    finally:
        t._MODEL_CACHE.clear()


"""Tests for job persistence and safe artifact deletion."""

import json
from pathlib import Path

import pytest

from meetingnotes.webapp.jobs import Job, JobQueue, mark_output_dir, _remove_artifact_dir


@pytest.fixture
def queue(tmp_path, monkeypatch):
    import meetingnotes.webapp.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "STATE_PATH", tmp_path / "jobs.json")
    return jobs_mod.JobQueue()


def test_new_queue_has_no_jobs(queue):
    assert queue.list() == []


def test_create_persists_to_disk(queue, tmp_path):
    import meetingnotes.webapp.jobs as jobs_mod

    job = queue.create("meeting.mp4", "scada", "raw")
    data = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert data["jobs"][0]["id"] == job.id


def test_load_restores_jobs_after_restart(tmp_path, monkeypatch):
    import meetingnotes.webapp.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "STATE_PATH", tmp_path / "jobs.json")
    q1 = jobs_mod.JobQueue()
    q1.create("a.mp4", "generic", "raw")
    q1.create("b.mp4", "generic", "raw")

    q2 = jobs_mod.JobQueue()  # simulates app restart
    assert len(q2.list()) == 2
    assert {j.filename for j in q2.list()} == {"a.mp4", "b.mp4"}


def test_interrupted_jobs_marked_failed_on_restart(tmp_path, monkeypatch):
    import meetingnotes.webapp.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "STATE_PATH", tmp_path / "jobs.json")
    q1 = jobs_mod.JobQueue()
    job = q1.create("running.mp4", "generic", "raw")
    job.status = "running"
    q1.persist()

    q2 = jobs_mod.JobQueue()
    restored = q2.get(job.id)
    assert restored.status == "failed"
    assert "interrupted" in restored.error


def test_staged_job_remove_keeps_artifacts(tmp_path):
    """Removing a staged job (no artifacts yet) must not delete anything."""
    out = tmp_path / "artifacts" / "meeting.mp4"
    out.mkdir(parents=True)
    (out / "meeting.txt").write_text("transcript", encoding="utf-8")
    mark_output_dir(out)

    assert _remove_artifact_dir("") is False  # staged jobs have no out_dir yet
    assert out.is_dir()  # folder untouched


def test_remove_artifact_dir_deletes_marked_folder(tmp_path):
    out = tmp_path / "artifacts" / "meeting.mp4"
    out.mkdir(parents=True)
    (out / "meeting.txt").write_text("transcript", encoding="utf-8")
    mark_output_dir(out)

    assert _remove_artifact_dir(str(out)) is True
    assert not out.exists()


def test_remove_artifact_dir_refuses_unmarked_folder(tmp_path):
    """A folder the app did NOT create must never be deleted."""
    out = tmp_path / "Downloads" / "important_stuff"
    out.mkdir(parents=True)
    (out / "resume.docx").write_text("important", encoding="utf-8")
    # no marker file

    assert _remove_artifact_dir(str(out)) is False
    assert (out / "resume.docx").exists()


def test_remove_artifact_dir_refuses_home_and_root(tmp_path):
    assert _remove_artifact_dir(str(Path.home())) is False
    assert _remove_artifact_dir("C:\\") is False
    assert _remove_artifact_dir("C:\\Users") is False


def test_remove_artifact_dir_refuses_nonexistent(tmp_path):
    assert _remove_artifact_dir(str(tmp_path / "does_not_exist")) is False


def test_remove_method_persists_state(tmp_path, monkeypatch):
    import meetingnotes.webapp.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "STATE_PATH", tmp_path / "jobs.json")
    q = jobs_mod.JobQueue()
    job = q.create("x.mp4", "generic", "raw")
    q.remove(job.id)
    assert q.get(job.id) is None
    # state file reflects the removal (restart would not resurrect it)
    data = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert data["jobs"] == []


def test_staged_jobs_survive_restart(tmp_path, monkeypatch):
    import meetingnotes.webapp.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "STATE_PATH", tmp_path / "jobs.json")
    q1 = jobs_mod.JobQueue()
    job = q1.create("staged.mp4", "scada", "testplan")
    job.status = "staged"
    q1.persist()

    q2 = jobs_mod.JobQueue()  # app restart
    restored = q2.get(job.id)
    assert restored is not None
    assert restored.status == "staged"  # staged survives, NOT marked failed


def test_cancel_event_lifecycle(queue):
    job = queue.create("run.mp4", "generic", "raw")
    queue.set_status(job, "running")

    assert queue.request_cancel(job.id) is True
    assert queue.cancel_event(job.id).is_set()

    queue.clear_cancel(job.id)  # a new start must not inherit the cancel
    ev = queue.cancel_event(job.id)
    assert ev is None or not ev.is_set()


def test_cancel_refused_for_staged(queue):
    job = queue.create("s.mp4", "generic", "raw")
    queue.set_status(job, "staged")
    assert queue.request_cancel(job.id) is False


def test_remove_clears_cancel_event(queue):
    job = queue.create("r.mp4", "generic", "raw")
    queue.set_status(job, "running")
    queue.request_cancel(job.id)
    queue.remove(job.id)
    assert queue.cancel_event(job.id) is None


def test_cancelled_jobs_do_not_become_failed_on_restart(tmp_path, monkeypatch):
    import meetingnotes.webapp.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "STATE_PATH", tmp_path / "jobs.json")
    q1 = jobs_mod.JobQueue()
    job = q1.create("c.mp4", "generic", "raw")
    job.status = "cancelled"
    q1.persist()

    assert jobs_mod.JobQueue().get(job.id).status == "cancelled"


def test_clear_with_delete_removes_only_marked_dirs(tmp_path, monkeypatch):
    import meetingnotes.webapp.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "STATE_PATH", tmp_path / "jobs.json")
    q = jobs_mod.JobQueue()

    # one job with a marked artifact folder
    out = tmp_path / "downloads" / "t" / "a.mp4"
    out.mkdir(parents=True)
    (out / "a.txt").write_text("x", encoding="utf-8")
    mark_output_dir(out)

    # one job pointing at an unmarked folder (someone else's)
    foreign = tmp_path / "downloads" / "t" / "foreign"
    foreign.mkdir(parents=True)
    (foreign / "not-ours.txt").write_text("x", encoding="utf-8")

    j1 = q.create("a.mp4", "generic", "raw")
    j1.out_dir = str(out)
    j1.status = "done"
    j2 = q.create("foreign", "generic", "raw")
    j2.out_dir = str(foreign)
    j2.status = "done"
    q.persist()

    # per-job delete via _remove_artifact_dir + queue.remove
    assert _remove_artifact_dir(j1.out_dir) is True
    q.remove(j1.id)
    q.remove(j2.id)  # list removal only for the foreign one
    assert not out.exists()       # ours: gone
    assert foreign.exists()       # theirs: untouched
    assert q.list() == []         # list emptied