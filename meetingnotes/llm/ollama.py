"""Ollama provider — local daemon (native API) or Ollama cloud (OpenAI-compatible)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from ..net import ssl_context, ssl_error_hint
from .base import LLMProvider, LLMResponse, LLMError


class OllamaProvider(LLMProvider):
    provider_name = "ollama"

    def __init__(self, model: str, host: str | None = None, api_key: str | None = None, **kwargs):
        super().__init__(model, **kwargs)
        host = host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
        self.host = host.strip().rstrip("/")
        key = api_key or os.environ.get("OLLAMA_API_KEY")
        self.api_key = key.strip() if key else None

    @property
    def _is_cloud(self) -> bool:
        return bool(self.api_key)

    def _request(self, path: str, payload: dict | None = None, timeout: int = 600) -> dict:
        """POST/GET helper with bearer auth when a key is set; maps HTTP errors."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(f"{self.host}{path}", data=data, headers=headers,
                                     method="POST" if payload is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise LLMError(
                    "Invalid or expired Ollama API key. "
                    "Open Settings, paste a fresh key, and press Save.",
                    http_code=e.code,
                ) from e
            if e.code == 429:
                raise LLMError(
                    "Ollama cloud rate limit hit — wait a bit and retry, or use a "
                    "local model (no quota).",
                    http_code=e.code,
                ) from e
            body = e.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"Ollama request failed ({e.code}): {body}", http_code=e.code) from e
        except Exception as e:
            hint = ssl_error_hint(e)
            if hint:
                raise LLMError(f"Ollama connection failed: {hint}") from e
            hint = "Is `ollama serve` running?" if not self._is_cloud else "Check host/key."
            raise LLMError(f"Ollama request failed ({e}). {hint}") from e

    def complete(self, system: str, user: str, *, max_tokens: int = 8000) -> LLMResponse:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if self._is_cloud:
            data = self._request("/v1/chat/completions",
                                 {"model": self.model, "messages": messages, "max_tokens": max_tokens})
            try:
                text = data["choices"][0]["message"]["content"].strip()
            except (KeyError, IndexError) as e:
                raise LLMError(f"Unexpected Ollama response shape: {str(data)[:300]}") from e
        else:
            data = self._request("/api/chat", {
                "model": self.model, "messages": messages, "stream": False,
                "options": {"num_predict": max_tokens},
            })
            text = (data.get("message") or {}).get("content", "").strip()
        return LLMResponse(text=text, model=self.model, provider=self.provider_name)

    def list_models(self) -> list[str]:
        data = self._request("/api/tags", timeout=15)
        return sorted({m.get("name") or m.get("model") or "" for m in data.get("models", [])} - {""})

    def verify_credentials(self, timeout: int = 30) -> None:
        """1-token chat probe; /api/tags is public so it can't detect bad keys."""
        if not self._is_cloud:
            return
        self._request("/v1/chat/completions",
                      {"model": self.model, "messages": [{"role": "user", "content": "hi"}],
                       "max_tokens": 1}, timeout=timeout)

    def is_available(self) -> bool:
        if self._is_cloud:
            return True
        try:
            self._request("/api/tags", timeout=3)
            return True
        except LLMError:
            return False