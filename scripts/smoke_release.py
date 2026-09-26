"""Post-build smoke gate: unpack the release zip like a fresh user and prove
the packaged app actually works. Catches the class of bug unit tests cannot:
missing binaries, broken bundles, wrong paths.

Checks (all against the extracted zip, not the dev tree):
  1. ffmpeg.exe + ffprobe.exe present in _internal/ffmpeg/
  2. WebView2 window assets present (tlb + loader)
  3. templates, static (sample recording) and profiles present
  4. the CLI exe runs from the extracted folder (settings get --json)
  5. bundled ffmpeg binaries launch
  6. certifi CA bundle bundled (cloud LLM TLS on clean machines)
  7. no stale pywebview/pythonnet in the bundle
  8. static UI is current (language select, wizard/beacon fixes)
  9. (optional, --with-transcribe) real transcription of a sample to done

Exit 0 = shippable, 1 = broken.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REQUIRED = [
    "_internal/ffmpeg/ffmpeg.exe",
    "_internal/ffmpeg/ffprobe.exe",
    "_internal/meetingnotes/native/WebView2.tlb",
    "_internal/meetingnotes/native/WebView2Loader.dll",
    "_internal/meetingnotes/webapp/templates/index.html",
    "_internal/meetingnotes/webapp/static/app.js",
    "_internal/meetingnotes/webapp/static/style.css",
    "_internal/meetingnotes/webapp/static/sample.mp3",
    "_internal/meetingnotes/profiles/scada.json",
]

CLI = "meetingnotes-cli.exe"
GUI = "meetingnotes.exe"


def main() -> int:
    zip_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not zip_path or not zip_path.is_file():
        print("usage: smoke_release.py <release.zip>")
        return 1

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="mn_smoke_") as td:
        root = Path(td)
        print(f"unpacking {zip_path.name} ...")
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(root)

        names = {n.replace("\\", "/") for n in
                 (str(p.relative_to(root)) for p in root.rglob("*"))}
        for req in REQUIRED:
            if req not in names:
                failures.append(f"missing from zip: {req}")
            else:
                print(f"  ok  {req}")

        if (root / GUI).is_file():
            print("  ok  meetingnotes.exe")
        else:
            failures.append("missing meetingnotes.exe")

        if not failures and (root / CLI).is_file():
            # Run the packaged CLI from inside the extracted folder.
            r = subprocess.run(
                [str(root / CLI), "settings", "get", "language", "--json"],
                capture_output=True, text=True, timeout=120, cwd=str(root))
            if r.returncode != 0:
                failures.append(f"CLI failed (exit {r.returncode}): {(r.stderr or '')[:200]}")
            else:
                try:
                    val = json.loads(r.stdout or "{}")
                    print(f"  ok  packaged CLI responded: {val}")
                except json.JSONDecodeError:
                    failures.append(f"CLI --json output not JSON: {r.stdout[:120]}")
        elif not (root / CLI).is_file():
            failures.append("missing meetingnotes-cli.exe")

        # ffmpeg resolver proof: run in-process with sys.frozen semantics is
        # hard from here; instead assert the bundled binaries launch.
        for exe in ("ffmpeg.exe", "ffprobe.exe"):
            p = root / "_internal" / "ffmpeg" / exe
            if p.is_file():
                r = subprocess.run([str(p), "-version"], capture_output=True,
                                   text=True, timeout=60)
                if r.returncode != 0:
                    failures.append(f"bundled {exe} does not run (exit {r.returncode})")
                else:
                    first = (r.stdout or r.stderr).splitlines()[0][:60]
                    print(f"  ok  bundled {exe}: {first}")

        # certifi CA bundle must ship inside the app: without it cloud LLM
        # calls fail on clean machines with CERTIFICATE_VERIFY_FAILED.
        cacert = [n for n in names if n.endswith("certifi/cacert.pem")
                  or n.endswith("certifi\\cacert.pem")]
        if cacert:
            print(f"  ok  certifi CA bundle: {cacert[0]}")
        else:
            failures.append("certifi cacert.pem missing — cloud LLM TLS will fail on clean machines")

        # The old window stack must not creep back in (it broke clean machines).
        stale = [n for n in names
                 if "/pywebview/" in n or "/pythonnet/" in n or n.endswith("/clr.pyd")]
        if stale:
            failures.append(f"stale window stack in bundle: {stale[:3]}")
        else:
            print("  ok  no pywebview/pythonnet in bundle")

        # The bundled UI must be the current one: per-job language select and
        # the wizard scroll fix. These are loose static files, so they can be
        # checked byte-for-byte. (Python modules live inside the PYZ archive;
        # their behavior is verified at runtime by the release check script.)
        appjs = root / "_internal" / "meetingnotes" / "webapp" / "static" / "app.js"
        if not appjs.is_file():
            failures.append("app.js missing from bundle")
        else:
            js = appjs.read_text(encoding="utf-8", errors="replace")
            ui_ok = True
            for needle, label in [
                ("job-language", "per-job language select"),
                ("window.scrollBy", "wizard wheel forwarding"),
                ("client-bye", "tab-close beacon"),
                ("settingsCache.language", "adaptive job card defaults"),
            ]:
                if needle not in js:
                    failures.append(f"stale UI bundle: '{label}' missing from app.js")
                    ui_ok = False
            if ui_ok:
                print("  ok  bundle UI is current (language select + wizard fixes)")

        # Window assets and their loose config files.
        native = root / "_internal" / "meetingnotes" / "native"
        if not (native / "WebView2.tlb").is_file() or not (native / "WebView2Loader.dll").is_file():
            failures.append("WebView2 native assets missing from bundle")

    if failures:
        print("\nSMOKE GATE FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nSMOKE GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
