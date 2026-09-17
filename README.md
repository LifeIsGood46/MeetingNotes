<div align="center">

# meetingnotes

**Meeting recordings → transcripts → structured documents. Local. Fast. Zero-config for raw transcripts.**

Drop a recording, get a timestamped transcript — optionally cleaned and reshaped
by an LLM into notes, action items, or a test plan.

[![tests](https://img.shields.io/badge/tests-83%20passing-brightgreen)](#development)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![platform](https://img.shields.io/badge/platform-Windows-lightgrey)](#run)

</div>

---

![settings](screenshots/settings.png)

## Why

Raw Whisper output on noisy, mixed-language technical meetings is borderline
unusable — misheard domain terms, drift, no punctuation discipline. meetingnotes
closes the gap with domain glossaries, audio preprocessing, and a two-phase
LLM cleanup stage. Everything runs locally through a web UI, a headless CLI
for agents, or both.

## How it works

| Stage | What happens |
|-------|--------------|
| **Extract** | ffmpeg pulls audio (+ loudness normalization, highpass) from any format |
| **Transcribe** | faster-whisper (CUDA/CPU), domain vocabulary prompt, VAD, no drift |
| **Correct** | per-profile glossary fixes Whisper-mangled terms (`скандала` → SCADA) |
| **Polish (optional)** | LLM cleanup, then notes / actions / test plan / summary / your own prompt |

## Highlights

- **Zero-config raw transcripts** — no API keys, no LLM needed
- **Bring your own LLM**: Ollama (local/cloud), LM Studio & any OpenAI-compatible
  server, OpenRouter, Anthropic, OpenAI
- **14 domain profiles in 6 languages** (RU/EN/ES/DE/FR/PT), auto-picked by audio language
- **Per-job control** in the UI: staged cards with per-job profile/output/prompt
- **Model manager**: download with live progress, preload, unload (free VRAM), delete
- **Results on disk**: `Downloads\meetingnotes\<filename>\`, one click to Explorer
- **Agent-ready CLI** with `--json` output and stable exit codes — see [AGENTS.md](AGENTS.md)
- **Measurable quality**: built-in Word Error Rate evaluation
- Jobs persist across restarts; safe per-job cleanup (marker-guarded deletion)

## Run

**GUI:** double-click `meetingnotes.exe` (build once with `build.bat`) — the UI
opens in your browser, everything from drag & drop to results happens there.

**CLI (agents / pipelines):**

```powershell
meetingnotes-cli run meeting.mp4 --task raw --json
```

```json
{
  "source": "meeting.mp4",
  "task": "raw",
  "outputs": {
    "transcript_raw": "C:\\...\\meeting.raw.txt",
    "transcript_corrected": "C:\\...\\meeting.txt",
    "segments_json": "C:\\...\\meeting.json"
  }
}
```

Formatted output needs an LLM — Ollama local works great:

```powershell
meetingnotes-cli run meeting.mp4 --profile scada --task testplan --llm ollama --llm-model gemma4:31b
```

Full command reference, exit codes, and automation recipes: **[AGENTS.md](AGENTS.md)**.

## Dev setup

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
python launcher.py          # GUI
python -m meetingnotes.cli run meeting.mp4   # CLI
```

Build both exes: `build.bat` (PyInstaller, ~700 MB each — CUDA libraries bundled).

## Quality measurement

Built-in Word Error Rate evaluation with zero dependencies — hand-check a short
excerpt and measure before/after:

```powershell
python -m meetingnotes.eval reference.txt hypothesis.txt
```

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests/
```

83 tests, seconds to run — no GPU, no network, external services mocked.

## Roadmap

- [ ] Speaker diarization
- [ ] Before/after WER report in the UI
- [ ] Queue-side batch export

## License

[MIT](LICENSE)