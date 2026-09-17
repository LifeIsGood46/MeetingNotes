# AGENTS.md — meetingnotes CLI contract

`meetingnotes-cli.exe` (or `python -m meetingnotes.cli`) is the headless interface for
autonomous agents and automated pipelines. Every command is non-interactive, every
command supports `--json` for machine-readable output, and exit codes are stable.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | success |
| `1` | usage error (bad flags, missing file, unknown key) |
| `2` | processing failure (transcription error, LLM error, network) |

Check the exit code, not stdout content — stdout format is documented but errors
on stderr take precedence.

## First-run requirements

1. **ffmpeg** must be on `PATH` (audio extraction).
2. **Whisper model**: none is downloaded automatically by the CLI. Check availability
   with `models list`; a `run` with a missing model fails with exit code 2. Download
   explicitly: `meetingnotes-cli models download large-v3` (large-v3 ≈ 3 GB).
3. **LLM is optional.** Default `--task raw` needs no LLM at all. Formatted output
   (`--task clean|notes|actions|testplan|summary`) requires a configured provider —
   see [Settings](#settings).

## Commands

### transcribe a file

```powershell
meetingnotes-cli run meeting.mp4 --task raw
```

Key flags (all optional; defaults come from saved settings):

| Flag | Values |
|------|--------|
| `--task` | `raw` (no LLM) · `clean` · `notes` · `actions` · `testplan` · `summary` · `custom` |
| `--profile` | see `meetingnotes-cli profiles` (default: `generic`) |
| `--llm` | `none` · `ollama` · `compat` (LM Studio/vLLM/llama.cpp) · `anthropic` · `openai` · `openrouter` |
| `--llm-model` | model name for the provider |
| `--model` | whisper size: `tiny base small medium large-v1 large-v2 large-v3` |
| `--device` | `cuda` · `cpu` |
| `--language` | ISO code (`ru`, `en`, …) |
| `--out-dir` | output directory (default `~\Downloads\meetingnotes`) |
| `--json` | machine-readable output |

**Agent-friendly one-liner** — raw transcript, no LLM, JSON result:

```powershell
meetingnotes-cli run meeting.mp4 --task raw --json
```

JSON payload:

```json
{
  "source": "meeting.mp4",
  "profile": "scada",
  "task": "raw",
  "elapsed_s": 942.1,
  "outputs": {
    "transcript_raw": "C:\\...\\meeting.raw.txt",
    "transcript_corrected": "C:\\...\\meeting.txt",
    "segments_json": "C:\\...\\meeting.json"
  }
}
```

`segments_json` is an array of `{start, end, text}` — the usual machine-readable
transcript for downstream tooling. `--task raw` never calls any LLM and never needs keys.

Custom output shaping:

```powershell
meetingnotes-cli run meeting.mp4 --task custom --prompt "Extract decisions as a bullet list" --llm ollama --llm-model gemma4:31b --json
```

### discover profiles / tasks / prompts

```powershell
meetingnotes-cli profiles --json
```

Returns `profiles` (name, description, language, correction count), `tasks`, and
`task_prompts` (built-in prompt per task — useful as templates for `--task custom`).

### manage whisper models

```powershell
meetingnotes-cli models list
meetingnotes-cli models status large-v3
meetingnotes-cli models download large-v3      # blocks until done
meetingnotes-cli models delete small
meetingnotes-cli models open-folder
```

All accept `--json`. `download` blocks until finished; poll `status` instead if
you need progress from another process. (Memory preload/unload is GUI-only —
a one-shot CLI process cannot keep a model warm for later invocations.)

### settings

```powershell
meetingnotes-cli settings get language --json          # single value
meetingnotes-cli settings get llm --json               # whole section
meetingnotes-cli settings set default_profile=scada language=ru
meetingnotes-cli settings reset                        # factory defaults
```

Settings persist to `~/.meetingnotes/settings.json`. Keys mirror the JSON returned by
`settings get`. Secrets are write-only from the CLI (`get` returns masked values).

### run the web UI

```powershell
meetingnotes-cli serve --port 8123
```

Same app as the GUI exe; useful when you want the UI on a headless box you RDP into.

## Automation recipes

**Nightly folder watch (PowerShell):**

```powershell
Get-ChildItem "C:\Recordings\*.mp4" | ForEach-Object {
    $out = "C:\Processed\$($_.BaseName)"
    meetingnotes-cli run $_.FullName --task raw --out-dir $out --json
    if ($LASTEXITCODE -ne 0) { Write-Warning "failed: $($_.Name)" }
}
```

**Transcribe + LLM test plan with Ollama local:**

```powershell
meetingnotes-cli run meeting.mp4 --profile scada --task testplan --llm ollama --llm-model gemma4:31b --json
```

**Check readiness before batching:**

```powershell
meetingnotes-cli models status large-v3 --json   # .installed == true required
meetingnotes-cli settings get llm --json         # provider configured?
```

## Notes for agent authors

- Long files are chunked automatically (10-minute chunks) with self-correcting ETA in
  human progress lines (suppressed in `--json` mode).
- Results always land in the output dir as `<name>.raw.txt`, `<name>.txt` (glossary-
  corrected), `<name>.json` (timestamped segments), and `<name>.<task>.md` for LLM tasks.
- The GUI exe and CLI share settings and the model cache — downloads in one are
  visible to the other.
- A second instance is prevented for the GUI exe only; parallel CLI runs are your
  responsibility (GPU memory is finite — serialize `run` commands).