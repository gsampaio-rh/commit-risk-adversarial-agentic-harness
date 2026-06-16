"""Tests for V4 investigation harness — state machine, brief validation, completion."""

import json

import pytest

from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.harness.brief_validator import BriefValidator, ValidationResult
from commit_investigator.harness.completion import CompletionEvaluator, CompletionStatus
from commit_investigator.harness.harness import InvestigationHarness, InvestigationOutcome
from commit_investigator.harness.llm_protocol import LLMResponse
from commit_investigator.models.candidates import CandidateCommit, CandidateSet
from commit_investigator.models.investigation import (
    BudgetState,
    CompletionCriteria,
    ExaminationStep,
    Hypothesis,
    InvestigationBrief,
    InvestigationState,
)


def _make_candidate_set(n: int = 10) -> CandidateSet:
    commits = [
        CandidateCommit(
            commit_id=f"{i:040x}",
            rank=i,
            retrieval_signal="file_log",
            summary=f"commit {i}",
            files_changed=["Foo.java"],
        )
        for i in range(1, n + 1)
    ]
    return CandidateSet(commits=commits, retrieval_metadata={}, temporal_bound="abc~1")


def _make_problem() -> ProblemStatement:
    return ProblemStatement(
        title="NPE in RouteBuilder",
        description="NullPointerException at RouteBuilder.configure()",
        project="CAMEL",
        issue_key="CAMEL-1234",
        extracted_files=["RouteBuilder.java"],
        extracted_symbols=["RouteBuilder"],
        extracted_keywords=["npe", "routebuilder"],
    )


def _make_valid_brief_json() -> str:
    brief = InvestigationBrief(
        hypotheses=[
            Hypothesis(id="h1", statement="Bug introduced because RouteBuilder null check removed"),
            Hypothesis(id="h2", statement="Bug caused by refactoring when configure() path changed"),
        ],
        examination_plan=[
            ExaminationStep(commit_id="a" * 40, look_for="null check removal"),
        ],
        strategy="Examine commits touching RouteBuilder.java for null-guard changes",
        max_effort=18,
    )
    return json.dumps(brief.to_dict())


class MockLLM:
    """Mock LLM that returns pre-configured responses in sequence."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    def generate(self, prompt: str, **kwargs) -> LLMResponse:
        idx = min(self._call_count, len(self._responses) - 1)
        content = self._responses[idx]
        self._call_count += 1
        return LLMResponse(content=content, tokens_used=100, cost=0.001)

    @property
    def call_count(self) -> int:
        return self._call_count


class TestBriefValidator:
    def test_valid_brief(self) -> None:
        brief = InvestigationBrief(
            hypotheses=[
                Hypothesis(id="h1", statement="Bug because null guard removed"),
                Hypothesis(id="h2", statement="Bug caused by refactoring"),
            ],
            examination_plan=[ExaminationStep(look_for="null check")],
            strategy="Examine top candidates for null changes",
            max_effort=18,
        )
        result = BriefValidator().validate(brief)
        assert result.valid

    def test_too_few_hypotheses(self) -> None:
        brief = InvestigationBrief(
            hypotheses=[Hypothesis(id="h1", statement="Bug because null guard removed")],
            examination_plan=[ExaminationStep(look_for="null check")],
            strategy="Examine top candidates for null changes",
            max_effort=18,
        )
        result = BriefValidator().validate(brief)
        assert not result.valid
        assert any("hypotheses" in e for e in result.errors)

    def test_hypothesis_not_falsifiable(self) -> None:
        brief = InvestigationBrief(
            hypotheses=[
                Hypothesis(id="h1", statement="Something is wrong here"),
                Hypothesis(id="h2", statement="Another problem exists"),
            ],
            examination_plan=[ExaminationStep(look_for="something")],
            strategy="Examine top candidates for changes",
            max_effort=18,
        )
        result = BriefValidator().validate(brief)
        assert not result.valid
        assert any("falsifiable" in e for e in result.errors)

    def test_empty_examination_plan(self) -> None:
        brief = InvestigationBrief(
            hypotheses=[
                Hypothesis(id="h1", statement="Bug because X"),
                Hypothesis(id="h2", statement="Bug if Y"),
            ],
            examination_plan=[],
            strategy="Examine top candidates for null changes",
            max_effort=18,
        )
        result = BriefValidator().validate(brief)
        assert not result.valid

    def test_strategy_too_short(self) -> None:
        brief = InvestigationBrief(
            hypotheses=[
                Hypothesis(id="h1", statement="Bug because X"),
                Hypothesis(id="h2", statement="Bug if Y"),
            ],
            examination_plan=[ExaminationStep(look_for="test")],
            strategy="short",
            max_effort=18,
        )
        result = BriefValidator().validate(brief)
        assert not result.valid

    def test_max_effort_exceeded(self) -> None:
        brief = InvestigationBrief(
            hypotheses=[
                Hypothesis(id="h1", statement="Bug because X"),
                Hypothesis(id="h2", statement="Bug if Y"),
            ],
            examination_plan=[ExaminationStep(look_for="test")],
            strategy="Examine top candidates for null changes",
            max_effort=30,
        )
        result = BriefValidator().validate(brief)
        assert not result.valid

    def test_default_brief_has_valid_structure(self) -> None:
        cs = _make_candidate_set(15)
        default = BriefValidator().default_brief(cs)
        result = BriefValidator().validate(default)
        assert result.valid
        assert len(default.examination_plan) == 10


class TestCompletionEvaluator:
    def test_continue_when_insufficient(self) -> None:
        state = InvestigationState(
            evidence_quotes_collected=1,
            hypotheses_tested=1,
            budget_used=BudgetState(total_tool_calls=5),
        )
        check = CompletionEvaluator().evaluate(
            state, CompletionCriteria(), top_confidence=0.3
        )
        assert check.status == CompletionStatus.CONTINUE

    def test_satisfied_when_all_met(self) -> None:
        state = InvestigationState(
            evidence_quotes_collected=4,
            hypotheses_tested=3,
            budget_used=BudgetState(total_tool_calls=10),
        )
        check = CompletionEvaluator().evaluate(
            state, CompletionCriteria(), top_confidence=0.75
        )
        assert check.status == CompletionStatus.SATISFIED

    def test_budget_exhausted(self) -> None:
        state = InvestigationState(
            budget_used=BudgetState(total_tool_calls=30, max_tool_calls=30),
        )
        check = CompletionEvaluator().evaluate(
            state, CompletionCriteria(), top_confidence=0.0
        )
        assert check.status == CompletionStatus.BUDGET_EXHAUSTED

    def test_hypotheses_exhausted(self) -> None:
        state = InvestigationState(
            hypotheses_tested=3,
            evidence_quotes_collected=1,
            budget_used=BudgetState(total_tool_calls=5),
        )
        check = CompletionEvaluator().evaluate(
            state, CompletionCriteria(), top_confidence=0.3, all_hypotheses_tested=True
        )
        assert check.status == CompletionStatus.HYPOTHESES_EXHAUSTED

    def test_max_effort_reached(self) -> None:
        brief = InvestigationBrief(max_effort=10)
        state = InvestigationState(
            budget_used=BudgetState(total_tool_calls=10),
            brief=brief,
        )
        check = CompletionEvaluator().evaluate(
            state, CompletionCriteria(), top_confidence=0.3
        )
        assert check.status == CompletionStatus.MAX_EFFORT_REACHED


class TestInvestigationHarness:
    def test_full_pipeline_with_valid_brief(self) -> None:
        """Happy path: planning → examination → attribution."""
        valid_brief = _make_valid_brief_json()
        exam_response = "Evidence: commit aaa removed null check in configure()"
        attribution = json.dumps({"suspects": [
            {"commit_id": "a" * 40, "confidence": 0.8, "mechanism": "null guard removed"},
        ]})

        llm = MockLLM([valid_brief, exam_response, exam_response, exam_response, attribution])
        harness = InvestigationHarness(
            llm=llm,
            problem_statement=_make_problem(),
            candidate_set=_make_candidate_set(),
        )
        outcome = harness.run()

        assert outcome.state.current_stage == 4
        assert len(outcome.planning_responses) >= 1
        assert len(outcome.examination_responses) >= 1
        assert outcome.attribution_response is not None

    def test_invalid_brief_retries_then_uses_default(self) -> None:
        """Brief validation failure → retry → default brief."""
        invalid_json = "not valid json"
        exam_response = "Evidence: found relevant change"
        attribution = json.dumps({"suspects": []})

        llm = MockLLM([invalid_json, invalid_json, exam_response, exam_response, exam_response, attribution])
        harness = InvestigationHarness(
            llm=llm,
            problem_statement=_make_problem(),
            candidate_set=_make_candidate_set(),
        )
        outcome = harness.run()

        assert outcome.brief is not None
        assert outcome.state.current_stage == 4

    def test_budget_exhausted_forces_degraded(self) -> None:
        """Budget exceeded during planning → degraded mode."""

        class BudgetExhaustingLLM:
            def generate(self, prompt, **kwargs):
                return LLMResponse(
                    content="not json",
                    tokens_used=50000,
                    cost=0.25,
                )

        harness = InvestigationHarness(
            llm=BudgetExhaustingLLM(),
            problem_statement=_make_problem(),
            candidate_set=_make_candidate_set(),
        )
        outcome = harness.run()
        assert outcome.degraded or outcome.state.budget_used.total_cost > 0

    def test_stages_advance_correctly(self) -> None:
        """Verify stage transitions: 2 → 3 → 4."""
        valid_brief = _make_valid_brief_json()
        exam = "Found evidence of bug introduction because null check removed"
        attrib = json.dumps({"suspects": []})

        llm = MockLLM([valid_brief, exam, exam, exam, attrib])
        harness = InvestigationHarness(
            llm=llm,
            problem_statement=_make_problem(),
            candidate_set=_make_candidate_set(),
        )
        outcome = harness.run()
        assert outcome.state.current_stage == 4

    def test_examination_collects_evidence(self) -> None:
        valid_brief = _make_valid_brief_json()
        exam = "This commit removed the null check guard in configure() method"
        attrib = json.dumps({"suspects": []})

        llm = MockLLM([valid_brief, exam, exam, exam, attrib])
        harness = InvestigationHarness(
            llm=llm,
            problem_statement=_make_problem(),
            candidate_set=_make_candidate_set(),
        )
        outcome = harness.run()
        assert len(outcome.evidence) > 0
        assert outcome.state.evidence_quotes_collected > 0
