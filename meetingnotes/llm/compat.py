"""OpenAI-compatible provider for locally hosted servers.

Covers LM Studio (default http://localhost:1234/v1), vLLM, LocalAI,
llama.cpp server. Works without a key for local servers; pass a key
for hosted/protected ones.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from ..net import ssl_context, ssl_error_hint
from .base import LLMProvider, LLMResponse, LLMError


class OpenAICompatProvider(LLMProvider):
    provider_name = "compat"

    def __init__(self, model: str, base_url: str | None = None, api_key: str | None = None, **kwargs):
        super().__init__(model, **kwargs)
        url = base_url or os.environ.get("COMPAT_BASE_URL") or "http://localhost:1234/v1"
        self.base_url = url.strip().rstrip("/")
        key = api_key or os.environ.get("COMPAT_API_KEY")
        self.api_key = key.strip() if key else None

    def _request(self, path: str, payload: dict | None = None, timeout: int = 600) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers,
                                     method="POST" if payload is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise LLMError(
                    "Invalid API key for the OpenAI-compatible server. "
                    "Check the key in Settings (leave it blank for local servers).",
                    http_code=e.code,
                ) from e
            if e.code == 404 and payload is not None:
                raise LLMError(
                    f"Model '{self.model or 'auto'}' not found on {self.base_url}. "
                    "Pick a model from the dropdown in Settings."
                ) from e
            body = e.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"Request failed ({e.code}): {body}") from e
        except Exception as e:
            hint = ssl_error_hint(e)
            if hint:
                raise LLMError(f"Connection to {self.base_url} failed: {hint}") from e
            raise LLMError(
                f"Could not reach {self.base_url} — is LM Studio (or your server) running?"
            ) from e

    def list_models(self) -> list[str]:
        return sorted({m.get("id", "") for m in self._request("/models", timeout=10).get("data", [])} - {""})

    def verify_credentials(self, timeout: int = 8) -> None:
        """Cheap /models fetch validates reachability + key."""
        self.list_models()

    def _resolve_model(self) -> str:
        """Empty model name -> first model the server offers (LM Studio zero-config)."""
        if self.model:
            return self.model
        models = self.list_models()
        if not models:
            raise LLMError(
                f"No models are served at {self.base_url}. "
                "Load a model in LM Studio (or your server) and refresh."
            )
        return models[0]

    def complete(self, system: str, user: str, *, max_tokens: int = 8000) -> LLMResponse:
        model = self._resolve_model()
        data = self._request("/chat/completions", {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
        })
        try:
            text = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected response shape: {str(data)[:300]}") from e
        return LLMResponse(text=text, model=model, provider=self.provider_name)

    def is_available(self) -> bool:
        try:
            self.list_models()
            return True
        except LLMError:
            return False