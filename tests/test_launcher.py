"""Tests for the launcher's browser-fallback watchdog.

When the app falls back to the system browser there is no window handle to
watch. Closing the tab used to leave the python process in Task Manager; the
watchdog stops the server once the web UI reports no connected clients.
"""

import threading
import time
from unittest import mock

from launcher import _watch_browser_session


class FakeServer:
    def __init__(self):
        self.should_exit = False


def test_watchdog_stops_server_when_clients_gone(monkeypatch):
    server = FakeServer()
    # Liveness says: a client was seen, and it left 30s ago.
    monkeypatch.setattr(
        "meetingnotes.webapp.main.client_idle_seconds", lambda: (True, 30.0))

    _watch_browser_session("http://x", server, grace_s=20.0, poll_s=0.01)
    assert server.should_exit is True


def test_watchdog_keeps_serving_while_client_connected(monkeypatch):
    server = FakeServer()
    calls = {"n": 0}

    def liveness():
        calls["n"] += 1
        if calls["n"] > 5:
            server.should_exit = True  # end the test cleanly
            return True, 0.0
        return True, None  # tab is open

    monkeypatch.setattr("meetingnotes.webapp.main.client_idle_seconds", liveness)
    _watch_browser_session("http://x", server, grace_s=20.0, poll_s=0.001)
    assert calls["n"] > 1, "watchdog must keep polling while a tab is open"


def test_watchdog_survives_reload_gap(monkeypatch):
    """A brief disconnect during reload (< grace) must not stop the server."""
    server = FakeServer()
    calls = {"n": 0}

    def liveness():
        calls["n"] += 1
        if calls["n"] == 1:
            return True, 5.0      # tab reloading, within grace
        server.should_exit = True  # reconnected; end the test
        return True, None

    monkeypatch.setattr("meetingnotes.webapp.main.client_idle_seconds", liveness)
    _watch_browser_session("http://x", server, grace_s=20.0, poll_s=0.001)
    assert calls["n"] == 2


def test_watchdog_gives_up_when_browser_never_opened(monkeypatch):
    server = FakeServer()
    monkeypatch.setattr(
        "meetingnotes.webapp.main.client_idle_seconds", lambda: (False, None))
    start = time.time()
    _watch_browser_session("http://x", server, grace_s=20.0, poll_s=0.01,
                           opened=False, never_seen_timeout_s=0.05)
    assert server.should_exit is True
    assert time.time() - start < 5, "should not idle for the full timeout"
