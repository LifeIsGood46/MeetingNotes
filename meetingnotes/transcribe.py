"""faster-whisper transcription with sane defaults and profile support."""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config
from .errors import CancelledError
from .profiles import Profile

log = logging.getLogger("meetingnotes.transcribe")


# Display names for the language codes accepted by faster-whisper.
# The code list itself always comes from the installed library
# (see supported_languages); this dict only provides labels.
LANGUAGE_NAMES = {
    "af": "Afrikaans", "am": "Amharic", "ar": "Arabic", "as": "Assamese",
    "az": "Azerbaijani", "ba": "Bashkir", "be": "Belarusian", "bg": "Bulgarian",
    "bn": "Bengali", "bo": "Tibetan", "br": "Breton", "bs": "Bosnian",
    "ca": "Catalan", "cs": "Czech", "cy": "Welsh", "da": "Danish",
    "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
    "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish",
    "fo": "Faroese", "fr": "French", "gl": "Galician", "gu": "Gujarati",
    "ha": "Hausa", "haw": "Hawaiian", "he": "Hebrew", "hi": "Hindi",
    "hr": "Croatian", "ht": "Haitian Creole", "hu": "Hungarian", "hy": "Armenian",
    "id": "Indonesian", "is": "Icelandic", "it": "Italian", "ja": "Japanese",
    "jw": "Javanese", "ka": "Georgian", "kk": "Kazakh", "km": "Khmer",
    "kn": "Kannada", "ko": "Korean", "la": "Latin", "lb": "Luxembourgish",
    "ln": "Lingala", "lo": "Lao", "lt": "Lithuanian", "lv": "Latvian",
    "mg": "Malagasy", "mi": "Maori", "mk": "Macedonian", "ml": "Malayalam",
    "mn": "Mongolian", "mr": "Marathi", "ms": "Malay", "mt": "Maltese",
    "my": "Burmese", "ne": "Nepali", "nl": "Dutch", "nn": "Norwegian Nynorsk",
    "no": "Norwegian", "oc": "Occitan", "pa": "Punjabi", "pl": "Polish",
    "ps": "Pashto", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "sa": "Sanskrit", "sd": "Sindhi", "si": "Sinhala", "sk": "Slovak",
    "sl": "Slovenian", "sn": "Shona", "so": "Somali", "sq": "Albanian",
    "sr": "Serbian", "su": "Sundanese", "sv": "Swedish", "sw": "Swahili",
    "ta": "Tamil", "te": "Telugu", "tg": "Tajik", "th": "Thai",
    "tk": "Turkmen", "tl": "Tagalog", "tr": "Turkish", "tt": "Tatar",
    "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek", "vi": "Vietnamese",
    "yi": "Yiddish", "yo": "Yoruba", "zh": "Chinese", "yue": "Cantonese",
}


def supported_languages() -> list[dict]:
    """Accepted languages from the installed faster-whisper build.

    Single source of truth for the UI dropdown, so a typo'd language
    can never reach the model.
    """
    try:
        from faster_whisper.tokenizer import _LANGUAGE_CODES

        codes = list(_LANGUAGE_CODES)
    except Exception:
        codes = list(LANGUAGE_NAMES)
    return [{"code": c, "name": LANGUAGE_NAMES.get(c, c)} for c in codes]


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptionResult:
    segments: list[Segment] = field(default_factory=list)
    language: str = "ru"
    duration_s: float = 0.0
    elapsed_s: float = 0.0

    def as_dicts(self) -> list[dict]:
        return [
            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text}
            for s in self.segments
        ]

    def text(self) -> str:
        return "\n\n".join(s.text for s in self.segments)


_MODEL_CACHE: dict[tuple[str, str, str], object] = {}


def loaded_model_keys() -> list[tuple[str, str, str]]:
    """(model_size, device, compute_type) triples currently held in memory."""
    return list(_MODEL_CACHE.keys())


def is_model_loaded(model_size: str) -> bool:
    """True if any cached entry matches this model size."""
    return any(k[0] == model_size for k in _MODEL_CACHE)


def unload_models(model_size: str | None = None) -> int:
    """Drop cached models to free VRAM/RAM. Returns number unloaded."""
    keys = [k for k in _MODEL_CACHE if model_size is None or k[0] == model_size]
    for k in keys:
        _MODEL_CACHE.pop(k, None)
    if keys:
        import gc

        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
    return len(keys)


def _cuda_available() -> bool:
    """True when a CUDA device is actually present.

    Checked before attempting a GPU load so no-GPU machines (and VMs without
    passthrough) quietly run on CPU instead of logging a scary DLL error and
    then falling back. If the check itself fails we say "available" and let
    the load attempt decide.
    """
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return True


def _get_model(model_size: str, device: str, compute_type: str,
               on_fallback=None) -> tuple[object, str, str]:
    """Load (and cache) a faster-whisper model, lazily importing the dep.

    Returns ``(model, device, compute_type)`` with the device ACTUALLY used:
    a CUDA load that fails (driver mismatch, DLL trouble) falls back to
    CPU + int8 instead of dying, and callers must report the real device.
    ``on_fallback(reason)`` is invoked (if given) so callers can log it.
    """
    key = (model_size, device, compute_type)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key], device, compute_type

    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "faster-whisper is not installed. Install with `pip install faster-whisper` "
            "or use an environment that has it."
        ) from e

    log.info("loading model %s (%s, %s)", model_size, device, compute_type)
    t0 = time.time()
    try:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception as e:
        if device == "cuda":
            # Short, non-alarming message for the progress line; the full
            # exception goes to the debug log for diagnosis.
            reason = ("The GPU could not be used; running on CPU (int8) instead. "
                      "Transcription will be slower.")
            log.error("CUDA load failed, falling back to CPU: %s\n%s",
                      e, traceback.format_exc())
            if on_fallback:
                on_fallback(reason)
            device, compute_type = "cpu", "int8"
            model = WhisperModel(model_size, device=device, compute_type=compute_type)
        else:
            raise
    log.info("model %s loaded in %.1fs on %s/%s", model_size, time.time() - t0,
             device, compute_type)
    _MODEL_CACHE[(model_size, device, compute_type)] = model
    return model, device, compute_type


def transcribe(
    audio_path: Path,
    profile: Profile,
    *,
    model_size: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
    language: str | None = None,
    time_offset: float = 0.0,
    progress_cb: Callable[..., None] | None = None,
    progress_meta: dict | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> TranscriptionResult:
    """Transcribe an audio file with the given profile.

    Defaults favor meeting recordings: VAD on, aggressive silence handling,
    beam search, no conditioning on previous text (reduces drift/hallucination
    loops on poor audio), and a domain vocabulary prompt.
    """
    model_size = model_size or config.DEFAULT_MODEL_SIZE
    device = device or config.DEFAULT_DEVICE
    compute_type = compute_type or config.DEFAULT_COMPUTE_TYPE
    language = language or profile.language or config.DEFAULT_LANGUAGE

    log = progress_cb or (lambda _msg, _frac=None: None)

    def _do_transcribe(dev: str, comp: str):
        # No CUDA device present: switch before loading so no-GPU machines and
        # passthrough-less VMs never surface a CUDA error, even briefly.
        if dev == "cuda" and not _cuda_available():
            log("No GPU detected — running on CPU (int8). Transcription will be slower.")
            dev, comp = "cpu", "int8"
        model, dev, comp = _get_model(model_size, dev, comp,
                                      on_fallback=lambda reason: log(reason))
        log(f"Transcribing {audio_path.name} [{dev}/{comp}]")
        start = time.time()
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=10,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=300,
                speech_pad_ms=400,
                threshold=0.35,
            ),
            initial_prompt=profile.initial_prompt or None,
            condition_on_previous_text=False,
            log_prob_threshold=-0.5,
            no_speech_threshold=0.5,
            compression_ratio_threshold=2.0,
        )
        result = TranscriptionResult(language=language)
        last_log = time.time()
        for seg in segments_iter:
            if should_cancel and should_cancel():
                raise CancelledError("cancelled by user")
            result.segments.append(
                Segment(
                    start=seg.start + time_offset,
                    end=seg.end + time_offset,
                    text=seg.text.strip(),
                )
            )
            if time.time() - last_log > 20:
                pos = seg.end + time_offset
                log(f"  ... {len(result.segments)} segments, {_progress_line(pos, progress_meta)}",
                    _audio_fraction(pos, progress_meta))
                last_log = time.time()
        result.duration_s = getattr(info, "duration", 0.0) or 0.0
        result.elapsed_s = time.time() - start
        log(f"Transcription done: {len(result.segments)} segments in {result.elapsed_s:.0f}s")
        return result

    return _do_transcribe(device, compute_type)


def _audio_fraction(audio_pos: float, meta: dict | None) -> float | None:
    """Share of the whole job's audio transcribed so far (0..1), if knowable."""
    total = (meta or {}).get("audio_total") or 0.0
    if total <= 0:
        return None
    base = meta.get("audio_done", 0.0) if meta else 0.0
    done = base + max(0.0, audio_pos - base)
    return min(0.999, max(0.0, done / total))


def _progress_line(audio_pos: float, meta: dict | None) -> str:
    """'audio pos + ETA' for the periodic progress line.

    ETA is a simple average-speed projection over the whole job's audio:
    (elapsed wall time / audio processed) * audio remaining. Accurate enough
    after the first ~30s and it self-corrects every progress tick.
    """
    if not meta:
        return f"{audio_pos:.0f}s"
    done = meta.get("audio_done", 0.0) + max(0.0, audio_pos - meta.get("audio_done", 0.0))
    total = meta.get("audio_total") or 0.0
    remaining_audio = max(0.0, total - done)
    elapsed = time.time() - meta.get("stage_started", time.time())
    if done <= 0 or remaining_audio <= 0:
        return f"{audio_pos:.0f}s"
    eta = elapsed / done * remaining_audio
    chunk_note = f", chunk {meta['chunk']}/{meta['chunks']}" if meta.get("chunks", 1) > 1 else ""
    return f"{audio_pos:.0f}s, ETA ~{_fmt_eta(eta)}{chunk_note}"


def _fmt_eta(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
