"""Brief validation for V4 investigation harness (ADR §Q6).

Validates InvestigationBrief structure and provides default brief fallback
when LLM produces invalid plans.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from commit_investigator.models.candidates import CandidateSet
from commit_investigator.models.investigation import (
    CompletionCriteria,
    ExaminationStep,
    Hypothesis,
    InvestigationBrief,
)

MIN_HYPOTHESES = 2
MAX_EFFORT_LIMIT = 25
MIN_STRATEGY_LENGTH = 20
FALSIFIABLE_MARKERS = ("because", "if", "caused by", "introduced by", "when")


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of brief validation."""

    valid: bool
    errors: list[str]


def _check_hypotheses(brief: InvestigationBrief) -> list[str]:
    errors: list[str] = []
    if len(brief.hypotheses) < MIN_HYPOTHESES:
        errors.append(f"Need >= {MIN_HYPOTHESES} hypotheses, got {len(brief.hypotheses)}")
    for hyp in brief.hypotheses:
        statement_lower = hyp.statement.lower()
        if not any(marker in statement_lower for marker in FALSIFIABLE_MARKERS):
            errors.append(f"Hypothesis '{hyp.id}' lacks falsifiable claim (no causal marker)")
    return errors


def _check_structure(brief: InvestigationBrief) -> list[str]:
    errors: list[str] = []
    if not brief.examination_plan:
        errors.append("examination_plan is empty")
    if not brief.strategy or len(brief.strategy) < MIN_STRATEGY_LENGTH:
        errors.append(f"strategy too short (need >= {MIN_STRATEGY_LENGTH} chars)")
    if brief.max_effort > MAX_EFFORT_LIMIT:
        errors.append(f"max_effort {brief.max_effort} exceeds limit {MAX_EFFORT_LIMIT}")
    return errors


class BriefValidator:
    """Validates InvestigationBrief and provides default fallback."""

    def validate(self, brief: InvestigationBrief) -> ValidationResult:
        """Check brief against structural requirements."""
        errors = _check_hypotheses(brief) + _check_structure(brief)
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def default_brief(self, candidate_set: CandidateSet) -> InvestigationBrief:
        """Build a default brief when LLM fails to produce a valid one.

        Examines top 10 candidates by retrieval rank with generic hypothesis.
        """
        top_commits = sorted(candidate_set.commits, key=lambda c: c.rank)[:10]
        plan = [
            ExaminationStep(
                commit_id=commit.commit_id,
                look_for="Changes that could introduce the reported bug",
            )
            for commit in top_commits
        ]
        return InvestigationBrief(
            hypotheses=[
                Hypothesis(
                    id="h1",
                    statement="Bug was introduced by a commit modifying extracted files because those files are referenced in the bug report",
                ),
                Hypothesis(
                    id="h2",
                    statement="Bug was caused by a refactoring commit that changed behavior when modifying related code paths",
                ),
            ],
            examination_plan=plan,
            success_criteria=CompletionCriteria(),
            strategy="Examine top-ranked retrieval candidates in order, looking for behavioral changes matching the bug report",
            max_effort=18,
        )
