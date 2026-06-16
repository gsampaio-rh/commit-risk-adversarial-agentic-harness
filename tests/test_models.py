"""Unit tests for V4 core model dataclasses."""

from __future__ import annotations

import json
from typing import Any

import pytest

from commit_investigator.models import (
    BudgetState,
    CandidateCommit,
    CandidateSet,
    CompletionCheck,
    CompletionCriteria,
    EliminationRecord,
    EvidenceRecord,
    ExaminationStep,
    Hypothesis,
    HypothesisRecord,
    InvestigationBrief,
    InvestigationState,
    InvestigationTrace,
    OutcomeRecord,
    StrategyRecord,
    TraceToolCall,
    TurnRecord,
)


def _round_trip(model: Any) -> dict[str, Any]:
    """Serialize to JSON and back; return final dict for identity check."""
    json_str = json.dumps(model.to_dict())
    restored = type(model).from_dict(json.loads(json_str))
    return restored.to_dict()


class TestCandidateModels:
    def test_empty_candidate_set_round_trip(self) -> None:
        candidate_set = CandidateSet(
            commits=[],
            retrieval_metadata={"strategy": "combined"},
            temporal_bound="abc123~1",
        )
        assert _round_trip(candidate_set) == candidate_set.to_dict()

    def test_candidate_commit_fields(self) -> None:
        commit = CandidateCommit(
            commit_id="a" * 40,
            rank=1,
            retrieval_signal="file_log",
            summary="Fix null pointer",
            files_changed=["src/Foo.java"],
            date="2024-01-01",
        )
        restored = CandidateCommit.from_dict(_round_trip(commit))
        assert restored.commit_id == commit.commit_id
        assert restored.rank == 1
        assert restored.files_changed == ["src/Foo.java"]


class TestInvestigationDefaults:
    def test_completion_criteria_defaults(self) -> None:
        criteria = CompletionCriteria()
        assert criteria.evidence_threshold == 3
        assert criteria.hypothesis_coverage == 2
        assert criteria.confidence_gate == 0.60
        assert criteria.brief_satisfaction is False
        assert criteria.budget_hard_stop is False

    def test_investigation_brief_max_effort_default(self) -> None:
        brief = InvestigationBrief()
        assert brief.max_effort == 18

    def test_budget_state_defaults(self) -> None:
        budget = BudgetState()
        assert budget.max_tool_calls == 30
        assert budget.max_tokens == 100_000
        assert budget.max_cost == 0.50
        assert budget.hard_stop is False

    def test_completion_criteria_custom_values(self) -> None:
        criteria = CompletionCriteria.from_dict(
            {
                "evidence_threshold": 5,
                "hypothesis_coverage": 4,
                "confidence_gate": 0.80,
                "brief_satisfaction": True,
                "budget_hard_stop": True,
            }
        )
        assert criteria.evidence_threshold == 5
        assert criteria.hypothesis_coverage == 4
        assert criteria.confidence_gate == 0.80
        assert criteria.brief_satisfaction is True
        assert criteria.budget_hard_stop is True


class TestRoundTripSerialization:
    @pytest.mark.parametrize(
        "factory",
        [
            lambda: CandidateSet(
                commits=[
                    CandidateCommit(
                        commit_id="def456",
                        rank=2,
                        retrieval_signal="keyword_grep",
                        summary="Remove guard",
                        files_changed=["Parser.java"],
                        date="2024-02-01",
                    )
                ],
                retrieval_metadata={"recall_estimate": 0.45},
                temporal_bound="abc123~1",
            ),
            lambda: InvestigationBrief(
                hypotheses=[
                    Hypothesis(id="h1", statement="NPE caused by removed null guard"),
                    Hypothesis(id="h2", statement="Race in async handler"),
                ],
                examination_plan=[
                    ExaminationStep(
                        commit_id="def456",
                        file_path=None,
                        look_for="deleted null check",
                    )
                ],
                success_criteria=CompletionCriteria(),
                strategy="blame-chain analysis on Parser.java",
                max_effort=18,
            ),
            lambda: InvestigationState(
                current_stage=3,
                candidates_examined=5,
                candidates_total=72,
                hypotheses_tested=2,
                hypotheses_confirmed=1,
                evidence_quotes_collected=3,
                re_plan_count=0,
                budget_used=BudgetState(total_tool_calls=4),
                brief=None,
            ),
            lambda: InvestigationTrace(
                trace_id="550e8400-e29b-41d4-a716-446655440000",
                issue_key="GROOVY-8298",
                run_id="2026-06-16T12:00:00Z",
                temporal_bound="abc123~1",
                candidate_set_size=72,
                retrieval_recall_100=True,
                hypotheses=[
                    HypothesisRecord(
                        id="h1",
                        statement="NPE caused by removed null guard",
                        status="confirmed",
                        reason="Diff shows deleted if-block",
                        stage=2,
                        turn=None,
                    )
                ],
                candidates_examined=["def456"],
                candidates_eliminated=[
                    EliminationRecord(
                        commit_id="aaa111",
                        reason="No file overlap",
                        turn=1,
                        hypothesis_id=None,
                    )
                ],
                evidence_collected=[
                    EvidenceRecord(
                        commit_id="def456",
                        quote="- if (token != null) {",
                        grounded=None,
                        hypothesis_id="h1",
                        turn=1,
                    )
                ],
                strategy_decisions=[
                    StrategyRecord(
                        decision="examine rank-3 candidate first",
                        rationale="file path match",
                        stage=3,
                        turn=1,
                        alternatives_considered=["rank-1", "rank-2"],
                    )
                ],
                examination_turns=[
                    TurnRecord(
                        turn=1,
                        tool_calls=[
                            TraceToolCall(
                                tool="get_commit_diff",
                                args={"commit_id": "def456"},
                                summary="Removed null check",
                            )
                        ],
                        hypothesis_updates=["h1"],
                        completion_check=CompletionCheck(
                            evidence_met=True,
                            coverage_met=True,
                            confidence_met=True,
                            brief_satisfied=True,
                        ),
                    )
                ],
                stage_timings={"planning": 4200.0, "examination": 45000.0},
                outcome=OutcomeRecord(
                    suspect_count=3,
                    top_confidence=0.85,
                    degraded=False,
                    degraded_reason=None,
                    hit_at_5=True,
                    mrr=1.0,
                ),
            ),
        ],
        ids=["CandidateSet", "InvestigationBrief", "InvestigationState", "InvestigationTrace"],
    )
    def test_top_level_round_trip(self, factory: Any) -> None:
        model = factory()
        assert _round_trip(model) == model.to_dict()

    @pytest.mark.parametrize(
        "factory",
        [
            lambda: Hypothesis(id="h1", statement="because X caused Y"),
            lambda: ExaminationStep(file_path="Foo.java", look_for="NPE"),
            lambda: CompletionCriteria(),
            lambda: BudgetState(hard_stop=True, total_tool_calls=30),
            lambda: HypothesisRecord(
                id="h1", statement="s", status="formed", reason="r", stage=2, turn=3
            ),
            lambda: EliminationRecord(
                commit_id="abc", reason="r", turn=1, hypothesis_id="h1"
            ),
            lambda: EvidenceRecord(
                commit_id="abc", quote="q", turn=1, grounded=True, hypothesis_id="h1"
            ),
            lambda: StrategyRecord(
                decision="d", rationale="r", stage=3, turn=2, alternatives_considered=["a"]
            ),
            lambda: TurnRecord(turn=1, hypothesis_updates=["h1"]),
            lambda: OutcomeRecord(
                suspect_count=0,
                top_confidence=0.0,
                degraded=True,
                degraded_reason="no_confirmed_hypotheses",
            ),
        ],
        ids=[
            "Hypothesis",
            "ExaminationStep",
            "CompletionCriteria",
            "BudgetState",
            "HypothesisRecord",
            "EliminationRecord",
            "EvidenceRecord",
            "StrategyRecord",
            "TurnRecord",
            "OutcomeRecord",
        ],
    )
    def test_nested_type_round_trip(self, factory: Any) -> None:
        model = factory()
        assert _round_trip(model) == model.to_dict()


class TestTraceNullableFields:
    def test_trace_preserves_null_optional_fields(self) -> None:
        trace = InvestigationTrace(
            trace_id="id",
            issue_key="KEY-1",
            run_id="run-1",
            temporal_bound="bound~1",
            candidate_set_size=0,
            retrieval_recall_100=None,
            hypotheses=[
                HypothesisRecord(
                    id="h1",
                    statement="s",
                    status="formed",
                    reason="r",
                    stage=2,
                    turn=None,
                )
            ],
            candidates_eliminated=[
                EliminationRecord(commit_id="c1", reason="r", turn=1, hypothesis_id=None)
            ],
            evidence_collected=[
                EvidenceRecord(
                    commit_id="c1",
                    quote="q",
                    turn=1,
                    grounded=None,
                    hypothesis_id=None,
                )
            ],
            strategy_decisions=[
                StrategyRecord(decision="d", rationale="r", stage=3, turn=None)
            ],
            outcome=OutcomeRecord(
                suspect_count=0,
                top_confidence=0.0,
                degraded=True,
                degraded_reason=None,
                hit_at_5=None,
                mrr=None,
            ),
        )
        restored_dict = _round_trip(trace)
        assert restored_dict["retrieval_recall_100"] is None
        assert restored_dict["hypotheses"][0]["turn"] is None
        assert restored_dict["candidates_eliminated"][0]["hypothesis_id"] is None
        assert restored_dict["evidence_collected"][0]["grounded"] is None
        assert restored_dict["strategy_decisions"][0]["turn"] is None
        assert restored_dict["outcome"]["degraded_reason"] is None
        assert restored_dict["outcome"]["hit_at_5"] is None
        assert restored_dict["outcome"]["mrr"] is None


class TestValidation:
    def test_hypothesis_record_rejects_invalid_status(self) -> None:
        with pytest.raises(ValueError, match="Invalid hypothesis status"):
            HypothesisRecord(
                id="h1",
                statement="s",
                status="pending",
                reason="r",
                stage=2,
            )

        with pytest.raises(ValueError, match="Invalid hypothesis status"):
            HypothesisRecord.from_dict(
                {
                    "id": "h1",
                    "statement": "s",
                    "status": "pending",
                    "reason": "r",
                    "stage": 2,
                }
            )

    def test_outcome_record_rejects_invalid_degraded_reason(self) -> None:
        with pytest.raises(ValueError, match="Invalid degraded_reason"):
            OutcomeRecord(
                suspect_count=0,
                top_confidence=0.0,
                degraded=True,
                degraded_reason="unknown",
            )
