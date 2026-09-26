"""Shared test setup.

Tests must never write into the developer's real debug log
(``.local/logs/meetingnotes.log``): the log exists to diagnose a real run,
and test chatter would bury the signal. Every test session gets a throwaway
log file instead.
"""

import logging

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_debug_log(tmp_path_factory):
    from meetingnotes import logs as applog

    logdir = tmp_path_factory.mktemp("logs")
    old_dir, old_path = applog.LOG_DIR, applog.LOG_PATH
    applog.LOG_DIR = logdir
    applog.LOG_PATH = logdir / "meetingnotes.log"

    root = logging.getLogger()
    old_level = root.level
    # The app normally lowers third-party noise at setup; tests import modules
    # directly so do the same here.
    for name in ("httpx", "httpcore", "urllib3", "uvicorn", "watchfiles"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        yield
    finally:
        applog.LOG_DIR, applog.LOG_PATH = old_dir, old_path
        root.setLevel(old_level)
