"""Completion criteria evaluator for the investigation harness.

After each Stage 3 turn, the harness evaluates whether the investigation
should continue, advance to attribution, or loop back to planning.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from commit_investigator.models.investigation import (
    BudgetState,
    CompletionCriteria,
    InvestigationState,
)


class CompletionStatus(Enum):
    """Outcome of completion evaluation after a Stage 3 turn."""

    CONTINUE = auto()
    SATISFIED = auto()
    BUDGET_EXHAUSTED = auto()
    HYPOTHESES_EXHAUSTED = auto()
    MAX_EFFORT_REACHED = auto()


@dataclass(frozen=True)
class CompletionCheck:
    """Detailed result of a completion criteria evaluation."""

    status: CompletionStatus
    evidence_met: bool
    coverage_met: bool
    confidence_met: bool
    brief_satisfied: bool
    budget_exceeded: bool

    def to_dict(self) -> dict:
        return {
            "status": self.status.name,
            "evidence_met": self.evidence_met,
            "coverage_met": self.coverage_met,
            "confidence_met": self.confidence_met,
            "brief_satisfied": self.brief_satisfied,
            "budget_exceeded": self.budget_exceeded,
        }


class CompletionEvaluator:
    """Evaluates completion criteria after each examination turn."""

    def evaluate(
        self,
        state: InvestigationState,
        criteria: CompletionCriteria,
        top_confidence: float = 0.0,
        all_hypotheses_tested: bool = False,
    ) -> CompletionCheck:
        """Check completion criteria against current investigation state.

        Args:
            state: Current harness-managed state.
            criteria: Completion thresholds from the InvestigationBrief.
            top_confidence: Highest confidence score among current suspects.
            all_hypotheses_tested: Whether all brief hypotheses have been tested.
        """
        budget_exceeded = state.budget_used.budget_exceeded

        evidence_met = state.evidence_quotes_collected >= criteria.evidence_threshold
        coverage_met = state.hypotheses_tested >= criteria.hypothesis_coverage
        confidence_met = top_confidence >= criteria.confidence_gate
        brief_satisfied = evidence_met and coverage_met and confidence_met

        if budget_exceeded:
            return CompletionCheck(
                status=CompletionStatus.BUDGET_EXHAUSTED,
                evidence_met=evidence_met,
                coverage_met=coverage_met,
                confidence_met=confidence_met,
                brief_satisfied=brief_satisfied,
                budget_exceeded=True,
            )

        if brief_satisfied:
            return CompletionCheck(
                status=CompletionStatus.SATISFIED,
                evidence_met=True,
                coverage_met=True,
                confidence_met=True,
                brief_satisfied=True,
                budget_exceeded=False,
            )

        effort_used = state.budget_used.total_tool_calls
        max_effort = 18
        if state.brief and state.brief.max_effort:
            max_effort = state.brief.max_effort

        if effort_used >= max_effort:
            return CompletionCheck(
                status=CompletionStatus.MAX_EFFORT_REACHED,
                evidence_met=evidence_met,
                coverage_met=coverage_met,
                confidence_met=confidence_met,
                brief_satisfied=brief_satisfied,
                budget_exceeded=False,
            )

        if all_hypotheses_tested and not brief_satisfied:
            return CompletionCheck(
                status=CompletionStatus.HYPOTHESES_EXHAUSTED,
                evidence_met=evidence_met,
                coverage_met=coverage_met,
                confidence_met=confidence_met,
                brief_satisfied=brief_satisfied,
                budget_exceeded=False,
            )

        return CompletionCheck(
            status=CompletionStatus.CONTINUE,
            evidence_met=evidence_met,
            coverage_met=coverage_met,
            confidence_met=confidence_met,
            brief_satisfied=brief_satisfied,
            budget_exceeded=False,
        )
