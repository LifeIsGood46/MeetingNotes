"""Tests for pipeline orchestration with Whisper + ffmpeg mocked out.

The goal is to verify the wiring (extract -> transcribe -> correct -> export)
without needing a GPU or real audio.
"""

from pathlib import Path
from unittest import mock

from meetingnotes.pipeline import PipelineOptions, run_pipeline
from meetingnotes.profiles import load_profile
from meetingnotes.transcribe import TranscriptionResult, Segment


def _fake_transcribe(*_args, **kwargs) -> TranscriptionResult:
    r = TranscriptionResult(language="ru", duration_s=2.0, elapsed_s=0.1)
    r.segments = [Segment(start=0.0, end=1.0, text="utilikt and shou mesedj")]
    return r


def test_pipeline_raw_end_to_end(tmp_path: Path):
    src = tmp_path / "meeting.mp4"
    src.write_bytes(b"fake-video")

    opts = PipelineOptions(profile="scada", task="raw")

    from meetingnotes.profiles import Profile

    corrections = {"utilikt": "Utilitect", "shou mesedj": "showMessage"}
    fake_profile = Profile(
        name="scada", description="d", language="ru",
        initial_prompt="p", corrections=corrections,
    )

    with mock.patch("meetingnotes.pipeline.extract_audio") as m_extract, \
         mock.patch("meetingnotes.pipeline.chunk_audio") as m_chunk, \
         mock.patch("meetingnotes.pipeline.transcribe", side_effect=_fake_transcribe), \
         mock.patch("meetingnotes.pipeline.load_profile", return_value=fake_profile):

        wav = tmp_path / "work" / "meeting.wav"
        m_extract.return_value = mock.Mock(wav_path=wav, duration_s=2.0)
        m_chunk.return_value = [wav]

        result = run_pipeline(src, tmp_path / "work", tmp_path / "out", opts)

    # Glossary correction applied to the mocked ASR text
    assert "Utilitect" in result.transcript_corrected
    assert "showMessage" in result.transcript_corrected
    # Raw preserved for comparison
    assert "utilikt" in result.transcript_raw

    # Files written
    for key in ("transcript_raw", "transcript_corrected", "segments_json"):
        assert key in result.outputs
        assert result.outputs[key].exists()
def test_pipeline_raw_skips_llm(tmp_path: Path):
    src = tmp_path / "m.mp4"
    src.write_bytes(b"x")
    opts = PipelineOptions(profile="scada", task="raw")

    with mock.patch("meetingnotes.pipeline.extract_audio") as m_extract, \
         mock.patch("meetingnotes.pipeline.chunk_audio", return_value=[tmp_path / "c.wav"]), \
         mock.patch("meetingnotes.pipeline.transcribe", side_effect=_fake_transcribe), \
         mock.patch("meetingnotes.pipeline.get_provider") as m_llm:

        m_extract.return_value = mock.Mock(wav_path=tmp_path / "c.wav", duration_s=1.0)
        run_pipeline(src, tmp_path / "work", tmp_path / "out", opts)
        m_llm.assert_not_called()  # raw task must not touch any LLM


def test_pipeline_loads_language_matching_dict(tmp_path: Path):
    """The transcription language picks the right dictionary variant."""
    src = tmp_path / "meeting.mp4"
    src.write_bytes(b"fake-video")

    seen = {}

    def _spy_transcribe(audio_path, profile, **kwargs):
        seen["prompt"] = profile.initial_prompt
        seen["language"] = profile.language
        return _fake_transcribe()

    opts = PipelineOptions(profile="software", task="raw", language="en")
    with mock.patch("meetingnotes.pipeline.extract_audio") as m_extract, \
         mock.patch("meetingnotes.pipeline.chunk_audio") as m_chunk, \
         mock.patch("meetingnotes.pipeline.transcribe", side_effect=_spy_transcribe):

        wav = tmp_path / "work" / "meeting.wav"
        m_extract.return_value = mock.Mock(wav_path=wav, duration_s=2.0)
        m_chunk.return_value = [wav]
        run_pipeline(src, tmp_path / "work", tmp_path / "out", opts)

    assert seen["language"] == "en"
    assert "РїСѓР»" not in seen["prompt"]  # no Cyrillic bias in the EN run
    assert "pull request" in seen["prompt"]


"""Tests for the settings store: defaults, persistence, masking, update."""

import json
from pathlib import Path

import pytest

from meetingnotes import settings as settings_mod
from meetingnotes.settings import AppSettings, load_settings, save_settings, update_settings


@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", path)
    return path


def test_defaults_when_no_file(tmp_settings):
    s = load_settings()
    assert s.model_size == "large-v3"
    # Fresh install = zero-config: works without any LLM.
    assert s.llm.provider == "none"
    assert s.llm.compat_base_url == "http://localhost:1234/v1"


def test_save_and_reload(tmp_settings):
    s = AppSettings(model_size="medium", compute_type="int8")
    s.llm.provider = "anthropic"
    s.llm.anthropic_api_key = "sk-ant-real"
    save_settings(s)
    again = load_settings()
    assert again.model_size == "medium"
    assert again.llm.provider == "anthropic"
    assert again.llm.anthropic_api_key == "sk-ant-real"


def test_to_public_dict_masks_secrets(tmp_settings):
    s = load_settings()
    s.llm.anthropic_api_key = "sk-ant-abcdefghijkl"
    public = s.to_public_dict()
    assert public["llm"]["anthropic_api_key"] == "********ijkl"
    assert "sk-ant-abcdefghijkl" not in public["llm"]["anthropic_api_key"]


def test_update_does_not_overwrite_key_with_mask(tmp_settings):
    s = AppSettings()
    s.llm.openai_api_key = "sk-openai-secret"
    save_settings(s)
    # UI sends back the masked value on save; it must NOT clobber the real key
    update_settings({"llm": {"openai_api_key": "********cret"}, "language": "en"})
    reloaded = load_settings()
    assert reloaded.llm.openai_api_key == "sk-openai-secret"
    assert reloaded.language == "en"

# ---------------- ETA progress ----------------

def test_fmt_eta_units():
    from meetingnotes.transcribe import _fmt_eta

    assert _fmt_eta(45) == "45s"
    assert _fmt_eta(90) == "1m30s"
    assert _fmt_eta(3720) == "1h02m"


def test_progress_line_without_meta_is_plain():
    from meetingnotes.transcribe import _progress_line

    assert _progress_line(42.0, None) == "42s"


def test_progress_line_with_meta_shows_eta_and_chunk():
    import time as _t

    from meetingnotes.transcribe import _progress_line

    meta = {
        "chunk": 2, "chunks": 2,
        "audio_total": 1200.0, "audio_done": 600.0,
        "stage_started": _t.time() - 60.0,
    }
    # 600s of audio processed in 60s of wall time -> 600s remaining -> ETA 1m00s
    line = _progress_line(600.0, meta)  # start of chunk 2
    assert "ETA ~1m00s" in line
    assert "chunk 2/2" in line


def test_progress_line_handles_zero_done():
    from meetingnotes.transcribe import _progress_line

    meta = {"chunk": 1, "chunks": 1, "audio_total": 100.0, "audio_done": 0.0,
            "stage_started": 0}
    assert _progress_line(0.0, meta) == "0s"


def test_progress_line_final_chunk_no_negative_eta():
    from meetingnotes.transcribe import _progress_line

    meta = {"chunk": 1, "chunks": 1, "audio_total": 60.0, "audio_done": 0.0,
            "stage_started": 0}
    # pos beyond total -> remaining clamps to 0 -> no ETA
    assert _progress_line(60.0, meta) == "60s"

