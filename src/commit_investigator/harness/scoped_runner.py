"""Scoped investigation — V4.2 examination tools scoped to CandidateSet.

Components: RevisedScopedInvestigator, NudgeLadder, MustExamineGate,
ToolCallCache, RollingSummary, Phase2Result.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from commit_investigator.agent.tools import build_scoped_tools
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.harness.scoped_prompts import (
    build_phase2_system_prompt,
    parse_suspects,
    parse_tool_calls,
)
from commit_investigator.narrowing.models import TriageResult
from commit_investigator.harness.result import InvestigationExitReason
from commit_investigator.infra.git_context import GitContextProvider, TemporalBoundViolation
from commit_investigator.infra.llm import LLMMessage, LLMProvider
from commit_investigator.models.candidates import CandidateSet


class NudgeAction(str, Enum):
    """What the harness should do after a nudge evaluation."""

    NONE = "none"
    DIRECTIVE = "directive"
    WARNING = "warning"
    FORCE_CONCLUDE = "force_conclude"
    REJECT_SUSPECTS = "reject_suspects"


@dataclass
class NudgeResult:
    """Output of NudgeLadder.evaluate() — action + message for the LLM."""

    action: NudgeAction
    message: str = ""


class NudgeLadder:
    """4-tier nudge ladder per V4.2 ADR §Nudge Ladder.

    Tracks consecutive idle turns (no tool call, no suspects) and produces
    escalating interventions. Also rejects suspects submitted without any
    diff examination.
    """

    def __init__(self, must_examine_shas: list[str]) -> None:
        self._must_examine_shas = list(must_examine_shas)
        self._consecutive_idle = 0

    def reset_idle(self) -> None:
        """Call after a turn that produced tool calls or valid suspects."""
        self._consecutive_idle = 0

    def evaluate_idle(
        self, calls_used: int, budget: int, unexamined_shas: list[str]
    ) -> NudgeResult:
        """Evaluate an idle turn (no tool calls, no suspects parsed)."""
        self._consecutive_idle += 1

        if self._consecutive_idle == 1:
            target = unexamined_shas[0] if unexamined_shas else self._must_examine_shas[0]
            return NudgeResult(
                action=NudgeAction.DIRECTIVE,
                message=f"Call get_commit_diff on {target}. Output tool block only.",
            )
        if self._consecutive_idle == 2:
            return NudgeResult(
                action=NudgeAction.WARNING,
                message=(
                    f"You have {calls_used}/{budget} calls. "
                    "Examine remaining must-examine SHAs or output suspects."
                ),
            )
        return NudgeResult(action=NudgeAction.FORCE_CONCLUDE, message="")

    def evaluate_suspects_without_diff(self) -> NudgeResult:
        """Reject suspects submitted before any diff examination."""
        return NudgeResult(
            action=NudgeAction.REJECT_SUSPECTS,
            message="Suspects rejected: no diff examined. Call get_commit_diff before suspects.",
        )

    @property
    def consecutive_idle(self) -> int:
        return self._consecutive_idle


class MustExamineGate:
    """Tracks per-SHA diff examination for must-examine candidates.

    Suspects are accepted only after at least one get_commit_diff call
    on a must-examine SHA returns a non-error result.
    """

    def __init__(self, must_examine_shas: list[str]) -> None:
        self._required = set(must_examine_shas)
        self._examined: set[str] = set()

    def record_diff(self, sha: str, success: bool) -> None:
        """Record a successful get_commit_diff execution."""
        resolved = self._resolve(sha)
        if success and resolved is not None:
            self._examined.add(resolved)

    def is_satisfied(self) -> bool:
        """At least 1 must-examine SHA has been diffed (or none required)."""
        if not self._required:
            return True
        return len(self._examined) > 0

    @property
    def examined_shas(self) -> set[str]:
        return set(self._examined)

    @property
    def unexamined_shas(self) -> list[str]:
        return [sha for sha in self._required if sha not in self._examined]

    @property
    def coverage(self) -> float:
        if not self._required:
            return 1.0
        return len(self._examined) / len(self._required)

    def _matches_required(self, sha: str) -> bool:
        return self._resolve(sha) is not None

    def _resolve(self, sha: str) -> str | None:
        """Resolve a SHA (possibly prefix) to a required must-examine SHA."""
        for req in self._required:
            if req == sha or req.startswith(sha[:12]) or sha.startswith(req[:12]):
                return req
        return None


class ToolCallCache:
    """Cache dedup layer for tool calls — AgentSZZ-inspired context compression.

    Same (tool_name, args) → cached preview instead of re-executing.
    Saves budget and avoids redundant context inflation.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def _key(self, tool: str, args: dict[str, Any]) -> str:
        sorted_args = tuple(sorted(args.items()))
        return f"{tool}:{sorted_args}"

    def get(self, tool: str, args: dict[str, Any]) -> str | None:
        return self._cache.get(self._key(tool, args))

    def put(self, tool: str, args: dict[str, Any], result: str) -> None:
        self._cache[self._key(tool, args)] = result

    def dedup_message(self, tool: str, args: dict[str, Any]) -> str:
        """Message returned when a duplicate call is detected."""
        return f"Already examined — see previous result for {tool}({args})."

    @property
    def size(self) -> int:
        return len(self._cache)


class RollingSummary:
    """Harness-maintained rolling summary of what has been learned.

    Appends 1-line summaries per tool execution. Truncates to max_chars
    to bound context window growth.
    """

    def __init__(self, max_chars: int = 2000) -> None:
        self._lines: list[str] = []
        self._max_chars = max_chars

    def add_tool_result(self, tool: str, args: dict[str, Any], result: str) -> None:
        """Extract a 1-line summary from a tool result and append it."""
        sha = args.get("commit_id", args.get("path", "?"))[:12]
        preview = result[:120].replace("\n", " ").strip()
        self._lines.append(f"- {tool}({sha}): {preview}")
        self._trim()

    def _trim(self) -> None:
        """Drop oldest lines until total length is within budget."""
        while self._lines and len(self.text) > self._max_chars:
            self._lines.pop(0)

    @property
    def text(self) -> str:
        return "\n".join(self._lines)

    @property
    def line_count(self) -> int:
        return len(self._lines)


@dataclass
class ToolCallRecord:
    tool: str
    args: dict[str, Any]
    result_preview: str = ""
    latency_ms: float = 0.0


@dataclass
class Phase2Result:
    """V4.2 Phase 2 investigation result with typed fields."""

    suspects: list[dict[str, Any]] = field(default_factory=list)
    exit_reason: InvestigationExitReason = InvestigationExitReason.NORMAL
    tool_trace: list[ToolCallRecord] = field(default_factory=list)
    must_examine_coverage: float = 0.0
    diff_examined: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class RevisedScopedInvestigator:
    """V4.2 Phase 2: scoped investigation with must-examine focus.

    Key differences from V4.1 ScopedInvestigator:
    - Prompt shows only 3 must-examine candidates (not 20)
    - 4-tier nudge ladder (not single generic nudge)
    - Must-examine gate (per-SHA diff tracking)
    - Tool call cache dedup (no re-execution)
    - Compressed context (system + rolling summary + last-turn results)
    - Explicit InvestigationExitReason on every exit
    """

    def __init__(
        self,
        llm: LLMProvider,
        problem: ProblemStatement,
        triage: TriageResult,
        candidate_set: CandidateSet,
        git: GitContextProvider,
        *,
        max_tool_calls: int = 15,
        max_turns: int = 8,
    ) -> None:
        self._llm = llm
        self._problem = problem
        self._triage = triage
        self._candidates = candidate_set
        self._max_tool_calls = max_tool_calls
        self._max_turns = max_turns
        self._registry = build_scoped_tools(git, candidate_set)

        must_shas = triage.must_examine_shas
        self._gate = MustExamineGate(must_shas)
        self._nudge = NudgeLadder(must_shas)
        self._cache = ToolCallCache()
        self._summary = RollingSummary()

    def investigate(self) -> Phase2Result:
        if not self._candidates.commits:
            return Phase2Result(exit_reason=InvestigationExitReason.EMPTY_CANDIDATES)

        trace: list[ToolCallRecord] = []
        calls_used = 0
        start = time.time()

        system_prompt = build_phase2_system_prompt(
            self._problem, self._triage, self._registry,
        )

        for turn in range(self._max_turns):
            messages = self._build_turn_messages(system_prompt, turn)

            try:
                resp = self._llm.complete(messages, max_tokens=4096)
            except Exception as exc:
                return self._finish(
                    [], trace, start, InvestigationExitReason.PROVIDER_ERROR,
                    error=str(exc),
                )

            suspects = parse_suspects(resp.content)
            if suspects:
                if not self._gate.is_satisfied():
                    nudge = self._nudge.evaluate_suspects_without_diff()
                    self._summary._lines.append(f"[harness] {nudge.message}")
                    continue
                self._nudge.reset_idle()
                return self._finish(suspects, trace, start, InvestigationExitReason.NORMAL)

            parsed = parse_tool_calls(resp.content)
            if not parsed:
                nudge = self._nudge.evaluate_idle(
                    calls_used, self._max_tool_calls, self._gate.unexamined_shas,
                )
                if nudge.action == NudgeAction.FORCE_CONCLUDE:
                    return self._finish(
                        [], trace, start, InvestigationExitReason.FORCED_CONCLUDE,
                    )
                self._summary._lines.append(f"[harness] {nudge.message}")
                continue

            self._nudge.reset_idle()
            for call in parsed:
                if calls_used >= self._max_tool_calls:
                    break
                tool_name = call["tool"]
                args = call.get("args", {})

                cached = self._cache.get(tool_name, args)
                if cached is not None:
                    self._summary._lines.append(
                        f"[cache] {self._cache.dedup_message(tool_name, args)}"
                    )
                    continue

                t0 = time.time()
                try:
                    result = self._registry.execute(tool_name, **args)
                except TemporalBoundViolation as exc:
                    result = f"Error: {exc}"

                calls_used += 1
                latency = (time.time() - t0) * 1000
                trace.append(ToolCallRecord(tool_name, args, result[:500], latency))
                self._cache.put(tool_name, args, result)
                self._summary.add_tool_result(tool_name, args, result)

                if tool_name == "get_commit_diff":
                    sha = args.get("commit_id", "")
                    self._gate.record_diff(sha, not result.startswith("Error"))

        return self._finish([], trace, start, InvestigationExitReason.MAX_TURNS)

    def _build_turn_messages(
        self, system_prompt: str, turn: int
    ) -> list[LLMMessage]:
        """Compressed context: system + rolling summary + turn instruction."""
        messages = [LLMMessage(role="system", content=system_prompt)]

        if turn == 0:
            messages.append(LLMMessage(
                role="user",
                content="Begin investigation. Examine each must-examine commit with get_commit_diff.",
            ))
        else:
            summary_text = self._summary.text
            content_parts = []
            if summary_text:
                content_parts.append(f"## Investigation Progress\n{summary_text}")
            content_parts.append(
                "Continue examining candidates or conclude with ```suspects."
            )
            messages.append(LLMMessage(role="user", content="\n\n".join(content_parts)))

        return messages

    def _finish(
        self,
        suspects: list[dict[str, Any]],
        trace: list[ToolCallRecord],
        start: float,
        exit_reason: InvestigationExitReason,
        error: str | None = None,
    ) -> Phase2Result:
        elapsed = round((time.time() - start) * 1000, 1)
        metadata: dict[str, Any] = {
            "tool_calls": len(trace),
            "elapsed_ms": elapsed,
            "model": self._llm.model_name,
            "candidates_total": len(self._candidates.commits),
            "must_examine_coverage": self._gate.coverage,
            "cache_hits": self._cache.size - len(trace),
            "exit_reason": exit_reason.value,
        }
        if error:
            metadata["error"] = error
        return Phase2Result(
            suspects=suspects,
            exit_reason=exit_reason,
            tool_trace=trace,
            must_examine_coverage=self._gate.coverage,
            diff_examined=self._gate.is_satisfied(),
            metadata=metadata,
        )


