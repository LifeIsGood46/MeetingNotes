"""App entry point: server + native window (no browser required).

This is what the GUI exe runs. The UI opens in a native OS window via
pywebview (WebView2); if that is unavailable it falls back to a chromeless
Edge app window, then to the system browser as a last resort.

Portable: the lockfile and logs live beside the exe (see meetingnotes.config).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from meetingnotes import config
from meetingnotes.webapp.main import create_app

HOST = "127.0.0.1"
PORT = 8123
LOCK_PATH = config.APP_DIR / "meetingnotes.lock"
LOG_PATH = config.APP_DIR / "launcher.log"


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
            os.kill(pid, 0)
            return False  # a real, live process owns it
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            pass  # stale lock from a crashed run — take over
    LOCK_PATH.write_text(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        if LOCK_PATH.is_file() and LOCK_PATH.read_text().strip() == str(os.getpid()):
            LOCK_PATH.unlink()
    except Exception:
        pass


def _log(msg: str) -> None:
    """One line in the portable log: which window path the app took."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _wait_ready(url: str, timeout: float = 15.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except Exception:
            time.sleep(0.25)


# ---------------------------------------------------------------------------
# window strategies: native webview -> edge app-mode -> system browser
# ---------------------------------------------------------------------------


def _open_webview_window(url: str, server_stopper) -> bool:
    """Open a native window via pywebview. Returns False if unavailable."""
    try:
        import webview  # pywebview
        # Import the Windows backend explicitly so PyInstaller bundles it
        # (webview picks the backend at runtime; static analysis misses it).
        import webview.platforms.edgechromium  # noqa: F401

        _wait_ready(url)
        window = webview.create_window(
            "MeetingNotes",
            url,
            width=1180,
            height=800,
            min_size=(900, 600),
        )
        # pywebview event API: fires when the user closes the window.
        window.events.closed += lambda: setattr(server_stopper, "should_exit", True)
        # Log BEFORE start(): it blocks the thread until the window closes.
        _log("window: native webview")
        webview.start()
        return True
    except Exception as e:
        # Log the real reason the native window failed (frozen exe has no console).
        try:
            import traceback

            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(f"\n=== webview failed {time.strftime('%H:%M:%S')} ===\n"
                        f"{traceback.format_exc()}\n")
        except Exception:
            pass
        return False


def _edge_path() -> str | None:
    """Locate Edge (or Chromium WebView2 host) for an app-mode window."""
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in candidates:
        if Path(p).is_file():
            return p
    return None


def _open_edge_app_window(url: str) -> bool:
    """Chromeless Edge window (no tabs/URL bar). Returns False if Edge missing."""
    edge = _edge_path()
    if not edge:
        return False
    # --user-data-dir: isolated profile so the window is standalone, not
    # the user's browsing session; --app gives the chromeless frame.
    profile = config.APP_DIR / "edge-profile"
    subprocess.Popen(
        [edge, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1180,800"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return True


def run(host: str = HOST, port: int = PORT) -> int:
    url = f"http://{host}:{port}"
    no_window = os.environ.get("MEETING_NOTES_NO_BROWSER", "").strip().lower() in ("1", "true", "yes")

    # If an instance is already up, focus it instead of starting a second copy.
    if _instance_running(url):
        if not no_window:
            _wait_ready(url)
            if not _open_edge_app_window(url):
                webbrowser.open(url)
        return 0

    if not _acquire_lock():
        if not no_window:
            _wait_ready(url)
            if not _open_edge_app_window(url):
                webbrowser.open(url)
        return 0

    try:
        app = create_app()
        # log_config=None: the windowed exe has no stdout; the default
        # logging config would crash binding a stream formatter.
        uconfig = uvicorn.Config(app, host=host, port=port, log_level="warning", log_config=None)
        server = uvicorn.Server(uconfig)

        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()

        if no_window:
            server_thread.join()
        else:
            _wait_ready(url)
            # 1st choice: native window. 2nd: chromeless Edge. 3rd: browser.
            if not _open_webview_window(url, server):
                if not _open_edge_app_window(url):
                    _log("window: system-browser fallback (webview + edge-app unavailable)")
                    webbrowser.open(url)
                else:
                    _log("window: edge app-mode fallback (native webview unavailable — see earlier lines)")
                # Keep serving until the user quits from the UI.
                server_thread.join()
            else:
                _log("window: native webview")
                # Native window closed by the user -> stop the server, then
                # exit hard. Graceful interpreter shutdown hangs ~30s on CUDA
                # teardown / stray non-daemon threads; state (jobs.json,
                # settings) is already persisted incrementally, and the lock
                # is released explicitly first.
                server.should_exit = True
                server_thread.join(timeout=10)
                _release_lock()
                os._exit(0)
    finally:
        _release_lock()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception:
        import traceback

        err = traceback.format_exc()
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n{err}\n")
        except Exception:
            pass
        raise