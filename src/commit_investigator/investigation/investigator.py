"""Scoped investigation — V4.2 examination tools scoped to CandidateSet."""

from __future__ import annotations

import time
from typing import Any

from commit_investigator.investigation.tools import build_scoped_tools
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.investigation.loop_support import (
    MustExamineGate,
    NudgeAction,
    NudgeLadder,
    NudgeResult,
    Phase2Result,
    RollingSummary,
    ToolCallCache,
    ToolCallRecord,
)
from commit_investigator.investigation.prompts import (
    build_phase2_system_prompt,
    parse_suspects,
    parse_tool_calls,
)
from commit_investigator.narrowing.models import TriageResult
from commit_investigator.investigation.result import InvestigationExitReason
from commit_investigator.infra.git_context import GitContextProvider, TemporalBoundViolation
from commit_investigator.infra.llm import LLMMessage, LLMProvider
from commit_investigator.models.candidates import CandidateSet

__all__ = [
    "MustExamineGate",
    "NudgeAction",
    "NudgeLadder",
    "NudgeResult",
    "Phase2Result",
    "RevisedScopedInvestigator",
    "RollingSummary",
    "ToolCallCache",
    "ToolCallRecord",
]


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


