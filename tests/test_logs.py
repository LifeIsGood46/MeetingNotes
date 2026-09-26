"""Tests for the debug log: one file to hand over after a VM test run."""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meetingnotes import config
from meetingnotes import logs as applog
from meetingnotes.webapp import jobs as jobs_mod
from meetingnotes.webapp import main as webmain


@pytest.fixture()
def client_with_log(tmp_path, monkeypatch):
    """Test app whose debug-log path points into tmp_path."""
    logpath = tmp_path / "logs" / "meetingnotes.log"
    monkeypatch.setattr(applog, "LOG_PATH", logpath)
    monkeypatch.setattr(jobs_mod, "STATE_PATH", tmp_path / "jobs.json")
    queue = jobs_mod.JobQueue()
    monkeypatch.setattr(webmain, "job_queue", queue)
    return TestClient(webmain.create_app()), logpath


def test_setup_logging_creates_file_and_logs(tmp_path, monkeypatch):
    logdir = tmp_path / "logs"
    monkeypatch.setattr(applog, "LOG_DIR", logdir)
    monkeypatch.setattr(applog, "LOG_PATH", logdir / "meetingnotes.log")
    monkeypatch.setattr(applog, "_configured", False)

    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = applog.setup_logging()
        logging.getLogger("meetingnotes.test").info("hello from the test")
        for h in root.handlers:
            h.flush()
        text = path.read_text(encoding="utf-8")
        assert "hello from the test" in text
        assert "meetingnotes" in text  # startup banner
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        applog._configured = False


def test_setup_logging_is_idempotent(tmp_path, monkeypatch):
    logdir = tmp_path / "logs"
    monkeypatch.setattr(applog, "LOG_DIR", logdir)
    monkeypatch.setattr(applog, "LOG_PATH", logdir / "meetingnotes.log")
    monkeypatch.setattr(applog, "_configured", False)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        applog.setup_logging()
        n = len(root.handlers)
        applog.setup_logging()  # second call must not add handlers again
        assert len(root.handlers) == n
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        applog._configured = False


def test_crash_hooks_installed(tmp_path, monkeypatch):
    import sys
    import threading

    logdir = tmp_path / "logs"
    monkeypatch.setattr(applog, "LOG_DIR", logdir)
    monkeypatch.setattr(applog, "LOG_PATH", logdir / "meetingnotes.log")
    monkeypatch.setattr(applog, "_configured", False)
    root = logging.getLogger()
    before = list(root.handlers)
    old_excepthook, old_thread_hook = sys.excepthook, threading.excepthook
    try:
        applog.setup_logging()
        assert sys.excepthook is not old_excepthook
        assert threading.excepthook is not old_thread_hook
    finally:
        sys.excepthook, threading.excepthook = old_excepthook, old_thread_hook
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        applog._configured = False


def test_log_endpoint_serves_text(client_with_log):
    http, logpath = client_with_log
    logpath.parent.mkdir(parents=True, exist_ok=True)
    logpath.write_text("line one\nline two\n", encoding="utf-8")
    r = http.get("/api/log")
    assert r.status_code == 200
    d = r.json()
    assert d["exists"] is True and "line two" in d["text"]

    tailed = http.get("/api/log?tail=1000").json()
    assert tailed["text"] == "line one\nline two\n"  # smaller than file: whole file


def test_log_endpoint_tail_truncates(client_with_log):
    http, logpath = client_with_log
    logpath.parent.mkdir(parents=True, exist_ok=True)
    logpath.write_text("X" * 100 + "END\n", encoding="utf-8")
    d = http.get("/api/log?tail=10").json()
    assert d["text"] == "XXXXXXEND\n"  # last 10 bytes (6 X + "END\n")


def test_ui_error_endpoint_writes_to_log(client_with_log):
    """A UI-side error must land in the handover log file."""
    import logging

    http, logpath = client_with_log
    logpath.parent.mkdir(parents=True, exist_ok=True)
    webapp_log = logging.getLogger("meetingnotes.webapp")
    handler = logging.FileHandler(logpath, encoding="utf-8")
    handler.setLevel(logging.ERROR)
    webapp_log.addHandler(handler)
    try:
        r = http.post("/api/log/ui-error",
                      json={"where": "app.js:42", "message": "boom"})
        assert r.status_code == 200
        handler.flush()
    finally:
        webapp_log.removeHandler(handler)
        handler.close()
    text = logpath.read_text(encoding="utf-8")
    assert "boom" in text and "app.js:42" in text
