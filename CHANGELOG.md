# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Debug log for handover.** The backend writes one rotating file
  (`data/logs/meetingnotes.log`, `.local/logs/` in dev) covering startup,
  environment, window strategy, device selection and CPU fallbacks, every
  job's lifecycle, pipeline stages and full error tracebacks. Open it from
  Help → Troubleshooting ("Open log folder"); the UI also reports uncaught
  errors into it. `MEETING_NOTES_DEBUG=1` adds verbose detail.
- Per-job audio-language selector on staged cards, next to profile, output
  and model. Blank means "use the saved default".
- Every job now writes a Markdown document: LLM tasks as before, and `raw`
  jobs as a timestamped transcript (`<name>.raw.md`) so the output shape is
  the same regardless of task.

### Fixed
- **CPU fallback was invisible and misreported.** A run that fell back to CPU
  still printed `[cuda/...]`, and the raw DLL error flashed in the progress
  bar. The real device is now reported, a no-GPU pre-check switches to CPU
  silently before any load is attempted, and the user-facing message is a
  plain "running on CPU (int8)" — the raw error goes to the debug log.
- **Ollama cloud without a key.** A remote host and no key used to hit the
  native daemon API shape and fail confusingly; it now fails fast with a
  clear message, and cloud model listing uses the OpenAI-compatible endpoint
  instead of `/api/tags`.
- **Wizard LLM step covered the fields it described.** The settings modal now
  shifts left, reserving a column where the tip docks; the spotlight follows
  the target. Long settings steps also scroll instantly (the smooth-scroll
  animation raced the highlight placement).
- **Wizard duplicated its instructions.** Step 4 → 5 flow rewritten: step 4
  auto-advances once a recording is added instead of repeating "there's
  already a job", and step 5 explains the job card's per-file controls.
- **UI lag on VMs.** Job-list rendering now updates only changed cards (a
  full rebuild detached the node under the pointer, which is what made
  buttons feel like they needed a second click), the wizard re-places its
  highlight at most once per animation frame, and the infinite pulse
  animation, layout-thrashing progress sweep and heavy outer glows are gone.
- **Closing the browser tab left a python process running.** In browser-
  fallback mode there was no window to watch, so the server lived on. The UI
  now reports liveness (SSE + a `pagehide` beacon) and the launcher stops the
  server once the last tab is gone.
- **Confirm dialogs opened during the walkthrough rendered underneath it**
  (dimmed and unclickable). The modal layer now sits above every wizard layer.
- **The walkthrough appeared late.** It is shown as soon as settings load
  instead of after several sequential round-trips.
- **VM rendering: guide appeared late, buttons needed two clicks, white bands
  on maximize.** Chromium's occlusion/background throttling and GPU
  compositing behave badly under virtualized graphics; the window host
  disables them, re-asserts the WebView bounds on geometry changes, and moves
  focus into the page on window activation.
- **Scrolling over the wizard tip did nothing.** Wheel events on the tip are
  forwarded to the settings pane underneath.
- Wizard step titles update live during the polling tick, not just on entry.

## [0.3.0] - 2026-09

### Added
- Interactive first-launch walkthrough (coach-marks over the real UI:
  pick a model and language, add a recording, press Start). Six reactive,
  skippable steps, including the optional LLM setup. Reopenable from Help.
- **Stop button on running jobs**: abort a queued or running transcription;
  the job lands in a new `cancelled` state and can be retried.
- Help overlay in the rail: walkthrough, folder layout, output type
  reference, quitting notes.
- Per-job model picker on staged cards; profiles can define a default
  model (e.g. a quick "tiny" profile).
- "Try with a sample" button: stages a short built-in recording (English
  speech) so a new user always has something to transcribe.
- CPU fallback: a CUDA failure now switches to CPU + int8 with a clear
  progress message instead of erroring out; Device dropdown offers cpu.
- Post-build smoke gate (`scripts/smoke_release.py`) that unpacks the zip
  and verifies the packaged app like a fresh user would.
- Native window via WebView2 COM (`meetingnotes/native/`), no .NET
  runtime needed; the previous pythonnet-based window could fail on clean
  machines. Fallback chain: WebView2 → Edge app window → browser.

### Fixed
- **Model picked in the wizard was ignored.** Choosing `tiny` in Settings and
  never pressing Save meant jobs still ran the old `large-v3` default. Model,
  language, profile and output dropdowns now save immediately, and the staged
  card's "default" option spells out the model it will actually use.
- **WebView2 window rendered a blank white page.** The controller starts
  invisible and was never made visible, so the window opened but painted
  nothing. Fixed (`controller.IsVisible = True`) and guarded by a render
  check in the release verification: the packaged window's pixels are
  sampled and a blank page fails the gate.
- **Cloud LLM calls failed on clean machines** with
  `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`: Python
  trusted only the system store, which can be missing root CAs. All providers
  now use the bundled certifi CA list, and TLS failures get a plain-language
  message. The smoke gate fails the build if certifi is missing.
- **Dialogs appeared only after a stray click** on VMs: Windows native
  occlusion detection paused rendering in the WebView2 window, leaving stale
  frames. Occlusion detection is now disabled, and the modal backdrop no
  longer uses `backdrop-filter` (which stalls compositing with software
  rendering).
- **ffmpeg is bundled.** The packaged app shipped without ffmpeg/ffprobe,
  so a clean machine could not transcribe anything. Binaries now ship in
  the zip and resolve from there; PATH is only a dev fallback.
- **Sample button was unreachable in the wizard.** The "add a recording"
  step now spotlights the dropzone *and* the sample button together.
- **Wizard highlight drifted and steps were skipped.** Scroll is locked on
  target-less steps, highlights re-place on scroll, and the Start step targets
  the running card (with adaptive text) instead of a Start button that may be
  gone, and no orphaned tooltip is left in the bottom corner.
- Edge app-mode window now stops the server when its window closes
  (previously the app could linger as a background process).
- Closing the native window stops everything; no zombie process (verified).
- Language dropdown sorts by display name (Tibetan no longer sits between
  Bengali and Breton).
- "Transcription language" renamed to "Audio language" with an
  explanation: it steers the model, it isn't the output language.
- Guide's "where everything lives" slide removed; that information now
  lives in Help where it belongs.

### Changed
- README rewritten in plain language, with a before/after correction
  example; development instructions moved to CONTRIBUTING.md.
- Output dropdown shows a short description per option.

## [0.2.0] - 2026-09

### Added
- Headless CLI (`meetingnotes-cli`) with full app parity: run, profiles,
  model management, settings, serve: all with `--json` output and stable
  exit codes (0/1/2) for agents and pipelines.
- Second exe target: `meetingnotes-cli.exe` (console build).
- **None (raw transcripts only)** LLM provider: the app works with zero
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
- SSE endpoint made async: enough browser tabs used to starve the thread
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