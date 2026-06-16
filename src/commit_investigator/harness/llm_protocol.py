"""LLM provider protocol for the investigation harness.

Defines the interface that any LLM backend must implement to be used
by InvestigationHarness. Enables mock-based testing without real LLM calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMResponse:
    """Structured response from an LLM call."""

    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0
    cost: float = 0.0
    model: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMProvider(Protocol):
    """Protocol for LLM backends used by InvestigationHarness."""

    def generate(self, prompt: str, **kwargs: Any) -> LLMResponse:
        """Generate a response from the given prompt."""
        ...
