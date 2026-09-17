# MeetingNotes

<img src="assets/logo.png" width="120" alt="MeetingNotes logo" />

Meeting recordings into transcripts and structured documents — notes, action items, test plans. Runs fully on your machine: drop a file in, press Start, open the results folder. First launch walks you through setup with a short guide (reopen it any time from Settings → Replay introduction); Settings lives behind the gear icon in the side rail.

![queue](screenshots/queue.png)

## Download and run

No installer, no admin rights, no accounts.

1. Download `MeetingNotes-<version>-win64.zip` from [Releases](../../releases)
2. Unzip anywhere — USB stick included
3. Double-click `meetingnotes.exe` — it opens its own window, no browser needed

Everything the app writes (settings, whisper models, transcripts) lives inside
its own folder. Delete the folder and it's gone completely. First run downloads
the transcription model of your choice (75 MB – 3 GB) with a live progress bar.

Running from source instead:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
python launcher.py
```

## How it works

| Stage | What happens |
|-------|--------------|
| Extract | ffmpeg pulls and normalizes audio from any video/audio format |
| Transcribe | faster-whisper on your GPU, with a domain vocabulary prompt |
| Correct | per-domain glossaries fix Whisper's mangled terms (`скандала` → SCADA) |
| Polish (optional) | an LLM cleans the text, then writes notes, actions, a test plan or a summary |

Transcription needs nothing but the app. The optional polish step works with a
local model (Ollama, LM Studio, any OpenAI-compatible server) or a cloud key
(Ollama Cloud, OpenRouter, Anthropic, OpenAI) — or with nothing at all, if you
only need raw transcripts.

## Dictionaries

14 domain profiles (software, business, medical, legal, finance, education,
science, marketing, support, HR, dashboards, SCADA, MES, generic) across 6
languages (RU/EN/ES/DE/FR/PT), picked automatically from the audio language.
Each profile pairs an upfront vocabulary prompt with a post-pass correction
map; corrections hold only verified normalizations and real observed
mishearings — never invented ones (enforced by `tests/test_profiles.py`).

## For agents and pipelines

`meetingnotes-cli.exe` in the same folder does everything headless, with
`--json` output and stable exit codes:

```powershell
meetingnotes-cli run meeting.mp4 --task raw --json
```

Full contract, recipes and exit codes: [AGENTS.md](AGENTS.md).

![settings](screenshots/settings.png)

## Development

```powershell
.venv\Scripts\python.exe -m pytest tests/   # 92 tests, seconds, no GPU needed
python -m meetingnotes.eval reference.txt hypothesis.txt   # Word Error Rate
```

`build.bat` rebuilds both exes (PyInstaller onedir). See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)