"""End-to-end pipeline: extract -> transcribe -> correct -> polish -> export."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .audio import extract_audio, chunk_audio, probe_duration
from .profiles import Profile, load_profile
from .transcribe import transcribe, TranscriptionResult
from .correct import apply_corrections, correct_segments
from .llm import LLMProvider, get_provider
from . import polish


@dataclass
class PipelineOptions:
    profile: str = "generic"
    task: str = "raw"                    # raw | clean | notes | actions | testplan | summary | custom
    custom_prompt: str = ""              # used when task == "custom"
    output_name: str | None = None       # filename suffix for the document (defaults to task)
    llm_provider: str | None = None      # ollama | anthropic | openai | None (no LLM)
    llm_model: str | None = None
    model_size: str | None = None
    device: str | None = None
    compute_type: str | None = None
    language: str | None = None
    normalize_audio: bool = True
    chunk_length_s: int = 600
    keep_work: bool = True


@dataclass
class PipelineResult:
    source: Path
    profile: str
    transcript_raw: str = ""
    transcript_corrected: str = ""
    document: str = ""
    segments: list[dict] = field(default_factory=list)
    work_dir: Path | None = None
    outputs: dict[str, Path] = field(default_factory=dict)
    elapsed_s: float = 0.0


def _log(cb: Callable[[str], None] | None, msg: str) -> None:
    if cb:
        cb(msg)


def _resolve_llm(opts: PipelineOptions) -> LLMProvider:
    """Resolve the LLM provider, honoring explicit options then app settings.

    Provider/model come from opts when set, else from persisted settings.
    Credentials come from settings/env based on the chosen provider.
    """
    from .settings import load_settings  # local import to avoid cycles

    saved = load_settings()
    provider_name = opts.llm_provider or saved.llm.provider or "ollama"
    model = opts.llm_model or saved.llm.model or None

    kwargs: dict = {}
    if provider_name == "ollama":
        if saved.llm.ollama_host:
            kwargs["host"] = saved.llm.ollama_host
        if saved.llm.ollama_api_key:
            kwargs["api_key"] = saved.llm.ollama_api_key
    elif provider_name == "compat":
        if saved.llm.compat_base_url:
            kwargs["base_url"] = saved.llm.compat_base_url
        if saved.llm.compat_api_key:
            kwargs["api_key"] = saved.llm.compat_api_key
    elif provider_name == "anthropic":
        if saved.llm.anthropic_api_key:
            kwargs["api_key"] = saved.llm.anthropic_api_key
    elif provider_name == "openai":
        if saved.llm.openai_api_key:
            kwargs["api_key"] = saved.llm.openai_api_key
    elif provider_name == "openrouter":
        if saved.llm.openrouter_api_key:
            kwargs["api_key"] = saved.llm.openrouter_api_key

    return get_provider(provider_name, model=model, **kwargs)


def run_pipeline(
    source: Path,
    work_dir: Path,
    out_dir: Path,
    opts: PipelineOptions,
    progress_cb: Callable[[str], None] | None = None,
) -> PipelineResult:
    """Run the full processing pipeline for one source file.

    Stages run conditionally based on ``opts.task``: ``raw`` stops after
    glossary correction; anything beyond that requires an LLM provider.
    """
    t0 = time.time()
    source = Path(source)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    profile: Profile = load_profile(opts.profile, language=opts.language)
    _log(progress_cb, f"Profile: {profile.name} — {profile.description}")

    # --- Stage 1: audio extraction / preprocessing -------------------------
    wav_path = work_dir / f"{source.stem}.wav"
    _log(progress_cb, f"[1/4] Extracting + normalizing audio -> {wav_path.name}")
    prep = extract_audio(source, wav_path, normalize=opts.normalize_audio)
    _log(progress_cb, f"      duration: {prep.duration_s:.0f}s")

    # --- Stage 2: transcription (chunked if long) --------------------------
    _log(progress_cb, "[2/4] Transcribing")
    chunks = chunk_audio(wav_path, work_dir / "chunks", chunk_length_s=opts.chunk_length_s)
    if len(chunks) > 1:
        _log(progress_cb, f"      long audio: {len(chunks)} chunks")

    all_segments: list[dict] = []
    audio_total = sum(probe_duration(c) for c in chunks)
    audio_done = 0.0
    transcribe_t0 = time.time()
    for i, chunk_path in enumerate(chunks):
        offset = i * opts.chunk_length_s if len(chunks) > 1 else 0.0
        res: TranscriptionResult = transcribe(
            chunk_path,
            profile,
            model_size=opts.model_size,
            device=opts.device,
            compute_type=opts.compute_type,
            language=opts.language,
            time_offset=offset,
            progress_cb=progress_cb,
            progress_meta={
                "chunk": i + 1,
                "chunks": len(chunks),
                "audio_total": audio_total,
                "audio_done": audio_done,
                "stage_started": transcribe_t0,
            },
        )
        audio_done += probe_duration(chunk_path)
        all_segments.extend(res.as_dicts())

    # --- Stage 3: glossary correction --------------------------------------
    _log(progress_cb, "[3/4] Applying glossary corrections")
    corrected_segments = correct_segments(all_segments, profile.corrections)
    transcript_raw = "\n\n".join(s["text"] for s in all_segments)
    transcript_corrected = "\n\n".join(s["text"] for s in corrected_segments)

    result = PipelineResult(
        source=source,
        profile=profile.name,
        transcript_raw=transcript_raw,
        transcript_corrected=transcript_corrected,
        segments=corrected_segments,
        work_dir=work_dir,
    )

    # --- Stage 4: optional LLM cleanup + document --------------------------
    document = ""
    needs_llm = opts.task not in ("raw",)
    if needs_llm:
        llm: LLMProvider = _resolve_llm(opts)
        _log(progress_cb, f"[4/4] LLM stage: provider={llm.provider_name} model={llm.model} task={opts.task}")
        cleaned = polish.clean_transcript(transcript_corrected, llm)
        if opts.task == "clean":
            document = cleaned
        elif opts.task == "custom":
            if not opts.custom_prompt.strip():
                raise ValueError("task=custom requires a non-empty custom_prompt")
            document = polish.generate_custom(cleaned, opts.custom_prompt, llm)
        else:
            document = polish.generate_document(cleaned, opts.task, llm)
    else:
        _log(progress_cb, "[4/4] LLM stage skipped (task=raw)")

    result.document = document

    # --- Export -------------------------------------------------------------
    stem = source.stem
    outputs = {
        "transcript_raw": out_dir / f"{stem}.raw.txt",
        "transcript_corrected": out_dir / f"{stem}.txt",
        "segments_json": out_dir / f"{stem}.json",
    }
    outputs["transcript_raw"].write_text(transcript_raw, encoding="utf-8")
    outputs["transcript_corrected"].write_text(transcript_corrected, encoding="utf-8")
    outputs["segments_json"].write_text(
        json.dumps(corrected_segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if document:
        suffix = opts.output_name or opts.task
        doc_path = out_dir / f"{stem}.{suffix}.md"
        doc_path.write_text(document, encoding="utf-8")
        outputs["document"] = doc_path

    result.outputs = outputs
    result.elapsed_s = time.time() - t0
    _log(progress_cb, f"Done in {result.elapsed_s:.0f}s.")
    return result
