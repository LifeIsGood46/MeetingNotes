"""Anthropic (Claude) provider — pure REST, no SDK needed (works in the frozen exe)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from ..net import ssl_context, ssl_error_hint
from .base import LLMProvider, LLMResponse, LLMError

API_BASE = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    provider_name = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, **kwargs):
        super().__init__(model, **kwargs)
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.api_key = key.strip() if key else None

    def _headers(self) -> dict:
        if not self.api_key:
            raise LLMError("Anthropic API key is not set. Open Settings and paste your key.")
        return {"Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": API_VERSION}

    def _request(self, path: str, payload: dict | None = None, timeout: int = 600) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=self._headers(),
                                     method="POST" if payload is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise LLMError(
                    "Invalid or expired Anthropic API key. "
                    "Open Settings, paste a fresh key, and press Save.",
                    http_code=e.code,
                ) from e
            if e.code == 429:
                raise LLMError("Anthropic rate limit hit — wait a bit and retry.",
                               http_code=e.code) from e
            if e.code == 404:
                raise LLMError(f"Anthropic model '{self.model}' not found.",
                               http_code=e.code) from e
            body = e.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"Anthropic request failed ({e.code}): {body}", http_code=e.code) from e
        except LLMError:
            raise
        except Exception as e:
            hint = ssl_error_hint(e)
            if hint:
                raise LLMError(f"Anthropic connection failed: {hint}") from e
            raise LLMError(f"Could not reach Anthropic: {e}. Check connection.") from e

    def list_models(self) -> list[str]:
        ids = [m.get("id", "") for m in self._request("/models", timeout=15).get("data", [])]
        return sorted({i for i in ids if i})

    def verify_credentials(self, timeout: int = 30) -> None:
        """GET /models doubles as an auth probe — no tokens burned."""
        self._request("/models", timeout=timeout)

    def complete(self, system: str, user: str, *, max_tokens: int = 8000) -> LLMResponse:
        data = self._request("/messages", {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        })
        try:
            text = "".join(b.get("text", "") for b in data.get("content", [])).strip()
            if not text:
                raise KeyError("empty content")
        except (KeyError, AttributeError) as e:
            raise LLMError(f"Unexpected Anthropic response shape: {str(data)[:300]}") from e
        return LLMResponse(text=text, model=self.model, provider=self.provider_name)

    def is_available(self) -> bool:
        return bool(self.api_key)
