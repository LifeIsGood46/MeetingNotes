"""OpenRouter provider (OpenAI-compatible API, incl. :free models)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import LLMProvider, LLMResponse, LLMError

API_BASE = "https://openrouter.ai/api/v1"


class OpenRouterProvider(LLMProvider):
    provider_name = "openrouter"

    def __init__(self, model: str, api_key: str | None = None, **kwargs):
        super().__init__(model, **kwargs)
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.api_key = key.strip() if key else None

    def _request(self, path: str, payload: dict | None = None, timeout: int = 600) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/meetingnotes",
            "X-Title": "meetingnotes",
        }
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=headers,
                                     method="POST" if payload is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise LLMError(
                    "Invalid or expired OpenRouter API key. "
                    "Open Settings, paste a fresh key, and press Save.",
                    http_code=e.code,
                ) from e
            if e.code == 402:
                raise LLMError(
                    "OpenRouter: insufficient credits. Pick a :free model or top up your key.",
                    http_code=e.code,
                ) from e
            if e.code == 429:
                raise LLMError(
                    "OpenRouter rate limit hit — wait a bit and retry, use a :free model, "
                    "or switch Provider to Ollama with a local model (no quota).",
                    http_code=e.code,
                ) from e
            body = e.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"OpenRouter request failed ({e.code}): {body}", http_code=e.code) from e
        except Exception as e:
            raise LLMError(f"Could not reach OpenRouter: {e}.") from e

    def list_models(self, *, free_only: bool = False) -> list[str]:
        ids = [m.get("id", "") for m in self._request("/models", timeout=15).get("data", [])]
        ids = [i for i in ids if i]
        if free_only:
            ids = [i for i in ids if i.endswith(":free")]
        return sorted(set(ids))

    def verify_credentials(self, timeout: int = 30) -> None:
        """1-token chat probe: fail fast on bad keys before transcription runs."""
        if not self.api_key:
            raise LLMError("OpenRouter API key is not set. Open Settings and paste your key.")
        self._request("/chat/completions",
                      {"model": self.model, "messages": [{"role": "user", "content": "hi"}],
                       "max_tokens": 1}, timeout=timeout)

    def complete(self, system: str, user: str, *, max_tokens: int = 8000) -> LLMResponse:
        if not self.api_key:
            raise LLMError("OpenRouter API key is not set. Open Settings and paste your key.")
        data = self._request("/chat/completions", {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
        })
        try:
            text = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected OpenRouter response shape: {str(data)[:300]}") from e
        return LLMResponse(text=text, model=self.model, provider=self.provider_name)

    def is_available(self) -> bool:
        return bool(self.api_key)