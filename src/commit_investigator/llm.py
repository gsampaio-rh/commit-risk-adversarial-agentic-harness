"""LLM provider interface and implementations.

Pluggable provider pattern: real OpenAI-compatible client or mock for testing.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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
    """Mock provider for testing without an API key.

    Produces deterministic responses based on the context provided.
    """

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
        """Generate a mock investigation response."""
        context = messages[-1].content if messages else ""
        has_diff = "diff" in context.lower() or "+++" in context

        risk_level = "MEDIUM" if has_diff else "LOW"
        confidence = 0.6 if has_diff else 0.3

        response_content = json.dumps({
            "risk_level": risk_level,
            "confidence": confidence,
            "reasoning": "Mock investigation: analyzed available context.",
            "findings": ["Mock finding based on available evidence"],
            "follow_up_needed": False,
            "localization": [],
            "recommendations": [],
        })

        token_estimate = len(context.split()) + len(response_content.split())

        return LLMResponse(
            content=response_content,
            tool_calls=[],
            tokens_used=token_estimate,
            estimated_cost=token_estimate * 0.000003,
            model=self.model_name,
            finish_reason="stop",
        )


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible LLM provider (works with OpenAI, Azure, local endpoints)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url.rstrip("/")
        self._model = model

        if not self._api_key:
            raise ValueError(
                "OpenAI API key required. Set OPENAI_API_KEY env var or pass api_key."
            )

    @property
    def model_name(self) -> str:
        return self._model

    def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Call OpenAI-compatible chat completions endpoint."""
        import httpx

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)

        tool_calls = []
        if choice["message"].get("tool_calls"):
            for tc in choice["message"]["tool_calls"]:
                tool_calls.append({
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "arguments": json.loads(tc["function"]["arguments"]),
                })

        cost_per_token = 0.00000015 if "mini" in self._model else 0.000003
        estimated_cost = total_tokens * cost_per_token

        return LLMResponse(
            content=choice["message"].get("content", ""),
            tool_calls=tool_calls,
            tokens_used=total_tokens,
            estimated_cost=estimated_cost,
            model=self._model,
            finish_reason=choice.get("finish_reason", "stop"),
        )


def get_provider(prefer_real: bool = True) -> LLMProvider:
    """Factory: return real provider if API key available, otherwise mock."""
    if prefer_real and os.environ.get("OPENAI_API_KEY"):
        return OpenAIProvider()
    return MockLLMProvider()
