# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Retry button on failed job cards (failed → staged, profile/task/prompt kept).
- Determinate job progress bar (0–100% across extract/download/transcribe/
  correct/LLM) with live ETA text; same-state updates no longer rebuild cards.
- Host preset chips in Settings: Ollama Local/Cloud, LM Studio/llama.cpp/vLLM.
- First-launch guide with real-UI screenshots; replay from Settings.
- Icon rail navigation, jobs model pill, per-job folders explanation.
- Per-job folders use the file stem (`results/<name>/`, never `<name>.mp4`);
  repeat runs get ` (2)`, ` (3)` suffixes instead of overwriting; failed runs
  leave no empty folders behind.
- Save-folder Browse button (native Explorer picker); host preset chips for
  Ollama (Local/Cloud) and compat servers (LM Studio/llama.cpp/vLLM).
- OpenAI/Anthropic providers rewritten to key-based REST (no SDK needed):
  live model lists, token-free credential check, 401/429/404 mapping.
### Fixed
- Untouched preset prompts no longer coerce the LLM stage to `task=custom`.
- LLM refresh button no longer trapped inside its label; fetches can't overlap.
- Model download counters show decimals (`244.4/3090 MB` instead of `0/0 MB`).

## [0.2.0] - 2026-09

### Added
- Headless CLI (`meetingnotes-cli`) with full app parity: run, profiles,
  model management, settings, serve — all with `--json` output and stable
  exit codes (0/1/2) for agents and pipelines.
- Second exe target: `meetingnotes-cli.exe` (console build).
- **None (raw transcripts only)** LLM provider — the app works with zero
  configuration; this is the fresh-install default.
- **LM Studio / OpenAI-compatible** provider (vLLM, llama.cpp, LocalAI) with
  configurable URL; blank key = local server, key = hosted.
- Domain dictionaries in 6 languages (RU/EN/ES/DE/FR/PT), auto-selected by
  transcription language; language-aware profile loading with EN fallback.
- Whisper model management from the UI: download with progress, preload,
  unload (free VRAM), delete, open model cache folder.
- ETA estimate in transcription progress (self-correcting average-speed
  projection with chunk position).
- Word Error Rate evaluation (`meetingnotes.eval`, `meetingnotes/wer.py`).
- Jobs persist across restarts; per-job delete with/without files
  (marker-guarded safe deletion).
- Results saved to `Downloads\meetingnotes\<filename>\`; "Open folder"
  reveals them in Explorer with the file selected.
- Fail-fast LLM credential check before transcription; friendly 401/429
  error messages.

### Fixed
- SSE endpoint made async — enough browser tabs used to starve the thread
  pool and hang the whole app ("stuck loading forever").
- Explorer "Open folder" on paths with spaces (raw command line, DEVNULL
  handles for the windowed exe).
- File-picker chooser stacking on repeated dropzone clicks.
- LLM model dropdown no longer resets to the first option on refresh.
- CUDA DLL discovery inside the PyInstaller bundle (`cublas64_12.dll`).
- Console window popups from ffmpeg subprocesses in the windowed exe.

## [0.1.0] - 2026-08
- Initial release: FastAPI UI, faster-whisper transcription, per-domain
  glossary correction, two-phase LLM cleanup, PyInstaller packaging.