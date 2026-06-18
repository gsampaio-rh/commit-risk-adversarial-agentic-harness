"""Support classes for scoped investigation — V4.2 examination harness.

Extracted from scoped_runner.py for file-sizing compliance.
These are cohesive support types used by RevisedScopedInvestigator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from commit_investigator.investigation.result import InvestigationExitReason


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
