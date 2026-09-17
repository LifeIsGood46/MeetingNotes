# Contributing

Thanks for your interest in improving meetingnotes!

## Development setup

```powershell
git clone <repo-url>
cd meetingnotes
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Running tests

```powershell
.venv\Scripts\python.exe -m pytest tests/
```

The suite runs in seconds — no GPU, no network, no model downloads required
(external services are mocked).

## Project layout

```
meetingnotes/            core package
  webapp/                FastAPI app + templates/static (GUI)
  llm/                   one file per LLM provider
  profiles/              domain dictionaries (JSON, 6 languages)
  cli.py                 headless interface (see AGENTS.md)
launcher.py              GUI exe entry point
cli_entry.py             CLI exe entry point
build.bat                builds both exes with PyInstaller
```

## Guidelines

- **No secrets in code or tests.** Keys belong in `.env` (gitignored) or the
  settings file.
- **Every user-visible behavior change** gets a test. The suite is fast — keep it that way.
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
3. If you touched the webapp or profiles, rebuild with `build.bat` and smoke-test
   both exes.
4. Keep PRs focused — one feature or fix each.

## Reporting bugs

Include: the command or UI action, the error text, and (for transcription issues)
the model size and device. Audio samples that reproduce ASR problems are gold —
trim to the failing 30 seconds.