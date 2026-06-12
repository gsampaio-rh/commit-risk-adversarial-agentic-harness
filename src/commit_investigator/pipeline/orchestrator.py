"""Agent orchestrator: bounded multi-turn investigation loop.

The orchestrator owns turn limits, tool dispatch, budget tracking,
checkpoint persistence, and report assembly. The LLM generates hypotheses;
Script layers handle risk scoring, evidence tagging, and gate decisions.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from commit_investigator.analysis.archetype import detect_archetype, has_production_defect_signals
from commit_investigator.context.context_builder import CommitContextBuilder, InvestigationContext
from commit_investigator.context.git_context import GitContextProvider
from commit_investigator.hypothesis.hypothesis_engine import (
    HypothesisResponse,
    build_investigation_messages,
    complete_with_parse_retry,
    generate_contrastive_hypotheses,
    mechanism_evaluator_loop,
    parse_hypothesis_response,
    select_primary_by_evidence,
)
from commit_investigator.hypothesis.hypothesis_prompts import (
    HYPOTHESIS_SYSTEM_PROMPT,
    HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE,
    HYPOTHESIS_SYSTEM_PROMPT_SYMPTOM_FIRST_WITH_EVALUATOR,
)
from commit_investigator.infra.llm import LLMMessage, LLMProvider, LLMResponse, get_provider
from commit_investigator.analysis.quality_gate import HypothesisArtifact, evaluate_gate
from commit_investigator.analysis.report import (
    CommitInvestigationReport,
    LocalizationClaim,
)
from commit_investigator.pipeline.report_builder import build_report, tag_hypotheses
from commit_investigator.analysis.risk_policy import evaluate_risk_from_hypotheses
from commit_investigator.pipeline.tools import ToolRegistry, build_default_registry
from commit_investigator.context.turn2_context import Turn2ContextBundle, build_turn2_follow_up

INVESTIGATION_SYSTEM_PROMPT = HYPOTHESIS_SYSTEM_PROMPT

__all__ = [
    "INVESTIGATION_SYSTEM_PROMPT",
    "AgentOrchestrator",
    "FollowUpMode",
    "InvalidInvestigationResponseError",
    "BudgetState",
    "TurnCheckpoint",
    "DEFAULT_MAX_DIFF_CHARS",
]

DEFAULT_MAX_DIFF_CHARS = 16_000


class FollowUpMode(str, Enum):
    """How turn-2+ follow-up is triggered."""

    GATE = "gate"
    ALWAYS = "always"


class InvalidInvestigationResponseError(ValueError):
    """Raised when LLM output is empty, unparseable, or missing required fields."""


@dataclass
class BudgetState:
    """Tracks token usage and cost across turns."""

    total_tokens: int = 0
    total_cost: float = 0.0
    max_tokens: int = 50000
    max_cost: float = 0.50
    turns_used: int = 0

    @property
    def budget_exceeded(self) -> bool:
        return self.total_tokens >= self.max_tokens or self.total_cost >= self.max_cost

    def record(self, response: LLMResponse) -> None:
        self.total_tokens += response.tokens_used
        self.total_cost += response.estimated_cost
        self.turns_used += 1


@dataclass
class TurnCheckpoint:
    """Persisted state for a single investigation turn."""

    turn: int
    timestamp: float
    messages_sent: int
    tool_calls_made: list[str]
    tokens_used: int
    cost: float
    follow_up_needed: bool
    latency_ms: float = 0.0
    turn2_injection: str | None = None


class AgentOrchestrator:
    """Bounded multi-turn investigative agent.

    LLM generates HypothesisResponse; Script layers score risk, tag evidence,
    and decide on follow-up gates. Report assembly is deterministic.
    """

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        max_turns: int = 3,
        max_tokens: int = 50000,
        max_cost: float = 0.50,
        checkpoint_dir: str | Path | None = None,
        max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS,
        follow_up_mode: FollowUpMode = FollowUpMode.GATE,
        enable_mechanism_evaluator: bool = False,
        enable_contrastive: bool = False,
    ) -> None:
        self._llm = llm_provider or get_provider()
        self._max_turns = max_turns
        self._budget = BudgetState(max_tokens=max_tokens, max_cost=max_cost)
        self._checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self._checkpoints: list[TurnCheckpoint] = []
        self._max_diff_chars = max_diff_chars
        self._follow_up_mode = follow_up_mode
        self._enable_mechanism_evaluator = enable_mechanism_evaluator
        self._enable_contrastive = enable_contrastive
        self._last_turn2_bundle: Turn2ContextBundle | None = None

    def investigate(
        self,
        commit_id: str,
        project: str,
        csv_row: dict[str, Any] | None = None,
        git_provider: GitContextProvider | None = None,
        context: InvestigationContext | None = None,
    ) -> CommitInvestigationReport:
        """Run a bounded multi-turn investigation on a commit."""
        self._budget = BudgetState(max_tokens=self._budget.max_tokens, max_cost=self._budget.max_cost)
        self._checkpoints = []
        self._last_turn2_bundle = None

        if git_provider is None and context is None:
            raise ValueError("Either git_provider or pre-built context required")

        if context is None:
            builder = CommitContextBuilder(git_provider)  # type: ignore[arg-type]
            context = builder.build(commit_id, project, csv_row)

        tools = _build_tools(git_provider, context)
        system_prompt = self._select_system_prompt()
        messages = build_investigation_messages(context, system_prompt=system_prompt)
        tools_used: list[str] = []
        all_tool_calls: list[str] = []
        hyp_response: HypothesisResponse | None = None
        last_response: LLMResponse | None = None
        hypothesis_fn = self._select_hypothesis_fn()

        for turn in range(1, self._max_turns + 1):
            if self._budget.budget_exceeded:
                break

            turn_start = time.time()
            hyp_response, last_response = hypothesis_fn(
                self._llm, messages,
                tools.to_openai_tools() if tools else None,
                self._parse_response, self._budget.record,
                InvalidInvestigationResponseError,
            )
            turn_latency_ms = (time.time() - turn_start) * 1000

            if last_response.tool_calls:
                for tc in last_response.tool_calls:
                    result = tools.execute(tc["name"], **tc.get("arguments", {}))
                    all_tool_calls.append(tc["name"])
                    messages.append(LLMMessage(role="assistant", content=f"[Tool call: {tc['name']}]"))
                    messages.append(LLMMessage(role="tool", content=result, name=tc["name"]))
                tools_used.extend(tc["name"] for tc in last_response.tool_calls)
                continue

            follow_up_needed = self._should_follow_up(hyp_response, context, turn)
            turn2_injection: str | None = None
            if follow_up_needed and turn < self._max_turns and git_provider is not None:
                bundle = build_turn2_follow_up(context, git_provider)
                self._last_turn2_bundle = bundle
                turn2_injection = bundle.message
                messages.append(LLMMessage(role="user", content=bundle.message))

            self._save_checkpoint(TurnCheckpoint(
                turn=turn, timestamp=time.time(), messages_sent=len(messages),
                tool_calls_made=all_tool_calls.copy(), tokens_used=self._budget.total_tokens,
                cost=self._budget.total_cost, follow_up_needed=follow_up_needed,
                latency_ms=turn_latency_ms,
                turn2_injection=turn2_injection,
            ))
            if not follow_up_needed:
                break

        if hyp_response is None or last_response is None:
            raise InvalidInvestigationResponseError("No LLM response received")
        if self._enable_contrastive:
            reordered = select_primary_by_evidence(hyp_response.hypotheses)
            hyp_response = HypothesisResponse(summary=hyp_response.summary, hypotheses=reordered)
        tagged = tag_hypotheses(hyp_response.hypotheses, context.diff, context.truncation_metadata)
        verdict = evaluate_risk_from_hypotheses(tagged, context)
        return build_report(
            hyp_response=hyp_response,
            tagged=tagged,
            verdict=verdict,
            context=context,
            last_response=last_response,
            checkpoints=self._checkpoints,
            budget=self._budget,
            tools_used=list(set(tools_used + all_tool_calls)),
            turns=self._budget.turns_used,
            turn2_bundle=self._last_turn2_bundle,
        )

    def _select_system_prompt(self) -> str:
        if self._enable_contrastive:
            return HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE
        if self._enable_mechanism_evaluator:
            return HYPOTHESIS_SYSTEM_PROMPT_SYMPTOM_FIRST_WITH_EVALUATOR
        return HYPOTHESIS_SYSTEM_PROMPT

    def _select_hypothesis_fn(self):
        if self._enable_contrastive:
            return generate_contrastive_hypotheses
        if self._enable_mechanism_evaluator:
            return mechanism_evaluator_loop
        return complete_with_parse_retry

    def _parse_response(self, response: LLMResponse) -> HypothesisResponse:
        content = (response.content or "").strip()
        if not content:
            raise InvalidInvestigationResponseError("Empty LLM response; cannot assemble report")
        try:
            return parse_hypothesis_response(content)
        except (ValueError, KeyError) as exc:
            raise InvalidInvestigationResponseError(
                f"Invalid LLM JSON (missing summary/hypotheses): {content[:300]!r}"
            ) from exc

    def _should_follow_up(
        self,
        hyp_response: HypothesisResponse,
        context: InvestigationContext,
        turn: int,
    ) -> bool:
        if turn >= self._max_turns:
            return False
        if self._follow_up_mode == FollowUpMode.ALWAYS:
            return True
        tagged = tag_hypotheses(hyp_response.hypotheses, context.diff, context.truncation_metadata)
        supported_count = sum(1 for t in tagged if t.tier == "SUPPORTED")
        tm = context.truncation_metadata
        diff_truncated = bool(tm and tm.truncated_files) or len(context.diff or "") >= self._max_diff_chars
        localization = [
            LocalizationClaim(file=h.file, lines=None, rationale=h.mechanism)
            for h in hyp_response.hypotheses if h.file
        ]
        findings = [h.mechanism for h, t in zip(hyp_response.hypotheses, tagged) if t.tier == "SUPPORTED"]
        artifact = HypothesisArtifact(
            supported_count=supported_count,
            production_defect_signals=has_production_defect_signals(context),
            localization=localization,
            diff_truncated=diff_truncated,
            archetype_is_ambiguous=not detect_archetype(context),
            findings=findings,
            validation_error=None,
        )
        return evaluate_gate(artifact).follow_up_needed

    def _save_checkpoint(self, checkpoint: TurnCheckpoint) -> None:
        self._checkpoints.append(checkpoint)
        if self._checkpoint_dir:
            self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
            path = self._checkpoint_dir / f"turn_{checkpoint.turn}.json"
            payload: dict[str, Any] = {
                "turn": checkpoint.turn,
                "timestamp": checkpoint.timestamp,
                "messages_sent": checkpoint.messages_sent,
                "tool_calls_made": checkpoint.tool_calls_made,
                "tokens_used": checkpoint.tokens_used,
                "cost": checkpoint.cost,
                "follow_up_needed": checkpoint.follow_up_needed,
                "latency_ms": checkpoint.latency_ms,
            }
            if checkpoint.turn2_injection:
                payload["turn2_injection"] = checkpoint.turn2_injection
            path.write_text(json.dumps(payload, indent=2))


def _build_tools(
    git_provider: GitContextProvider | None,
    context: InvestigationContext,
) -> ToolRegistry:
    if git_provider is None:
        return ToolRegistry()
    return build_default_registry(git_provider, context)
