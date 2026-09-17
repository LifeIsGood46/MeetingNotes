"""Tests for determinate progress fractions + task resolution."""

from pathlib import Path
from unittest import mock

from meetingnotes.pipeline import PipelineOptions, run_pipeline
from meetingnotes.transcribe import TranscriptionResult, Segment, _audio_fraction


def _fake_transcribe(*_args, **kwargs) -> TranscriptionResult:
    r = TranscriptionResult(language="ru", duration_s=2.0, elapsed_s=0.1)
    r.segments = [Segment(start=0.0, end=1.0, text="hello")]
    return r


def _run_with_spy(tmp_path: Path, **opt_kw):
    src = tmp_path / "meeting.mp4"
    src.write_bytes(b"fake-video")
    seen: list = []
    opts = PipelineOptions(profile="generic", task="raw", **opt_kw)
    with mock.patch("meetingnotes.pipeline.extract_audio") as m_extract, \
         mock.patch("meetingnotes.pipeline.chunk_audio") as m_chunk, \
         mock.patch("meetingnotes.pipeline.transcribe", side_effect=_fake_transcribe):
        wav = tmp_path / "work" / "meeting.wav"
        m_extract.return_value = mock.Mock(wav_path=wav, duration_s=2.0)
        m_chunk.return_value = [wav]
        run_pipeline(src, tmp_path / "work", tmp_path / "out", opts,
                     progress_cb=lambda msg, frac=None: seen.append((msg, frac)))
    return seen


def test_pipeline_fractions_monotonic_and_complete(tmp_path: Path):
    seen = _run_with_spy(tmp_path)
    fracs = [f for _, f in seen if f is not None]
    assert fracs, "expected some determinate progress reports"
    assert all(0.0 <= f <= 1.0 for f in fracs)
    assert all(b >= a for a, b in zip(fracs, fracs[1:])), "fractions must not go backwards"
    assert fracs[0] <= 0.05 and fracs[-1] == 1.0


def test_pipeline_transcribe_band(tmp_path: Path):
    """Transcription ticks land in the 0.15–0.85 band (tested via wrapper)."""
    from meetingnotes import pipeline as pl

    seen: list = []
    # drive the wrapper the same way run_pipeline does, with a fake tick
    with mock.patch("meetingnotes.pipeline.transcribe") as m_tr, \
         mock.patch("meetingnotes.pipeline.extract_audio") as m_extract, \
         mock.patch("meetingnotes.pipeline.chunk_audio") as m_chunk, \
         mock.patch("meetingnotes.pipeline.probe_duration", return_value=600.0):
        wav = tmp_path / "c.wav"
        m_extract.return_value = mock.Mock(wav_path=wav, duration_s=600.0)
        m_chunk.return_value = [wav]

        def fake_tr(audio_path, profile, **kwargs):
            cb = kwargs["progress_cb"]
            meta = kwargs["progress_meta"]
            cb("  ... tick", _audio_fraction(300.0, meta))
            return _fake_transcribe()

        m_tr.side_effect = fake_tr
        src = tmp_path / "m.mp4"
        src.write_bytes(b"x")
        run_pipeline(src, tmp_path / "work", tmp_path / "out",
                     PipelineOptions(profile="generic", task="raw"),
                     progress_cb=lambda msg, frac=None: seen.append((msg, frac)))
    tick = [f for m, f in seen if "tick" in m]
    assert tick and tick[0] is not None
    assert 0.15 <= tick[0] <= 0.85


def test_audio_fraction():
    assert _audio_fraction(10.0, None) is None
    assert _audio_fraction(10.0, {"audio_total": 0}) is None
    meta = {"audio_total": 100.0, "audio_done": 0.0}
    assert _audio_fraction(0.0, meta) == 0.0
    assert abs(_audio_fraction(50.0, meta) - 0.5) < 1e-9
    assert _audio_fraction(200.0, meta) <= 0.999  # clamped, never premature 1.0


def test_resolve_task_keeps_untouched_preset():
    from meetingnotes.polish import task_default_prompts
    from meetingnotes.webapp.main import _resolve_task

    presets = task_default_prompts()
    for task in ("notes", "actions", "testplan", "summary"):
        assert _resolve_task(task, presets.get(task, ""))[0] == task


def test_resolve_task_edited_prompt_becomes_custom():
    from meetingnotes.webapp.main import _resolve_task

    assert _resolve_task("notes", "my own instructions")[0] == "custom"


def test_resolve_task_raw_clean_custom_rules():
    from meetingnotes.webapp.main import _resolve_task

    assert _resolve_task("raw", "")[0] == "raw"
    assert _resolve_task("clean", "anything")[0] == "clean"
    assert _resolve_task("custom", "")[0] == "clean"
    assert _resolve_task("custom", "do X")[0] == "custom"
