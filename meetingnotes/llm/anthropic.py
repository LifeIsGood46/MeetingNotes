"""Anthropic (Claude) provider."""

from __future__ import annotations

import os

from .base import LLMProvider, LLMResponse, LLMError


class AnthropicProvider(LLMProvider):
    provider_name = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, **kwargs):
        super().__init__(model, **kwargs)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    def complete(self, system: str, user: str, *, max_tokens: int = 8000) -> LLMResponse:
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic
        except ImportError as e:
            raise LLMError("pip install anthropic  (or install meetingnotes[anthropic])") from e

        client = anthropic.Anthropic(api_key=self.api_key)
        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as e:
            raise LLMError(f"Anthropic request failed: {e}") from e

        text = "".join(block.text for block in resp.content if hasattr(block, "text")).strip()
        return LLMResponse(text=text, model=self.model, provider=self.provider_name)

    def is_available(self) -> bool:
        return bool(self.api_key)
