"""meetingnotes CLI — full-featured headless interface.

Parity with the web app: run, profiles/tasks, whisper model management,
settings get/set, and the web UI. Every command supports ``--json`` for
machine-readable output (agents / pipelines), and exit codes are stable:

    0  success
    1  usage / input error
    2  processing failure (transcription, LLM, network...)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, config
from .pipeline import PipelineOptions, run_pipeline
from .polish import available_tasks, task_default_prompts
from .profiles import list_profiles
from .settings import load_settings, update_settings


def _print(msg: str) -> None:
    print(msg, flush=True)


def _emit(args: argparse.Namespace, payload: dict) -> int:
    """Print a JSON payload (--json) or a human summary. Agents use --json."""
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _known_models() -> list[str]:
    from . import models as model_mgr

    return model_mgr.KNOWN_MODELS


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    source = Path(args.input)
    if not source.is_file():
        print(f"error: input not found: {source}", file=sys.stderr)
        return 1

    # Settings provide defaults (same source of truth as the UI); flags override.
    settings = load_settings()
    opts = PipelineOptions(
        profile=args.profile or settings.default_profile,
        task=args.task or settings.default_task,
        custom_prompt=args.prompt or "",
        llm_provider=args.llm,
        llm_model=args.llm_model,
        model_size=args.model or settings.model_size,
        device=args.device or settings.device,
        compute_type=args.compute_type or settings.compute_type,
        language=args.language or settings.language,
        normalize_audio=not args.no_normalize,
        chunk_length_s=args.chunk_length or 600,
    )

    # LLM settings default to the persisted provider unless --llm overrides.
    if opts.llm_provider is None and opts.task != "raw":
        opts.llm_provider = settings.llm.provider

    out_dir = Path(args.out_dir or _resolve_out(settings))
    work_dir = Path(args.work_dir or config.WORK_DIR) / source.stem

    def progress(msg: str) -> None:
        if args.json:
            return  # keep stdout machine-pure; progress goes nowhere (stderr would interleave)
        _print(msg)

    try:
        result = run_pipeline(source, work_dir, out_dir, opts, progress_cb=progress)
    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"error: {e}", file=sys.stderr)
        return 2

    payload = {
        "source": str(source),
        "profile": result.profile,
        "task": opts.task,
        "elapsed_s": round(result.elapsed_s, 1),
        "outputs": {k: str(v) for k, v in result.outputs.items()},
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("\nOutputs:")
        for name, path in result.outputs.items():
            print(f"  {name:20} {path}")
    return 0


def _resolve_out(settings) -> Path:
    custom = (settings.output_dir or "").strip()
    return Path(custom) if custom else config.OUT_DIR


# ---------------------------------------------------------------------------
# profiles / tasks
# ---------------------------------------------------------------------------

def cmd_profiles(args: argparse.Namespace) -> int:
    profiles = [
        {"name": p.name, "description": p.description, "language": p.language,
         "corrections": len(p.corrections)}
        for p in list_profiles()
    ]
    tasks = ["raw", *available_tasks(), "custom"]
    prompts = task_default_prompts()
    if args.json:
        return _emit(args, {"profiles": profiles, "tasks": tasks, "task_prompts": prompts})
    for p in profiles:
        print(f"{p['name']:12}  {p['description']}  [corrections: {p['corrections']}]")
    print(f"\nTasks: {', '.join(tasks)}")
    print("Prompt presets:")
    for t, prompt in prompts.items():
        print(f"  {t:10} {prompt}")
    return 0


# ---------------------------------------------------------------------------
# whisper models
# ---------------------------------------------------------------------------

def cmd_models(args: argparse.Namespace) -> int:
    from . import models as model_mgr

    if args.action == "list":
        data = {"models": model_mgr.list_models()}
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            for m in data["models"]:
                state = "installed" if m["installed"] else ("downloading" if m["downloading"] else "not installed")
                size = f" {m['size_mb']} MB" if m["size_mb"] else ""
                mem = " [in memory]" if m["loaded"] else ""
                print(f"{m['size']:10} {state}{size}{mem}")
        return 0

    size = args.size
    if size not in model_mgr.KNOWN_MODELS:
        print(f"error: unknown model size '{size}'. Known: {', '.join(model_mgr.KNOWN_MODELS)}", file=sys.stderr)
        return 1

    if args.action == "download":
        dl = model_mgr.download_model(size)
        if not args.json:
            _print(f"downloading {size}...")
        dl._thread.join(timeout=None)  # type: ignore[attr-defined]
        payload = model_mgr.model_status(size)
        return _emit(args, payload) or (0 if payload.get("installed") else 2)

    if args.action == "delete":
        ok = model_mgr.delete_model(size)
        payload = {"size": size, "deleted": ok}
        return _emit(args, payload) or (0 if ok else 2)

    if args.action == "open-folder":
        folder = model_mgr.hub_cache_dir()
        if not folder.is_dir():
            print(f"error: model cache folder does not exist: {folder}", file=sys.stderr)
            return 2
        from .webapp.main import _open_in_explorer

        _open_in_explorer(folder)
        return _emit(args, {"folder": str(folder)})

    if args.action == "status":
        return _emit(args, model_mgr.model_status(size))

    return 1  # unreachable; argparse restricts choices


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

def cmd_settings(args: argparse.Namespace) -> int:
    if args.action == "get":
        public = load_settings().to_public_dict()
        if args.key:
            # dotted path: llm.model, language, ...
            node: dict | object = public
            for part in args.key.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    print(f"error: unknown settings key '{args.key}'", file=sys.stderr)
                    return 1
            return _emit(args, {"key": args.key, "value": node})
        return _emit(args, public)

    if args.action == "set":
        payload: dict = {}
        pairs = ([args.key] if args.key else []) + list(args.set_args or [])
        for pair in pairs:
            if "=" not in pair:
                print(f"error: expected KEY=VALUE, got '{pair}'", file=sys.stderr)
                return 1
            k, _, v = pair.partition("=")
            # typed conversion
            if v.lower() in ("true", "false"):
                payload[k] = v.lower() == "true"
            elif v.isdigit():
                payload[k] = int(v)
            else:
                payload[k] = v
        if not payload:
            print("error: nothing to set — pass KEY=VALUE pairs", file=sys.stderr)
            return 1
        updated = update_settings(payload)
        return _emit(args, updated.to_public_dict())

    if args.action == "reset":
        from .settings import AppSettings, save_settings

        save_settings(AppSettings())
        return _emit(args, load_settings().to_public_dict())

    return 1


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    from meetingnotes.webapp.main import create_app

    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning",
                log_config=None)  # log_config=None: windowed exe has no stdout
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meetingnotes",
        description="Meeting recordings -> transcripts -> structured documents. "
                    "Add --json to any command for machine-readable output.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Transcribe a file (and optionally LLM-format it)")
    p_run.add_argument("input", help="Audio or video file")
    p_run.add_argument("--profile", help="Domain profile (default from settings)")
    p_run.add_argument("--task", choices=["raw", *available_tasks(), "custom"],
                       help="raw = no LLM; default from settings")
    p_run.add_argument("--prompt", help="Custom LLM prompt (implies a formatted output)")
    p_run.add_argument("--llm", choices=["none", "ollama", "compat", "anthropic", "openai", "openrouter"],
                       help="LLM provider (default from settings)")
    p_run.add_argument("--llm-model", help="LLM model name")
    p_run.add_argument("--model", help=f"Whisper size: {', '.join(_known_models())}")
    p_run.add_argument("--device", help="cuda | cpu")
    p_run.add_argument("--compute-type", help="float16 | int8 | int8_float16 | float32")
    p_run.add_argument("--language", help="Language code, e.g. ru, en (default from settings)")
    p_run.add_argument("--no-normalize", action="store_true", help="Skip audio normalization")
    p_run.add_argument("--chunk-length", type=int, help="Chunk length in seconds")
    p_run.add_argument("--out-dir", help="Where to save results (default: Downloads\\meetingnotes)")
    p_run.add_argument("--work-dir", help="Working dir for intermediate audio")
    p_run.add_argument("--json", action="store_true", help="Machine-readable output")
    p_run.set_defaults(func=cmd_run)

    # profiles
    p_prof = sub.add_parser("profiles", help="List profiles, tasks and prompt presets")
    p_prof.add_argument("--json", action="store_true")
    p_prof.set_defaults(func=cmd_profiles)

    # models
    p_mod = sub.add_parser("models", help="Manage whisper models")
    p_mod.add_argument("action", choices=["list", "download", "delete", "status", "open-folder"])
    p_mod.add_argument("size", nargs="?", help=f"Model size: {', '.join(_known_models())}")
    p_mod.add_argument("--json", action="store_true")
    p_mod.set_defaults(func=cmd_models)

    # settings
    p_set = sub.add_parser("settings", help="Get or set app settings")
    p_set.add_argument("action", choices=["get", "set", "reset"])
    p_set.add_argument("key", nargs="?", help="Dotted key for 'get' (e.g. llm.model)")
    p_set.add_argument("set_args", nargs="*", help="KEY=VALUE pairs for 'set'")
    p_set.add_argument("--json", action="store_true")
    p_set.set_defaults(func=cmd_settings)

    # serve
    p_serve = sub.add_parser("serve", help="Run the web UI (FastAPI)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8123)
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())