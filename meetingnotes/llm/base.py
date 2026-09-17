"""Abstract LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class LLMError(RuntimeError):
    """Raised when a provider call fails or is misconfigured."""

    def __init__(self, message: str = "", http_code: int | None = None):
        super().__init__(message)
        self.http_code = http_code  # lets callers distinguish 401/403 from 429 etc.


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str


class LLMProvider(ABC):
    """Minimal interface: one system prompt + one user message -> text."""

    provider_name: str = "base"

    def __init__(self, model: str, **kwargs):
        self.model = model
        self.options = kwargs

    @abstractmethod
    def complete(self, system: str, user: str, *, max_tokens: int = 8000) -> LLMResponse:
        """Return the model's completion for the given prompt pair."""
        raise NotImplementedError

    def verify_credentials(self, timeout: int = 30) -> None:
        """Best-effort credential precheck; raise LLMError if clearly invalid."""
        return None

    def is_available(self) -> bool:
        """Best-effort availability check (credentials, server running)."""
        return True
