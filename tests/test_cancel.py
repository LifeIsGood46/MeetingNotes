"""Tests for job cancellation plumbing (pipeline + transcribe + models)."""

from pathlib import Path
from unittest import mock

import pytest

from meetingnotes.errors import CancelledError
from meetingnotes.pipeline import PipelineOptions, run_pipeline
from meetingnotes.transcribe import Segment, TranscriptionResult


def test_pipeline_aborts_before_extraction_when_cancelled(tmp_path: Path):
    src = tmp_path / "m.mp4"
    src.write_bytes(b"x")
    called = []
    with mock.patch("meetingnotes.pipeline.extract_audio") as m_extract:
        with pytest.raises(CancelledError):
            run_pipeline(src, tmp_path / "w", tmp_path / "o",
                         PipelineOptions(task="raw"), cancel_cb=lambda: True)
    m_extract.assert_not_called()


def test_pipeline_cancel_mid_transcription(tmp_path: Path):
    """A cancel raised inside the transcribe loop stops the run."""
    src = tmp_path / "m.mp4"
    src.write_bytes(b"x")
    wav = tmp_path / "w" / "m.wav"

    def fake_transcribe(*_a, **kwargs):
        # simulate what transcribe() does when should_cancel() turns true
        raise CancelledError("cancelled by user")

    with mock.patch("meetingnotes.pipeline.extract_audio") as m_extract, \
         mock.patch("meetingnotes.pipeline.chunk_audio", return_value=[wav]), \
         mock.patch("meetingnotes.pipeline.probe_duration", return_value=1.0), \
         mock.patch("meetingnotes.pipeline.transcribe", side_effect=fake_transcribe):
        m_extract.return_value = mock.Mock(wav_path=wav, duration_s=1.0)
        with pytest.raises(CancelledError):
            run_pipeline(src, tmp_path / "w", tmp_path / "o",
                         PipelineOptions(task="raw"))


def test_transcribe_loop_honours_should_cancel(tmp_path: Path):
    from meetingnotes.profiles import load_profile
    from meetingnotes import transcribe as tr

    class FakeSeg:
        start, end, text = 0.0, 1.0, "hi"

    class FakeModel:
        def transcribe(self, *a, **k):
            info = mock.Mock(duration=1.0)
            return iter([FakeSeg(), FakeSeg()]), info

    profile = load_profile("generic", language="en")
    calls = {"n": 0}

    def should_cancel():
        calls["n"] += 1
        return calls["n"] >= 2  # cancel on the second segment

    with mock.patch.object(tr, "_get_model", return_value=FakeModel()):
        with pytest.raises(CancelledError):
            tr.transcribe(tmp_path / "a.wav", profile, model_size="tiny",
                          device="cpu", compute_type="int8",
                          should_cancel=should_cancel)


def test_ensure_downloaded_cancel_raises(tmp_path, monkeypatch):
    from meetingnotes import models as m

    monkeypatch.setattr(m, "is_model_downloaded", lambda size: False)
    dl = m.ModelDownload("tiny")
    dl.status = "downloading"
    dl._thread = mock.Mock()
    dl._thread.join = lambda timeout=None: None
    monkeypatch.setattr(m, "download_model", lambda size: dl)

    with pytest.raises(CancelledError):
        m.ensure_downloaded("tiny", should_cancel=lambda: True)


def test_ensure_downloaded_no_flag_runs_to_completion(tmp_path, monkeypatch):
    from meetingnotes import models as m

    monkeypatch.setattr(m, "is_model_downloaded", lambda size: True)
    m.ensure_downloaded("tiny")  # returns immediately, no error
