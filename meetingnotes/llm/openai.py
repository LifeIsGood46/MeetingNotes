"""OpenAI provider — pure REST, no SDK needed (works in the frozen exe)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import LLMProvider, LLMResponse, LLMError

API_BASE = "https://api.openai.com/v1"

# Prefixes of chat-capable models; anything else (embeddings, TTS, image…)
# is hidden from the picker (Custom... still allows them).
_CHAT_PREFIXES = ("gpt", "o1", "o3", "o4", "chatgpt")


class OpenAIProvider(LLMProvider):
    provider_name = "openai"

    def __init__(self, model: str, api_key: str | None = None, **kwargs):
        super().__init__(model, **kwargs)
        key = api_key or os.environ.get("OPENAI_API_KEY")
        self.api_key = key.strip() if key else None

    def _request(self, path: str, payload: dict | None = None, timeout: int = 600) -> dict:
        if not self.api_key:
            raise LLMError("OpenAI API key is not set. Open Settings and paste your key.")
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self.api_key}"}
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=headers,
                                     method="POST" if payload is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise LLMError(
                    "Invalid or expired OpenAI API key. "
                    "Open Settings, paste a fresh key, and press Save.",
                    http_code=e.code,
                ) from e
            if e.code == 429:
                raise LLMError("OpenAI rate limit hit — wait a bit and retry.",
                               http_code=e.code) from e
            if e.code == 404:
                raise LLMError(f"OpenAI model '{self.model}' not found for your key.",
                               http_code=e.code) from e
            body = e.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"OpenAI request failed ({e.code}): {body}", http_code=e.code) from e
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"Could not reach OpenAI: {e}. Check connection.") from e

    def list_models(self) -> list[str]:
        ids = [m.get("id", "") for m in self._request("/models", timeout=15).get("data", [])]
        ids = sorted({i for i in ids if i})
        chat = [i for i in ids if i.startswith(_CHAT_PREFIXES)]
        return chat or ids

    def verify_credentials(self, timeout: int = 30) -> None:
        """GET /models doubles as an auth probe — no tokens burned."""
        self._request("/models", timeout=timeout)

    def complete(self, system: str, user: str, *, max_tokens: int = 8000) -> LLMResponse:
        data = self._request("/chat/completions", {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens,
        })
        try:
            text = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected OpenAI response shape: {str(data)[:300]}") from e
        return LLMResponse(text=text, model=self.model, provider=self.provider_name)

    def is_available(self) -> bool:
        return bool(self.api_key)
