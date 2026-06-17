"""LLM provider interface and implementations.

Pluggable provider pattern: Cursor SDK, OpenAI-compatible, or mock for testing.
"""

from __future__ import annotations

import json
import logging
import os
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
    """Mock provider for testing without an API key.

    Returns empty responses (no tool calls, no suspects).
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


class CursorSDKProvider(LLMProvider):
    """LLM provider using the Cursor SDK (cursor-sdk).

    complete() — one-shot via Agent.prompt(). Use for single-turn calls.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self._api_key = api_key or os.environ.get("CURSOR_API_KEY", "")
        self._model = model

        if not self._api_key:
            raise ValueError(
                "Cursor API key required. Set CURSOR_API_KEY env var or pass api_key."
            )

    @property
    def model_name(self) -> str:
        return f"cursor-sdk/{self._model}"

    def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a one-shot prompt via Cursor SDK and return the result."""
        from cursor_sdk import Agent, AgentOptions
        from cursor_sdk.errors import CursorAgentError

        prompt_text = self._format_messages(messages)
        word_count = len(prompt_text.split())

        try:
            result = Agent.prompt(
                prompt_text,
                AgentOptions(
                    api_key=self._api_key,
                    model=self._model,
                    mode="plan",
                ),
            )
        except CursorAgentError as exc:
            logger.error("Cursor SDK call failed: %s (retryable=%s)", exc.message, exc.is_retryable)
            return LLMResponse(
                content=f"Investigation failed: {exc.message}",
                model=self.model_name,
                finish_reason="error",
            )

        if result.status != "finished":
            logger.warning("Cursor SDK run status: %s (run %s)", result.status, result.id)

        response_text = result.result or ""
        response_word_count = len(response_text.split())
        token_estimate = word_count + response_word_count
        cost_estimate = token_estimate * 0.000003

        actual_model = self._model
        if result.model:
            model_id = getattr(result.model, "id", None)
            if model_id:
                actual_model = str(model_id)

        return LLMResponse(
            content=response_text,
            tool_calls=[],
            tokens_used=token_estimate,
            estimated_cost=cost_estimate,
            model=f"cursor-sdk/{actual_model}",
            finish_reason="stop" if result.status == "finished" else str(result.status),
        )

    @staticmethod
    def _format_messages(messages: list[LLMMessage]) -> str:
        """Flatten chat messages into a single prompt string for Agent.prompt()."""
        parts: list[str] = []
        for msg in messages:
            if msg.role == "system":
                parts.append(f"[System Instructions]\n{msg.content}\n")
            elif msg.role == "user":
                parts.append(f"{msg.content}\n")
            elif msg.role == "assistant":
                parts.append(f"[Previous Response]\n{msg.content}\n")
            elif msg.role == "tool":
                parts.append(f"[Tool Result: {msg.name}]\n{msg.content}\n")
        return "\n".join(parts)


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

        with httpx.Client(timeout=300.0) as client:
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


def try_local_ollama_provider() -> LLMProvider | None:
    """Return an OpenAI-compatible provider backed by local Ollama if reachable."""
    if os.environ.get("DISABLE_LOCAL_LLM", "").lower() in ("1", "true", "yes"):
        return None

    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    health_url = base_url.replace("/v1", "").rstrip("/") + "/api/tags"
    try:
        import httpx

        response = httpx.get(health_url, timeout=2.0)
        if response.status_code != 200 or not response.json().get("models"):
            return None
    except Exception:
        return None

    model = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
    return OpenAIProvider(api_key="ollama", base_url=base_url, model=model)


def get_provider(prefer_real: bool = True) -> LLMProvider:
    """Factory: return real provider if available, otherwise mock.

    Priority: CURSOR_API_KEY → OPENAI_API_KEY (+ OPENAI_BASE_URL) → local Ollama → Mock.
    """
    if prefer_real:
        cursor_key = os.environ.get("CURSOR_API_KEY")
        if cursor_key:
            return CursorSDKProvider(api_key=cursor_key)

        openai_key = os.environ.get("OPENAI_API_KEY")
        if openai_key:
            base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            return OpenAIProvider(api_key=openai_key, base_url=base_url, model=model)

        local = try_local_ollama_provider()
        if local is not None:
            return local

    return MockLLMProvider()
