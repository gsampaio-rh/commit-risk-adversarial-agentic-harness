"""Concrete LLM provider implementations and factory.

CursorSDKProvider, OpenAIProvider, local Ollama detection, and get_provider factory.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from commit_investigator.infra.llm import LLMMessage, LLMProvider, LLMResponse, MockLLMProvider

logger = logging.getLogger(__name__)


class CursorSDKProvider(LLMProvider):
    """LLM provider using the Cursor SDK (cursor-sdk).

    Uses Agent.prompt() per call with an isolated cwd to prevent the
    agent from using its own tools.
    """

    _NO_TOOLS_NOTICE = (
        "\n\n---\n"
        "IMPORTANT: Respond with text ONLY using the exact formats described above "
        "(```tool and ```suspects blocks). Do NOT read files, run commands, or use "
        "any built-in tools. Reason solely from the information provided.\n"
        "---"
    )

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self._api_key = api_key or os.environ.get("CURSOR_API_KEY", "")
        self._model = model
        self._cwd = self._get_isolated_cwd()

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
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
        from cursor_sdk.errors import CursorAgentError

        prompt_text = self._format_messages(messages)
        word_count = len(prompt_text.split())

        try:
            result = Agent.prompt(
                prompt_text,
                AgentOptions(
                    api_key=self._api_key,
                    model=self._model,
                    local=LocalAgentOptions(cwd=self._cwd),
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
        """Flatten chat messages into a single prompt for Agent.prompt()."""
        parts: list[str] = []
        for msg in messages:
            if msg.role == "system":
                parts.append(msg.content)
            elif msg.role == "user":
                parts.append(msg.content)
            elif msg.role == "assistant":
                parts.append(f"--- Previous analysis ---\n{msg.content}\n--- End ---")
            elif msg.role == "tool":
                tool_label = msg.name or "tool"
                parts.append(f"[Result from {tool_label}]\n{msg.content}")
        text = "\n\n".join(parts)
        text += CursorSDKProvider._NO_TOOLS_NOTICE
        return text

    @staticmethod
    def _get_isolated_cwd() -> str:
        """Return a minimal isolated directory so the agent has nothing to explore."""
        import tempfile
        return tempfile.mkdtemp(prefix="cursor_sdk_sandbox_")


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
        self._is_ollama = api_key == "ollama" or "11434" in base_url

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

        payload = self._build_payload(messages, tools, temperature, max_tokens)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=300.0) as client:
            response = client.post(
                f"{self._base_url}/chat/completions", headers=headers, json=payload,
            )
            response.raise_for_status()
            data = response.json()

        return self._parse_response(data)

    def _build_payload(
        self, messages: list[LLMMessage], tools: list[dict[str, Any]] | None,
        temperature: float, max_tokens: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self._is_ollama:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        choice = data["choices"][0]
        total_tokens = data.get("usage", {}).get("total_tokens", 0)

        tool_calls = []
        if choice["message"].get("tool_calls"):
            for tc in choice["message"]["tool_calls"]:
                tool_calls.append({
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "arguments": json.loads(tc["function"]["arguments"]),
                })

        cost_per_token = 0.00000015 if "mini" in self._model else 0.000003
        return LLMResponse(
            content=choice["message"].get("content", ""),
            tool_calls=tool_calls,
            tokens_used=total_tokens,
            estimated_cost=total_tokens * cost_per_token,
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


class ProviderUnavailableError(RuntimeError):
    """Raised in fail-fast mode when no real provider is available."""


def get_provider(
    prefer_real: bool = True,
    *,
    phase: str | None = None,
    fail_fast: bool | None = None,
) -> LLMProvider:
    """Factory: return real provider if available, otherwise mock.

    Priority: CURSOR_API_KEY -> OPENAI_API_KEY (+ OPENAI_BASE_URL) -> local Ollama -> Mock.
    """
    strict = fail_fast
    if strict is None:
        strict = os.environ.get("EVAL_STRICT", "").lower() in ("1", "true", "yes")

    if phase == "investigation":
        inv_model = os.environ.get("INVESTIGATION_MODEL")
        if inv_model:
            base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
            try:
                return OpenAIProvider(api_key="ollama", base_url=base_url, model=inv_model)
            except Exception:
                logger.warning("INVESTIGATION_MODEL=%s but Ollama provider failed", inv_model)

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

    if strict:
        raise ProviderUnavailableError(
            "No real LLM provider available and EVAL_STRICT=1. "
            "Set CURSOR_API_KEY, OPENAI_API_KEY, or start Ollama."
        )

    return MockLLMProvider()
