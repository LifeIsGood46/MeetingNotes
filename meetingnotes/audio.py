"""Audio extraction, preprocessing, and optional chunking via ffmpeg."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AudioPrepResult:
    """Result of preparing input audio."""

    wav_path: Path
    duration_s: float
    source: Path


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    kwargs = dict(capture_output=True, text=True, check=False)
    if sys.platform.startswith("win"):
        # Prevent console window popups in windowed exe
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError:
        raise RuntimeError(
            f"'{cmd[0]}' not found. Install ffmpeg and ensure it is on your PATH. "
            "See https://ffmpeg.org/download.html"
        ) from None


def probe_duration(path: Path) -> float:
    """Return duration in seconds via ffprobe (0.0 on failure)."""
    r = _run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def extract_audio(
    source: Path,
    out_wav: Path,
    *,
    normalize: bool = True,
    sample_rate: int = 16000,
) -> AudioPrepResult:
    """Extract audio from any ffmpeg-supported input into 16kHz mono WAV.

    Optionally applies loudness normalization + a gentle highpass filter,
    which noticeably improves meeting-recording transcription quality.
    """
    out_wav.parent.mkdir(parents=True, exist_ok=True)

    filters = ["highpass=f=80"]
    if normalize:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    af = ",".join(filters)

    cmd = [
        "ffmpeg", "-y", "-i", str(source),
        "-vn",
        "-af", af,
        "-ar", str(sample_rate),
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    r = _run(cmd)
    if r.returncode != 0:
        detail = _clean_ffmpeg_error(r.stderr)
        raise RuntimeError(f"Could not process '{source.name}': {detail}")

    return AudioPrepResult(
        wav_path=out_wav,
        duration_s=probe_duration(out_wav),
        source=source,
    )


def chunk_audio(
    wav_path: Path,
    chunk_dir: Path,
    *,
    chunk_length_s: int = 600,
) -> list[Path]:
    """Split a WAV into fixed-length chunks (last chunk may be shorter).

    Returns an ordered list of chunk paths. Skips chunking if the file is
    already shorter than one chunk.
    """
    duration = probe_duration(wav_path)
    if duration <= chunk_length_s:
        return [wav_path]

    chunk_dir.mkdir(parents=True, exist_ok=True)
    pattern = chunk_dir / "chunk_%03d.wav"
    cmd = [
        "ffmpeg", "-y", "-i", str(wav_path),
        "-f", "segment",
        "-segment_time", str(chunk_length_s),
        "-c", "copy",
        str(pattern),
    ]
    r = _run(cmd)
    if r.returncode != 0:
        detail = _clean_ffmpeg_error(r.stderr)
        raise RuntimeError(f"Audio chunking failed: {detail}")

    return sorted(chunk_dir.glob("chunk_*.wav"))


def _clean_ffmpeg_error(stderr: str) -> str:
    """Return a concise, user-friendly ffmpeg error without version banners."""
    if not stderr:
        return "unknown error"
    lower = stderr.lower()
    if "invalid data found" in lower:
        return "Invalid file format or corrupted file. Please upload a valid audio or video file."
    if "no such file" in lower:
        return "File not found."
    lines = [l.strip() for l in stderr.splitlines() if l.strip()]
    filtered = [
        l for l in lines
        if not l.startswith("ffmpeg version")
        and not l.startswith("built with")
        and not l.startswith("configuration:")
        and not l.startswith("libav")
        and not l.startswith("  ")
    ]
    for keyword in ("Error opening", "Conversion failed", "Invalid"):
        for l in reversed(filtered):
            if keyword.lower() in l.lower():
                return l
    return filtered[-1] if filtered else "unknown error"
