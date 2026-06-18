"""LLM provider interface and core data models.

Concrete implementations live in llm_providers.py.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Response from an LLM reasoning step."""

    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0
    estimated_cost: float = 0.0
    model: str = ""
    finish_reason: str = "stop"


@dataclass
class LLMMessage:
    """A message in the conversation history."""

    role: str  # "system", "user", "assistant", "tool"
    content: str
    tool_call_id: str | None = None
    name: str | None = None


class LLMProvider(ABC):
    """Abstract base for LLM providers."""

    @abstractmethod
    def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate a completion from the LLM."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identifier for the model being used."""
        ...


class MockLLMProvider(LLMProvider):
    """Mock provider for testing without an API key."""

    @property
    def model_name(self) -> str:
        return "mock-investigator-v1"

    def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        context = messages[-1].content if messages else ""
        token_estimate = len(context.split()) + 50
        return LLMResponse(
            content="No investigation performed (mock provider).",
            tool_calls=[],
            tokens_used=token_estimate,
            estimated_cost=token_estimate * 0.000003,
            model=self.model_name,
            finish_reason="stop",
        )


# Re-exports for backward compatibility — callers import from this module
from commit_investigator.infra.llm_providers import (  # noqa: E402, F401
    CursorSDKProvider,
    OpenAIProvider,
    ProviderUnavailableError,
    get_provider,
    try_local_ollama_provider,
)
