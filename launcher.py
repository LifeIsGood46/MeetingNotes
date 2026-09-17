"""Single-entry launcher: start the server and open the UI in a browser.

This is what gets packaged into an exe (PyInstaller) — the user double-clicks
and the app opens. No console, no manual URL.

Key behaviors:
  * Single instance — a lockfile prevents a second copy starting up (which
    would fight over the port and the GPU).
  * If an instance is already running, just open the browser at it.
  * Ctrl+C or closing the console shuts everything down cleanly.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from meetingnotes.webapp.main import create_app

HOST = "127.0.0.1"
PORT = 8123
LOCK_PATH = Path.home() / ".meetingnotes" / "meetingnotes.lock"


def _instance_running(url: str, timeout: float = 2.0) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _acquire_lock() -> bool:
    """Create a lockfile holding our PID. False if a live instance owns it."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.is_file():
        try:
            pid = int(LOCK_PATH.read_text().strip())
            # Is that PID still alive? (signal 0 raises if not)
            os.kill(pid, 0)
            return False  # a real, live process owns it
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            # Stale lock from a crashed run — take over.
            pass
    LOCK_PATH.write_text(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        if LOCK_PATH.is_file() and LOCK_PATH.read_text().strip() == str(os.getpid()):
            LOCK_PATH.unlink()
    except Exception:
        pass


def _open_browser_when_ready(url: str, timeout: float = 15.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                webbrowser.open(url)
                return
        except Exception:
            time.sleep(0.25)
    webbrowser.open(url)


def run(host: str = HOST, port: int = PORT) -> int:
    url = f"http://{host}:{port}"
    no_browser = os.environ.get("MEETING_NOTES_NO_BROWSER", "").strip().lower() in ("1", "true", "yes")

    # If an instance is already up, just open the browser and exit (no zombies).
    if _instance_running(url):
        if not no_browser:
            webbrowser.open(url)
        return 0

    if not _acquire_lock():
        if not no_browser:
            webbrowser.open(url)
        return 0

    try:
        app = create_app()
        if not no_browser:
            threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
        # log_config=None: under pythonw.exe there is no stdout/stderr, and the
        # default logging config crashes trying to bind a stream formatter.
        config = uvicorn.Config(app, host=host, port=port, log_level="warning", log_config=None)
        server = uvicorn.Server(config)

        try:
            server.run()
        except KeyboardInterrupt:
            pass
        finally:
            # Uvicorn handles its own shutdown; daemon threads (browser opener,
            # job worker) die with the process. Nothing else to clean.
            pass
    finally:
        _release_lock()
    return 0


if __name__ == "__main__":
    LOG = Path.home() / ".meetingnotes" / "launcher.log"
    try:
        sys.exit(run())
    except Exception:
        import traceback

        LOCK_ERR = traceback.format_exc()
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as f:
                f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n{LOCK_ERR}\n")
        except Exception:
            pass
        raise
