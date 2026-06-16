"""InvestigationHarness — V4 state machine orchestrating Stages 2→3→4.

The harness governs the LLM: it decides when to invoke, what context to
provide, when to stop, and when to re-plan. The LLM does not self-govern.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.governance import assemble_prompt, create_default_registry
from commit_investigator.governance.rules import HardRuleRegistry, RuleViolation
from commit_investigator.harness.brief_validator import BriefValidator, ValidationResult
from commit_investigator.harness.completion import CompletionCheck, CompletionEvaluator, CompletionStatus
from commit_investigator.harness.llm_protocol import LLMProvider, LLMResponse
from commit_investigator.models.candidates import CandidateSet
from commit_investigator.models.investigation import (
    BudgetState,
    InvestigationBrief,
    InvestigationState,
)

logger = logging.getLogger(__name__)

MAX_BRIEF_RETRIES = 1
MAX_REPLANS = 2


@dataclass
class ExaminationTurn:
    """Record of a single examination turn for trace population."""

    turn: int
    response_summary: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    completion_check: dict[str, Any] = field(default_factory=dict)


@dataclass
class InvestigationOutcome:
    """Final result of a harness-governed investigation."""

    state: InvestigationState
    brief: InvestigationBrief | None
    suspects: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    degraded: bool = False
    degraded_reason: str | None = None
    planning_responses: list[LLMResponse] = field(default_factory=list)
    examination_responses: list[LLMResponse] = field(default_factory=list)
    examination_turns: list[ExaminationTurn] = field(default_factory=list)
    attribution_response: LLMResponse | None = None
    top_confidence: float = 0.0


class InvestigationHarness:
    """State machine governing the agent pipeline (Stages 2→3→4)."""

    def __init__(
        self,
        llm: LLMProvider,
        problem_statement: ProblemStatement,
        candidate_set: CandidateSet,
        *,
        registry: HardRuleRegistry | None = None,
    ) -> None:
        self._llm = llm
        self._problem = problem_statement
        self._candidates = candidate_set
        self._registry = registry or create_default_registry()
        self._validator = BriefValidator()
        self._evaluator = CompletionEvaluator()
        self._state = InvestigationState(
            current_stage=2,
            candidates_total=len(candidate_set.commits),
        )
        self._brief: InvestigationBrief | None = None
        self._evidence: list[str] = []
        self._replan_count = 0
        self._top_confidence = 0.0
        self._turn_count = 0
        self._validation_errors: list[str] = []

    @property
    def state(self) -> InvestigationState:
        return self._state

    def run(self) -> InvestigationOutcome:
        """Execute the full investigation pipeline: plan → examine → attribute."""
        outcome = InvestigationOutcome(state=self._state, brief=None)

        self._run_planning(outcome)
        if self._state.budget_used.budget_exceeded:
            return self._finalize_degraded(outcome, "budget_exhausted")

        self._run_examination(outcome)

        if self._state.budget_used.budget_exceeded:
            self._run_attribution(outcome)
            return self._finalize_degraded(outcome, "budget_exhausted")

        if self._replan_count >= MAX_REPLANS and not outcome.examination_turns:
            self._run_attribution(outcome)
            return self._finalize_degraded(outcome, "replan_limit")

        self._run_attribution(outcome)

        outcome.state = self._state
        outcome.brief = self._brief
        outcome.evidence = list(self._evidence)
        outcome.top_confidence = self._top_confidence
        return outcome

    def _run_planning(self, outcome: InvestigationOutcome) -> None:
        """Stage 2: produce and validate InvestigationBrief (ADR §Q6)."""
        self._state.current_stage = 2
        retries = 0

        while retries <= MAX_BRIEF_RETRIES:
            extra_context = ""
            if self._validation_errors:
                extra_context = (
                    "\n\n## Previous Validation Errors\n"
                    + "\n".join(f"- {e}" for e in self._validation_errors)
                    + "\n\nFix these issues in your response."
                )

            prompt = assemble_prompt(
                stage="planning",
                problem_statement=self._problem,
                candidate_set=self._candidates,
                investigation_state=self._state,
            )
            if extra_context:
                prompt += extra_context

            response = self._llm.generate(prompt)
            self._update_budget(response)
            outcome.planning_responses.append(response)

            brief = self._parse_brief(response.content)
            if brief is None:
                self._validation_errors = ["Response was not valid JSON"]
                retries += 1
                continue

            validation = self._validator.validate(brief)
            if validation.valid:
                self._brief = brief
                self._state.brief = brief
                self._validation_errors = []
                return

            self._validation_errors = validation.errors
            logger.info("Brief validation failed (retry %d): %s", retries, validation.errors)
            retries += 1

        self._brief = self._validator.default_brief(self._candidates)
        self._state.brief = self._brief
        self._validation_errors = []
        logger.info("Using default brief after %d failed retries", MAX_BRIEF_RETRIES)

    def _run_examination(self, outcome: InvestigationOutcome) -> None:
        """Stage 3: examine candidates, collect evidence, check completion."""
        self._state.current_stage = 3

        while True:
            self._turn_count += 1
            prompt = assemble_prompt(
                stage="examination",
                problem_statement=self._problem,
                candidate_set=self._candidates,
                investigation_state=self._state,
                brief=self._brief,
                evidence=self._evidence,
            )
            response = self._llm.generate(prompt)
            self._update_budget(response)
            outcome.examination_responses.append(response)

            self._process_examination_response(response)

            check = self._evaluator.evaluate(
                state=self._state,
                criteria=self._brief.success_criteria if self._brief else self._default_criteria(),
                top_confidence=self._top_confidence,
                all_hypotheses_tested=self._state.hypotheses_tested >= 2,
            )

            turn_record = ExaminationTurn(
                turn=self._turn_count,
                response_summary=response.content[:200] if response.content else "",
                tool_calls=response.tool_calls,
                completion_check=check.to_dict(),
            )
            outcome.examination_turns.append(turn_record)

            if check.status == CompletionStatus.SATISFIED:
                return
            if check.status == CompletionStatus.BUDGET_EXHAUSTED:
                return
            if check.status == CompletionStatus.MAX_EFFORT_REACHED:
                return
            if check.status == CompletionStatus.HYPOTHESES_EXHAUSTED:
                if self._replan_count < MAX_REPLANS:
                    self._replan_count += 1
                    self._state.re_plan_count = self._replan_count
                    self._run_planning(outcome)
                    continue
                outcome.degraded = True
                outcome.degraded_reason = "replan_limit"
                return

    def _run_attribution(self, outcome: InvestigationOutcome) -> None:
        """Stage 4: produce final suspect ranking."""
        self._state.current_stage = 4

        violations = self._check_hard_rules("attribution")
        if violations and not self._state.budget_used.budget_exceeded:
            logger.warning("Hard rule violations at attribution: %s", violations)

        prompt = assemble_prompt(
            stage="attribution",
            problem_statement=self._problem,
            candidate_set=self._candidates,
            investigation_state=self._state,
            brief=self._brief,
            evidence=self._evidence,
        )
        response = self._llm.generate(prompt)
        self._update_budget(response)
        outcome.attribution_response = response
        outcome.suspects = self._parse_suspects(response.content)

        if outcome.suspects:
            confidences = [s.get("confidence", 0.0) for s in outcome.suspects]
            self._top_confidence = max(confidences) if confidences else 0.0
            outcome.top_confidence = self._top_confidence

    def _process_examination_response(self, response: LLMResponse) -> None:
        """Extract evidence, confidence, and state updates from a turn."""
        self._state.candidates_examined += 1
        if response.has_tool_calls:
            self._state.budget_used.total_tool_calls += len(response.tool_calls)

        content = response.content
        if content and len(content) > 20:
            self._evidence.append(content[:500])
            self._state.evidence_quotes_collected += 1
            confidence = self._extract_confidence_from_response(content)
            if confidence > self._top_confidence:
                self._top_confidence = confidence

        if self._state.candidates_examined >= 2:
            self._state.hypotheses_tested = min(
                self._state.candidates_examined // 2, 3
            )

    def _extract_confidence_from_response(self, content: str) -> float:
        """Extract confidence signal from LLM examination output.

        Looks for patterns like "confidence: 0.X" or "confidence 0.X" in the
        response. Falls back to heuristic based on evidence language strength.
        """
        import re
        match = re.search(r"confidence[:\s]+([01]\.?\d*)", content.lower())
        if match:
            try:
                return min(float(match.group(1)), 1.0)
            except ValueError:
                pass
        strong_markers = ("confirms", "strongly suggests", "definitive", "root cause")
        if any(marker in content.lower() for marker in strong_markers):
            return 0.65
        return 0.0

    def _update_budget(self, response: LLMResponse) -> None:
        budget = self._state.budget_used
        budget.total_tokens += response.tokens_used
        budget.total_cost += response.cost
        budget.turns_used += 1

    def _check_hard_rules(self, stage: str) -> list[RuleViolation]:
        context = {
            "suspects": [],
            "candidate_set": self._candidates.commits,
            "top_confidence": self._top_confidence,
            "confidence_gate": 0.60,
            "budget_hard_stop": self._state.budget_used.budget_exceeded,
        }
        return self._registry.check_all(stage, context)

    def _parse_brief(self, content: str) -> InvestigationBrief | None:
        """Attempt to parse InvestigationBrief from LLM JSON output."""
        try:
            data = json.loads(content)
            return InvestigationBrief.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def _parse_suspects(self, content: str) -> list[dict[str, Any]]:
        """Attempt to parse suspects from attribution response."""
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "suspects" in data:
                return data["suspects"]
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    def _default_criteria(self) -> "CompletionCriteria":
        from commit_investigator.models.investigation import CompletionCriteria
        return CompletionCriteria()

    def _finalize_degraded(
        self, outcome: InvestigationOutcome, reason: str
    ) -> InvestigationOutcome:
        outcome.degraded = True
        outcome.degraded_reason = reason
        outcome.state = self._state
        outcome.brief = self._brief
        outcome.evidence = list(self._evidence)
        outcome.top_confidence = self._top_confidence
        return outcome
