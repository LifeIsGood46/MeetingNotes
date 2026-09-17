"""OpenAI provider."""

from __future__ import annotations

import os

from .base import LLMProvider, LLMResponse, LLMError


class OpenAIProvider(LLMProvider):
    provider_name = "openai"

    def __init__(self, model: str, api_key: str | None = None, **kwargs):
        super().__init__(model, **kwargs)
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")

    def complete(self, system: str, user: str, *, max_tokens: int = 8000) -> LLMResponse:
        if not self.api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
        except ImportError as e:
            raise LLMError("pip install openai  (or install meetingnotes[openai])") from e

        client = OpenAI(api_key=self.api_key)
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
            )
        except Exception as e:
            raise LLMError(f"OpenAI request failed: {e}") from e

        text = (resp.choices[0].message.content or "").strip()
        return LLMResponse(text=text, model=self.model, provider=self.provider_name)

    def is_available(self) -> bool:
        return bool(self.api_key)
