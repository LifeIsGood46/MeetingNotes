"""Configuration and environment setup.

Keeps all path juggling and CUDA DLL bootstrapping in one place so the rest
of the codebase never has to think about it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from .env in the project root (if present).

    Only sets variables not already defined in the environment.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# ---------------------------------------------------------------------------
# Base paths
# ---------------------------------------------------------------------------

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
PROFILES_DIR = PACKAGE_DIR / "profiles"

# Frozen (PyInstaller) apps extract to a temp dir that is DELETED on exit,
# so outputs must live in a stable user dir. Final artifacts go straight to
# the user's Downloads folder; intermediate files stay under ~/.meetingnotes.
if getattr(sys, "frozen", False):
    _base = Path.home() / ".meetingnotes"
    _downloads = Path.home() / "Downloads" / "meetingnotes"
    WORK_DIR = Path(os.environ.get("MEETING_NOTES_WORK_DIR", _base / "work"))
    OUT_DIR = Path(os.environ.get("MEETING_NOTES_OUT_DIR", _downloads))
else:
    WORK_DIR = Path(os.environ.get("MEETING_NOTES_WORK_DIR", PROJECT_ROOT / "work"))
    OUT_DIR = Path(os.environ.get("MEETING_NOTES_OUT_DIR", PROJECT_ROOT / "out"))

# ---------------------------------------------------------------------------
# Model defaults
# ---------------------------------------------------------------------------

DEFAULT_MODEL_SIZE = os.environ.get("MEETING_NOTES_MODEL", "large-v3")
DEFAULT_DEVICE = os.environ.get("MEETING_NOTES_DEVICE", "cuda")
DEFAULT_COMPUTE_TYPE = os.environ.get("MEETING_NOTES_COMPUTE_TYPE", "float16")
DEFAULT_LANGUAGE = os.environ.get("MEETING_NOTES_LANGUAGE", "ru")

# ---------------------------------------------------------------------------
# CUDA DLL bootstrapping (Windows + faster-whisper)
#
# faster-whisper via CTranslate2 needs cuBLAS / CUDA runtime DLLs visible.
# On Windows with Python 3.13+ they aren't on PATH by default. This scans the
# installed nvidia pip packages and adds their bin dirs.
# ---------------------------------------------------------------------------


def setup_cuda_dlls() -> None:
    """Add nvidia pip package DLL dirs to the DLL search path (no-op elsewhere)."""
    if not sys.platform.startswith("win"):
        return

    candidates: list[Path] = []
    # 1. PyInstaller bundle (sys._MEIPASS)
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        for nvidia_root in [meipass / "nvidia", meipass / "_internal" / "nvidia"]:
            if nvidia_root.is_dir():
                for pkg in nvidia_root.iterdir():
                    bin_dir = pkg / "bin"
                    if bin_dir.is_dir():
                        candidates.append(bin_dir)
        # Also check for ctranslate2 bundled libs
        for p in [meipass, meipass / "_internal"]:
            if (p / "cublas64_12.dll").exists():
                candidates.append(p)
            ct2 = p / "ctranslate2"
            if ct2.is_dir():
                candidates.append(ct2)
    # 2. Regular site-packages
    for site in _site_packages():
        nvidia = site / "nvidia"
        if not nvidia.is_dir():
            continue
        for pkg in nvidia.iterdir():
            bin_dir = pkg / "bin"
            if bin_dir.is_dir():
                candidates.append(bin_dir)

    for d in candidates:
        try:
            os.add_dll_directory(str(d))
        except (OSError, AttributeError):
            pass

    # Prepend to PATH as a fallback for older resolution logic
    if candidates:
        os.environ["PATH"] = ";".join(str(d) for d in candidates) + ";" + os.environ.get("PATH", "")


def _site_packages() -> list[Path]:
    """Return all site-packages dirs for the current interpreter."""
    paths: list[Path] = []
    for p in sys.path:
        candidate = Path(p)
        if candidate.name == "site-packages" and candidate.is_dir():
            paths.append(candidate)
    return paths


# Run once on import — cheap and idempotent.
setup_cuda_dlls()
