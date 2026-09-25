# Contributing

Thanks for your interest in improving MeetingNotes!

## Development setup

```powershell
git clone <repo-url>
cd transcribe
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
python launcher.py
```

Running from source needs ffmpeg on PATH (the packaged exe bundles its own
copy). On Windows: `winget install ffmpeg`.

## Running tests

```powershell
.venv\Scripts\python.exe -m pytest tests/
```

The suite runs in seconds: no GPU, no network, no model downloads required
(external services are mocked).

## Building the release

`build.bat` builds both exes and the portable zip. It also runs a smoke gate
that unpacks the zip and checks the packaged app the way a fresh user would:
bundled ffmpeg runs, the CLI responds, the certifi CA bundle ships, the old
window stack is gone, and the WebView2 assets are present. Don't ship a build
that fails the gate.

```powershell
build.bat
```

The build downloads ffmpeg into `vendor/` on first run (gitignored, ~200 MB).
The WebView2 loader + type library live in `meetingnotes/native/`.

## Project layout

```
meetingnotes/            core package
  webapp/                FastAPI app + templates/static (GUI)
  llm/                   one file per LLM provider
  profiles/              domain dictionaries (JSON, 6 languages)
  native/                WebView2 loader + typelib (window hosting)
  errors.py              shared exception types (cancellation)
  ffbin.py               ffmpeg/ffprobe resolution (bundled > PATH)
  net.py                 SSL context (certifi) for providers
  webview2win.py         native window (COM, no .NET)
  cli.py                 headless interface (see AGENTS.md)
launcher.py              GUI exe entry point
cli_entry.py             CLI exe entry point
build.bat                builds both exes with PyInstaller
scripts/smoke_release.py post-build gate
```

## Guidelines

- **No secrets in code or tests.** Keys belong in `.env` (gitignored) or the
  settings file.
- **Every user-visible behavior change** gets a test. The suite is fast, so keep it that way.
- **Packaging changes need a smoke run.** Anything that touches bundled files
  (ffmpeg, native/, templates, static) must be verified through `build.bat`,
  not just pytest: unit tests can't see a missing binary in the zip.
- **Corrections maps** (domain dictionaries) follow the policy enforced by
  `tests/test_profiles.py`: only real normalizations, observed mishearings, or
  documented Whisper artifacts. No invented entries.
- Keep `--json` output stable: agents parse it. Additive changes are fine;
  renaming/removing fields is a breaking change.
- Error messages should say what to do next ("open Settings → LLM"), not just
  what broke.

## Pull requests

1. Fork, create a feature branch.
2. `pytest tests/` must pass.
3. If you touched the webapp, packaging, or profiles, run `build.bat` and
   smoke-test the resulting exe.
4. Keep PRs focused: one feature or fix each.

## Reporting bugs

Include: the command or UI action, the error text, and (for transcription issues)
the model size and device. Audio samples that reproduce ASR problems are gold;
trim to the failing 30 seconds.
