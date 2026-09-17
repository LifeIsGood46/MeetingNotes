"""Persistent application settings (JSON)."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .config import APP_DIR

SETTINGS_PATH = APP_DIR / "settings.json"

_DEFAULTS = {
    "model_size": "large-v3",
    "device": "cuda",
    "compute_type": "float16",
    "language": "ru",
    "default_profile": "generic",
    "default_task": "raw",
    "output_dir": "",
    "onboarded": False,   # first-launch guide seen
    "llm": {
        "provider": "none",
        "model": "",
        "ollama_host": "http://localhost:11434",
        "ollama_api_key": "",
        "compat_base_url": "http://localhost:1234/v1",
        "compat_api_key": "",
        "anthropic_api_key": "",
        "openai_api_key": "",
        "openrouter_api_key": "",
    },
}


@dataclass
class LLMSettings:
    provider: str = "none"
    model: str = ""
    ollama_host: str = "http://localhost:11434"
    ollama_api_key: str = ""
    compat_base_url: str = "http://localhost:1234/v1"
    compat_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""


@dataclass
class AppSettings:
    model_size: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str = "ru"
    default_profile: str = "generic"
    default_task: str = "raw"
    output_dir: str = ""                  # blank = Downloads\meetingnotes
    onboarded: bool = False             # first-launch guide seen
    llm: LLMSettings = field(default_factory=LLMSettings)

    # -- public export (never exposes raw secrets) --------------------------
    def to_public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for keyname in ("ollama_api_key", "compat_api_key", "anthropic_api_key", "openai_api_key", "openrouter_api_key"):
            v = d["llm"][keyname]
            d["llm"][keyname] = ("*" * 8 + v[-4:]) if v else ""
        return d


def load_settings() -> AppSettings:
    if not SETTINGS_PATH.is_file():
        return AppSettings()
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AppSettings()

    def pick(d: dict, defaults: dict, cls):
        out = copy.copy(defaults)
        for k in defaults:
            if k in d:
                out[k] = d[k]
        return cls(**out)

    llm = pick(raw.get("llm", {}), _DEFAULTS["llm"], LLMSettings)
    scalar_keys = {k: v for k, v in _DEFAULTS.items() if k != "llm"}
    scalars = {k: raw.get(k, v) for k, v in scalar_keys.items()}
    if "onboarded" not in raw and SETTINGS_PATH.is_file():
        # Upgraded install: only returning users (with job history) skip the
        # first-launch guide. A bare settings file — e.g. migrated from a
        # legacy smoke test with no jobs — still gets the guide.
        try:
            jobs_data = json.loads((APP_DIR / "jobs.json").read_text(encoding="utf-8"))
            if isinstance(jobs_data, dict) and jobs_data.get("jobs"):
                scalars["onboarded"] = True
        except (OSError, ValueError):
            pass
    return AppSettings(llm=llm, **scalars)


def save_settings(settings: AppSettings) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(settings)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_settings(patch: dict[str, Any]) -> AppSettings:
    """Apply a partial update dict (from the UI) and persist."""
    settings = load_settings()
    llm_patch = patch.pop("llm", {}) or {}

    # Do not let a masked placeholder overwrite a real stored key.
    masked = lambda v: isinstance(v, str) and v.startswith("********")

    for k, v in llm_patch.items():
        if hasattr(settings.llm, k):
            if masked(v):
                continue
            # Strip stray whitespace on keys/hosts from copy-paste (silent 401s)
            if isinstance(v, str) and ("api_key" in k or k.endswith("host") or k.endswith("base_url")):
                v = v.strip()
            setattr(settings.llm, k, v)
    # Guard the scalar fields that drive the pipeline.
    if "language" in patch and isinstance(patch["language"], str):
        from .transcribe import LANGUAGE_NAMES  # local import: no cycle

        if patch["language"] not in LANGUAGE_NAMES:
            patch.pop("language")  # keep the previous valid value
    for k, v in patch.items():
        if hasattr(settings, k) and k != "llm":
            setattr(settings, k, v)
    save_settings(settings)
    return settings
