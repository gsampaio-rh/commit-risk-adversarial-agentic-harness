"""Bug attribution agent orchestrator (V3).

Multi-turn agentic loop that searches a git repository to find the
commit that introduced a reported bug. The agent uses git tools
(search, blame, diff, show) within temporal bounds.

Phases: Understand → Search → Examine → Refine → Conclude.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from commit_investigator.analysis.evidence_tagger import (
    SuspectEvidenceScore,
    score_suspect_evidence,
)
from commit_investigator.context.git_context import GitContextProvider, TemporalBoundViolation
from commit_investigator.context.problem_extractor import ProblemStatement
from commit_investigator.infra.llm import LLMMessage, LLMProvider, LLMResponse, get_provider
from commit_investigator.pipeline.tools import ToolRegistry, build_attribution_tools

logger = logging.getLogger(__name__)

__all__ = [
    "AgentOrchestrator",
    "BudgetState",
    "BugAttributionReport",
    "SuspectCommit",
]

_SYSTEM_PROMPT_TEMPLATE = (
    "You are a bug attribution agent. Given a bug report, your task is to "
    "search a git repository to find the commit that most likely INTRODUCED the bug.\n\n"
    "## Available Tools\n\n"
    "You have these git tools. To use a tool, output a JSON block:\n"
    "```tool\n"
    '{{"tool": "<name>", "args": {{<arguments>}}}}\n'
    "```\n\n"
    "{tool_descriptions}\n\n"
    "## Strategy\n\n"
    "1. **Understand**: Read the bug report carefully. Identify file names, class names, "
    "error messages, component names.\n"
    "2. **Search**: Use search tools to find commits touching relevant files or mentioning "
    "relevant keywords.\n"
    "3. **Examine**: Read diffs of candidate commits. Look for changes that could have "
    "introduced the bug.\n"
    "4. **Refine**: Narrow down suspects. Follow blame chains. Compare adjacent commits.\n"
    "5. **Conclude**: When you have enough evidence, output your final answer.\n\n"
    "## Output Format\n\n"
    "When ready to conclude, output:\n"
    "```suspects\n"
    "[\n"
    '  {{"commit_id": "<full SHA>", "confidence": 0.8, '
    '"mechanism": "If <change> then <consequence>", '
    '"evidence_quotes": ["<exact text from diff>"]}}\n'
    "]\n"
    "```\n\n"
    "## Rules\n"
    "- Search broadly first, then narrow down.\n"
    "- Always examine the diff of a suspect before ranking it.\n"
    "- Provide specific evidence quotes from actual diffs.\n"
    "- You cannot see commits after the temporal boundary.\n"
    "- If a tool returns an error about temporal bounds, that commit is inaccessible.\n"
)

TURN_PROMPT = """Continue your investigation. You have used {tool_calls_used}/{max_tool_calls} tool calls and {tokens_used:,} tokens.

Tool results from your last request:
{tool_results}

What would you like to do next? Use more tools to investigate, or conclude with your suspects list."""


@dataclass
class BudgetState:
    """Tracks token usage and cost across turns."""

    total_tokens: int = 0
    total_cost: float = 0.0
    total_tool_calls: int = 0
    max_tokens: int = 100_000
    max_cost: float = 0.50
    max_tool_calls: int = 30
    turns_used: int = 0

    @property
    def budget_exceeded(self) -> bool:
        return (
            self.total_tokens >= self.max_tokens
            or self.total_cost >= self.max_cost
            or self.total_tool_calls >= self.max_tool_calls
        )

    def record(self, response: LLMResponse) -> None:
        self.total_tokens += response.tokens_used
        self.total_cost += response.estimated_cost
        self.turns_used += 1


@dataclass
class SuspectCommit:
    """A candidate bug-introducing commit with evidence."""

    commit_id: str
    rank: int
    confidence: float
    mechanism: str
    evidence_quotes: list[str] = field(default_factory=list)


@dataclass
class ToolCallRecord:
    """Record of a single tool invocation."""

    tool: str
    args: dict[str, Any]
    result: str
    latency_ms: float = 0.0


@dataclass
class BugAttributionReport:
    """Final output of the attribution agent."""

    problem_title: str
    problem_description: str
    suspects: list[SuspectCommit]
    reasoning_summary: str
    tool_trace: list[ToolCallRecord]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_title": self.problem_title,
            "problem_description": self.problem_description[:500],
            "suspects": [
                {
                    "commit_id": s.commit_id,
                    "rank": s.rank,
                    "confidence": s.confidence,
                    "mechanism": s.mechanism,
                    "evidence_quotes": s.evidence_quotes,
                }
                for s in self.suspects
            ],
            "reasoning_summary": self.reasoning_summary,
            "tool_trace": [
                {"tool": t.tool, "args": t.args, "latency_ms": t.latency_ms}
                for t in self.tool_trace
            ],
            "metadata": self.metadata,
        }


def _build_tool_descriptions(registry: ToolRegistry) -> str:
    """Format tool descriptions for the system prompt."""
    lines = []
    for tool_def in registry.to_openai_tools():
        fn = tool_def["function"]
        params = fn.get("parameters", {}).get("properties", {})
        required = fn.get("parameters", {}).get("required", [])
        param_strs = []
        for pname, pinfo in params.items():
            req = " (required)" if pname in required else ""
            param_strs.append(f"    {pname}: {pinfo.get('description', '')}{req}")
        lines.append(f"**{fn['name']}**: {fn['description']}")
        if param_strs:
            lines.append("  Parameters:")
            lines.extend(param_strs)
        lines.append("")
    return "\n".join(lines)


_TOOL_CALL_PATTERN = re.compile(
    r"```tool\s*\n\s*(\{[^`]+?\})\s*\n\s*```",
    re.DOTALL,
)

_SUSPECTS_PATTERN = re.compile(
    r"```suspects\s*\n\s*(\[[\s\S]+?\])\s*\n\s*```",
    re.DOTALL,
)


def _parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """Extract tool call JSON blocks from LLM output."""
    calls = []
    for match in _TOOL_CALL_PATTERN.finditer(text):
        try:
            parsed = json.loads(match.group(1))
            if "tool" in parsed:
                calls.append(parsed)
        except json.JSONDecodeError:
            continue
    return calls


def _parse_suspects(text: str) -> list[SuspectCommit]:
    """Extract suspects list from LLM output."""
    match = _SUSPECTS_PATTERN.search(text)
    if not match:
        return []
    try:
        raw = json.loads(match.group(1))
        suspects = []
        for i, item in enumerate(raw):
            suspects.append(SuspectCommit(
                commit_id=item.get("commit_id", ""),
                rank=i + 1,
                confidence=float(item.get("confidence", 0.0)),
                mechanism=item.get("mechanism", ""),
                evidence_quotes=item.get("evidence_quotes", []),
            ))
        return suspects
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _evidence_score_to_dict(score: SuspectEvidenceScore) -> dict[str, Any]:
    """Serialize evidence score for report metadata (JSON-safe)."""
    return {
        "commit_id": score.commit_id,
        "total_quotes": score.total_quotes,
        "grounded_quotes": score.grounded_quotes,
        "grounding_rate": score.grounding_rate,
    }


def _attach_evidence_scores(
    suspects: list[SuspectCommit],
    git_provider: GitContextProvider,
) -> list[dict[str, Any]]:
    """Score each suspect's evidence quotes and return metadata-ready dicts."""
    scores: list[dict[str, Any]] = []
    for suspect in suspects:
        diff = None
        if suspect.commit_id:
            try:
                diff = git_provider.get_diff(suspect.commit_id)
            except Exception:
                diff = None
        score = score_suspect_evidence(
            commit_id=suspect.commit_id,
            evidence_quotes=suspect.evidence_quotes,
            diff=diff,
        )
        scores.append(_evidence_score_to_dict(score))
    return scores


class AgentOrchestrator:
    """V3 bug attribution agent.

    Given a problem description (JIRA ticket) and a temporally-bounded git
    repository, searches for the commit that caused the reported bug.
    """

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        max_tokens: int = 100_000,
        max_cost: float = 0.50,
        max_tool_calls: int = 30,
        max_turns: int = 15,
        checkpoint_dir: str | Path | None = None,
    ) -> None:
        self._llm = llm_provider or get_provider()
        self._max_turns = max_turns
        self._checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self._default_budget = {
            "max_tokens": max_tokens,
            "max_cost": max_cost,
            "max_tool_calls": max_tool_calls,
        }

    def investigate(
        self,
        problem: ProblemStatement,
        git_provider: GitContextProvider,
    ) -> BugAttributionReport:
        """Run bug attribution investigation.

        Args:
            problem: The bug report (JIRA title + description).
            git_provider: Temporally-bounded git access.

        Returns:
            BugAttributionReport with ranked suspects.
        """
        budget = BudgetState(**self._default_budget)
        registry = build_attribution_tools(git_provider)
        tool_trace: list[ToolCallRecord] = []

        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            tool_descriptions=_build_tool_descriptions(registry),
        )

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=problem.to_prompt_text()),
        ]

        all_text = []
        suspects: list[SuspectCommit] = []
        start_time = time.time()

        for turn in range(self._max_turns):
            if budget.budget_exceeded:
                logger.warning("Budget exceeded at turn %d", turn)
                break

            response = self._llm.complete(messages, max_tokens=4096)
            budget.record(response)
            all_text.append(response.content)

            parsed_suspects = _parse_suspects(response.content)
            if parsed_suspects:
                suspects = parsed_suspects
                break

            tool_calls = _parse_tool_calls(response.content)
            if not tool_calls:
                suspects = parsed_suspects
                break

            tool_results_parts = []
            for call in tool_calls:
                if budget.total_tool_calls >= budget.max_tool_calls:
                    tool_results_parts.append(
                        f"**{call['tool']}**: Budget limit reached ({budget.max_tool_calls} tool calls)."
                    )
                    break

                tool_start = time.time()
                try:
                    result = registry.execute(call["tool"], **call.get("args", {}))
                except TemporalBoundViolation as e:
                    result = f"Error: {e}"

                tool_latency = (time.time() - tool_start) * 1000
                budget.total_tool_calls += 1

                record = ToolCallRecord(
                    tool=call["tool"],
                    args=call.get("args", {}),
                    result=result[:500],
                    latency_ms=tool_latency,
                )
                tool_trace.append(record)
                tool_results_parts.append(f"**{call['tool']}**:\n{result}")

            messages.append(LLMMessage(role="assistant", content=response.content))
            messages.append(LLMMessage(
                role="user",
                content=TURN_PROMPT.format(
                    tool_calls_used=budget.total_tool_calls,
                    max_tool_calls=budget.max_tool_calls,
                    tokens_used=budget.total_tokens,
                    tool_results="\n\n".join(tool_results_parts),
                ),
            ))

        elapsed_ms = (time.time() - start_time) * 1000

        reasoning = "\n\n".join(all_text)[:2000]
        evidence_scores = _attach_evidence_scores(suspects, git_provider)

        return BugAttributionReport(
            problem_title=problem.title,
            problem_description=problem.description,
            suspects=suspects,
            reasoning_summary=reasoning,
            tool_trace=tool_trace,
            metadata={
                "turns_used": budget.turns_used,
                "tool_calls": budget.total_tool_calls,
                "tokens_used": budget.total_tokens,
                "total_cost_usd": budget.total_cost,
                "elapsed_ms": elapsed_ms,
                "temporal_bound": git_provider.temporal_bound,
                "model": self._llm.model_name,
                "budget_exceeded": budget.budget_exceeded,
                "evidence_scores": evidence_scores,
                "evidence_scoring_applied": True,
                "post_processing_applied": False,
            },
        )
