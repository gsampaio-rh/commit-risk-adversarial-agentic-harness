"""InvestigationBrief, InvestigationState, and related V4 harness types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self


@dataclass
class Hypothesis:
    """Falsifiable statement in an InvestigationBrief."""

    id: str
    statement: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "statement": self.statement}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(id=data["id"], statement=data["statement"])


@dataclass
class ExaminationStep:
    """One planned examination in an InvestigationBrief."""

    look_for: str
    commit_id: str | None = None
    file_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_id": self.commit_id,
            "file_path": self.file_path,
            "look_for": self.look_for,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            look_for=data["look_for"],
            commit_id=data.get("commit_id"),
            file_path=data.get("file_path"),
        )


@dataclass
class CompletionCriteria:
    """Brief-driven exit conditions for an investigation."""

    evidence_threshold: int = 3
    hypothesis_coverage: int = 2
    confidence_gate: float = 0.60
    brief_satisfaction: bool = False
    budget_hard_stop: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_threshold": self.evidence_threshold,
            "hypothesis_coverage": self.hypothesis_coverage,
            "confidence_gate": self.confidence_gate,
            "brief_satisfaction": self.brief_satisfaction,
            "budget_hard_stop": self.budget_hard_stop,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            evidence_threshold=data.get("evidence_threshold", 3),
            hypothesis_coverage=data.get("hypothesis_coverage", 2),
            confidence_gate=data.get("confidence_gate", 0.60),
            brief_satisfaction=data.get("brief_satisfaction", False),
            budget_hard_stop=data.get("budget_hard_stop", False),
        )


@dataclass
class BudgetState:
    """V4 harness budget tracker — distinct from V3 orchestrator.BudgetState."""

    total_tokens: int = 0
    total_cost: float = 0.0
    total_tool_calls: int = 0
    turns_used: int = 0
    max_tokens: int = 100_000
    max_cost: float = 0.50
    max_tool_calls: int = 30
    hard_stop: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "total_cost": self.total_cost,
            "total_tool_calls": self.total_tool_calls,
            "turns_used": self.turns_used,
            "max_tokens": self.max_tokens,
            "max_cost": self.max_cost,
            "max_tool_calls": self.max_tool_calls,
            "hard_stop": self.hard_stop,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            total_tokens=data.get("total_tokens", 0),
            total_cost=data.get("total_cost", 0.0),
            total_tool_calls=data.get("total_tool_calls", 0),
            turns_used=data.get("turns_used", 0),
            max_tokens=data.get("max_tokens", 100_000),
            max_cost=data.get("max_cost", 0.50),
            max_tool_calls=data.get("max_tool_calls", 30),
            hard_stop=data.get("hard_stop", False),
        )

    @property
    def budget_exceeded(self) -> bool:
        return (
            self.total_tokens >= self.max_tokens
            or self.total_cost >= self.max_cost
            or self.total_tool_calls >= self.max_tool_calls
        )


@dataclass
class InvestigationBrief:
    """Stage 2 planning output — hypotheses, plan, and completion criteria."""

    hypotheses: list[Hypothesis] = field(default_factory=list)
    examination_plan: list[ExaminationStep] = field(default_factory=list)
    success_criteria: CompletionCriteria = field(default_factory=CompletionCriteria)
    strategy: str = ""
    max_effort: int = 18

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "examination_plan": [item.to_dict() for item in self.examination_plan],
            "success_criteria": self.success_criteria.to_dict(),
            "strategy": self.strategy,
            "max_effort": self.max_effort,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        criteria_data = data.get("success_criteria", {})
        return cls(
            hypotheses=[Hypothesis.from_dict(item) for item in data.get("hypotheses", [])],
            examination_plan=[
                ExaminationStep.from_dict(item) for item in data.get("examination_plan", [])
            ],
            success_criteria=CompletionCriteria.from_dict(criteria_data),
            strategy=data.get("strategy", ""),
            max_effort=data.get("max_effort", 18),
        )


@dataclass
class InvestigationState:
    """Harness-managed state across agent pipeline stages."""

    current_stage: int = 2
    candidates_examined: int = 0
    candidates_total: int = 0
    hypotheses_tested: int = 0
    hypotheses_confirmed: int = 0
    evidence_quotes_collected: int = 0
    re_plan_count: int = 0
    budget_used: BudgetState = field(default_factory=BudgetState)
    brief: InvestigationBrief | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_stage": self.current_stage,
            "candidates_examined": self.candidates_examined,
            "candidates_total": self.candidates_total,
            "hypotheses_tested": self.hypotheses_tested,
            "hypotheses_confirmed": self.hypotheses_confirmed,
            "evidence_quotes_collected": self.evidence_quotes_collected,
            "re_plan_count": self.re_plan_count,
            "budget_used": self.budget_used.to_dict(),
            "brief": self.brief.to_dict() if self.brief is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        brief_data = data.get("brief")
        return cls(
            current_stage=data.get("current_stage", 2),
            candidates_examined=data.get("candidates_examined", 0),
            candidates_total=data.get("candidates_total", 0),
            hypotheses_tested=data.get("hypotheses_tested", 0),
            hypotheses_confirmed=data.get("hypotheses_confirmed", 0),
            evidence_quotes_collected=data.get("evidence_quotes_collected", 0),
            re_plan_count=data.get("re_plan_count", 0),
            budget_used=BudgetState.from_dict(data.get("budget_used", {})),
            brief=InvestigationBrief.from_dict(brief_data) if brief_data is not None else None,
        )
