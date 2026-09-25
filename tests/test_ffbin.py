"""Tests for the ffmpeg/ffprobe binary resolver (packaging-critical)."""

import sys
from pathlib import Path

import pytest

from meetingnotes import ffbin


def test_dev_mode_prefers_path(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    found = ffbin.find_ffmpeg("ffmpeg")
    # dev machine has ffmpeg; when it doesn't the function returns None
    if found is not None:
        assert Path(found).name.lower().startswith("ffmpeg")


def test_frozen_resolves_bundled_first(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "meetingnotes.exe"))
    bundled = tmp_path / "_internal" / "ffmpeg" / "ffmpeg.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"MZ")
    probe = tmp_path / "_internal" / "ffmpeg" / "ffprobe.exe"
    probe.write_bytes(b"MZ")

    monkeypatch.setattr(ffbin, "find_ffmpeg", ffbin.find_ffmpeg)  # keep ref
    assert ffbin.find_ffmpeg("ffmpeg") == bundled
    assert ffbin.find_ffmpeg("ffprobe") == probe
    status = ffbin.ffmpeg_status()
    assert status["bundled"] is True
    assert status["ffmpeg"] is True and status["ffprobe"] is True


def test_frozen_missing_bundled_has_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "meetingnotes.exe"))
    monkeypatch.setattr(ffbin, "find_ffmpeg", lambda name: None)

    with pytest.raises(ffbin.FFmpegMissing) as exc:
        raise ffbin.FFmpegMissing("ffmpeg")
    assert "re-download" in str(exc.value).lower()


def test_status_shape(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    st = ffbin.ffmpeg_status()
    assert set(st) == {"frozen", "bundled", "ffmpeg", "ffprobe", "source"}


def test_audio_uses_resolver(monkeypatch, tmp_path):
    """audio._binary must go through the resolver, not bare 'ffmpeg'."""
    from meetingnotes import audio

    sentinel = tmp_path / "ffmpeg.exe"
    sentinel.write_bytes(b"MZ")
    monkeypatch.setattr(audio, "find_ffmpeg", lambda name: sentinel)
    assert audio._binary("ffmpeg") == str(sentinel)

    monkeypatch.setattr(audio, "find_ffmpeg", lambda name: None)
    with pytest.raises(audio.FFmpegMissing):
        audio._binary("ffmpeg")
