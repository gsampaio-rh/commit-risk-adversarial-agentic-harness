"""InvestigationTrace and nested record types for V4 structured tracing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self

from commit_investigator.models._serde import instance_to_dict

VALID_HYPOTHESIS_STATUSES = frozenset({"formed", "confirmed", "rejected", "abandoned"})
VALID_DEGRADED_REASONS = frozenset(
    {"budget_exhausted", "replan_limit", "no_confirmed_hypotheses", "empty_candidates"}
)


class HypothesisStatus(StrEnum):
    """Lifecycle status for a hypothesis in the investigation trace."""

    FORMED = "formed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    ABANDONED = "abandoned"


@dataclass
class HypothesisRecord:
    """One hypothesis event in an InvestigationTrace."""

    id: str
    statement: str
    status: str
    reason: str
    stage: int
    turn: int | None = None

    def __post_init__(self) -> None:
        if self.status not in VALID_HYPOTHESIS_STATUSES:
            msg = f"Invalid hypothesis status: {self.status!r}"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        status = data["status"]
        if status not in VALID_HYPOTHESIS_STATUSES:
            msg = f"Invalid hypothesis status: {status!r}"
            raise ValueError(msg)
        return cls(
            id=data["id"],
            statement=data["statement"],
            status=status,
            reason=data["reason"],
            stage=data["stage"],
            turn=data.get("turn"),
        )


@dataclass
class EliminationRecord:
    """Record of a candidate commit eliminated during examination."""

    commit_id: str
    reason: str
    turn: int
    hypothesis_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            commit_id=data["commit_id"],
            reason=data["reason"],
            turn=data["turn"],
            hypothesis_id=data.get("hypothesis_id"),
        )


@dataclass
class EvidenceRecord:
    """Evidence quote collected during examination."""

    commit_id: str
    quote: str
    turn: int
    grounded: bool | None = None
    hypothesis_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            commit_id=data["commit_id"],
            quote=data["quote"],
            turn=data["turn"],
            grounded=data.get("grounded"),
            hypothesis_id=data.get("hypothesis_id"),
        )


@dataclass
class StrategyRecord:
    """Strategic decision logged during an investigation."""

    decision: str
    rationale: str
    stage: int
    alternatives_considered: list[str] = field(default_factory=list)
    turn: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            decision=data["decision"],
            rationale=data["rationale"],
            stage=data["stage"],
            alternatives_considered=list(data.get("alternatives_considered", [])),
            turn=data.get("turn"),
        )


@dataclass
class TraceToolCall:
    """Tool invocation within a TurnRecord."""

    tool: str
    args: dict[str, Any]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            tool=data["tool"],
            args=dict(data.get("args", {})),
            summary=data["summary"],
        )


@dataclass
class CompletionCheck:
    """Per-turn completion criteria evaluation snapshot."""

    evidence_met: bool
    coverage_met: bool
    confidence_met: bool
    brief_satisfied: bool

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            evidence_met=data["evidence_met"],
            coverage_met=data["coverage_met"],
            confidence_met=data["confidence_met"],
            brief_satisfied=data["brief_satisfied"],
        )


@dataclass
class TurnRecord:
    """Per-turn Stage 3 examination log entry."""

    turn: int
    tool_calls: list[TraceToolCall] = field(default_factory=list)
    hypothesis_updates: list[str] = field(default_factory=list)
    completion_check: CompletionCheck | None = None

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        check_data = data.get("completion_check")
        return cls(
            turn=data["turn"],
            tool_calls=[TraceToolCall.from_dict(item) for item in data.get("tool_calls", [])],
            hypothesis_updates=list(data.get("hypothesis_updates", [])),
            completion_check=CompletionCheck.from_dict(check_data) if check_data is not None else None,
        )


@dataclass
class OutcomeRecord:
    """Final investigation outcome summary."""

    suspect_count: int
    top_confidence: float
    degraded: bool
    degraded_reason: str | None = None
    hit_at_5: bool | None = None
    mrr: float | None = None

    def __post_init__(self) -> None:
        if self.degraded_reason is not None and self.degraded_reason not in VALID_DEGRADED_REASONS:
            msg = f"Invalid degraded_reason: {self.degraded_reason!r}"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        degraded_reason = data.get("degraded_reason")
        if degraded_reason is not None and degraded_reason not in VALID_DEGRADED_REASONS:
            msg = f"Invalid degraded_reason: {degraded_reason!r}"
            raise ValueError(msg)
        return cls(
            suspect_count=data["suspect_count"],
            top_confidence=data["top_confidence"],
            degraded=data["degraded"],
            degraded_reason=degraded_reason,
            hit_at_5=data.get("hit_at_5"),
            mrr=data.get("mrr"),
        )


@dataclass
class InvestigationTrace:
    """Structured record of one investigation run."""

    trace_id: str
    issue_key: str
    run_id: str
    temporal_bound: str
    candidate_set_size: int
    hypotheses: list[HypothesisRecord] = field(default_factory=list)
    candidates_examined: list[str] = field(default_factory=list)
    candidates_eliminated: list[EliminationRecord] = field(default_factory=list)
    evidence_collected: list[EvidenceRecord] = field(default_factory=list)
    strategy_decisions: list[StrategyRecord] = field(default_factory=list)
    examination_turns: list[TurnRecord] = field(default_factory=list)
    stage_timings: dict[str, float] = field(default_factory=dict)
    outcome: OutcomeRecord | None = None
    retrieval_recall_100: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        outcome_data = data.get("outcome")
        return cls(
            trace_id=data["trace_id"],
            issue_key=data["issue_key"],
            run_id=data["run_id"],
            temporal_bound=data["temporal_bound"],
            candidate_set_size=data["candidate_set_size"],
            hypotheses=[HypothesisRecord.from_dict(item) for item in data.get("hypotheses", [])],
            candidates_examined=list(data.get("candidates_examined", [])),
            candidates_eliminated=[
                EliminationRecord.from_dict(item) for item in data.get("candidates_eliminated", [])
            ],
            evidence_collected=[
                EvidenceRecord.from_dict(item) for item in data.get("evidence_collected", [])
            ],
            strategy_decisions=[
                StrategyRecord.from_dict(item) for item in data.get("strategy_decisions", [])
            ],
            examination_turns=[
                TurnRecord.from_dict(item) for item in data.get("examination_turns", [])
            ],
            stage_timings={key: float(value) for key, value in data.get("stage_timings", {}).items()},
            outcome=OutcomeRecord.from_dict(outcome_data) if outcome_data is not None else None,
            retrieval_recall_100=data.get("retrieval_recall_100"),
        )
