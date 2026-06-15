"""Tests for V3 evaluation metrics."""

from unittest.mock import MagicMock

import pytest

from commit_investigator.pipeline.orchestrator import (
    BugAttributionReport,
    SuspectCommit,
    ToolCallRecord,
)
from commit_investigator.runners.eval_metrics import (
    AggregateEvalReport,
    AttributionEvalResult,
    _find_rank,
    aggregate_results,
    evaluate_attribution,
)


def _make_report(
    suspects: list[tuple[str, float]] | None = None,
    tool_trace_commits: list[str] | None = None,
) -> BugAttributionReport:
    if suspects is None:
        suspects = [("abc123def456", 0.8)]
    suspect_list = [
        SuspectCommit(
            commit_id=cid,
            rank=i + 1,
            confidence=conf,
            mechanism="test mechanism",
            evidence_quotes=["test quote here and more"],
        )
        for i, (cid, conf) in enumerate(suspects)
    ]
    trace = []
    for cid in (tool_trace_commits or []):
        trace.append(ToolCallRecord(tool="get_commit_diff", args={"commit_id": cid}, result="ok"))

    return BugAttributionReport(
        problem_title="test",
        problem_description="desc",
        suspects=suspect_list,
        reasoning_summary="test reasoning",
        tool_trace=trace,
        metadata={"tool_calls": len(trace), "tokens_used": 1000, "total_cost_usd": 0.01, "elapsed_ms": 500},
    )


class TestFindRank:
    def test_exact_match(self) -> None:
        assert _find_rank(["abc123", "def456"], "abc123") == 1

    def test_prefix_match(self) -> None:
        assert _find_rank(["abc123def456789"], "abc123def456") == 1

    def test_not_found(self) -> None:
        assert _find_rank(["abc123", "def456"], "xyz789") is None

    def test_second_position(self) -> None:
        assert _find_rank(["abc123def456", "xyz789012345"], "xyz789012345") == 2


class TestEvaluateAttribution:
    def test_hit_at_1(self) -> None:
        report = _make_report(suspects=[("abc123def456", 0.9)])
        result = evaluate_attribution(report, "abc123def456", "camel", "CAMEL-123")
        assert result.hit_at_1 is True
        assert result.hit_at_5 is True
        assert result.mrr == 1.0

    def test_hit_at_3_not_1(self) -> None:
        report = _make_report(suspects=[
            ("other1_commit", 0.9),
            ("other2_commit", 0.7),
            ("abc123def456", 0.5),
        ])
        result = evaluate_attribution(report, "abc123def456", "camel", "CAMEL-123")
        assert result.hit_at_1 is False
        assert result.hit_at_3 is True
        assert result.mrr == pytest.approx(1 / 3)

    def test_no_hit(self) -> None:
        report = _make_report(suspects=[("other_commit_1", 0.9)])
        result = evaluate_attribution(report, "abc123def456", "camel", "CAMEL-123")
        assert result.hit_at_1 is False
        assert result.hit_at_5 is False
        assert result.mrr == 0.0

    def test_retrieval_recall_true(self) -> None:
        report = _make_report(
            suspects=[("other_commit_1", 0.9)],
            tool_trace_commits=["abc123def456"],
        )
        result = evaluate_attribution(report, "abc123def456", "camel", "CAMEL-123")
        assert result.retrieval_recall is True

    def test_retrieval_recall_false(self) -> None:
        report = _make_report(
            suspects=[("other_commit_1", 0.9)],
            tool_trace_commits=["xyz789012345"],
        )
        result = evaluate_attribution(report, "abc123def456", "camel", "CAMEL-123")
        assert result.retrieval_recall is False

    def test_reuses_attached_evidence_scores(self) -> None:
        report = _make_report(suspects=[("abc123def456", 0.8)])
        report.metadata["evidence_scores"] = [{
            "commit_id": "abc123def456",
            "total_quotes": 2,
            "grounded_quotes": 1,
            "grounding_rate": 0.5,
        }]
        report.metadata["evidence_scoring_applied"] = True

        git = MagicMock()
        result = evaluate_attribution(
            report, "abc123def456", "camel", "CAMEL-123", git_provider=git,
        )

        git.get_diff.assert_not_called()
        assert result.evidence_grounding_rate == 0.5
        assert result.suspect_details[0]["grounding_rate"] == 0.5
        assert result.suspect_details[0]["grounded_quotes"] == 1

    def test_fallback_when_no_attached_scores(self) -> None:
        report = _make_report(suspects=[("abc123def456", 0.8)])
        git = MagicMock()
        git.get_diff.return_value = "some diff content here for matching"

        result = evaluate_attribution(
            report, "abc123def456", "camel", "CAMEL-123", git_provider=git,
        )

        git.get_diff.assert_called_once_with("abc123def456")
        assert result.evidence_grounding_rate >= 0.0
        assert len(result.suspect_details) == 1


class TestAggregateResults:
    def test_empty(self) -> None:
        agg = aggregate_results([])
        assert agg.total == 0

    def test_aggregate_metrics(self) -> None:
        r1 = AttributionEvalResult(
            bug_hash="a", project="p", issue_key="K-1",
            hit_at_1=True, hit_at_3=True, hit_at_5=True,
            mrr=1.0, retrieval_recall=True,
            evidence_grounding_rate=1.0,
            suspect_count=1, tool_calls=5, tokens_used=1000,
            cost_usd=0.01, elapsed_ms=500,
        )
        r2 = AttributionEvalResult(
            bug_hash="b", project="p", issue_key="K-2",
            hit_at_1=False, hit_at_3=False, hit_at_5=False,
            mrr=0.0, retrieval_recall=False,
            evidence_grounding_rate=0.0,
            suspect_count=0, tool_calls=3, tokens_used=500,
            cost_usd=0.005, elapsed_ms=300,
        )
        agg = aggregate_results([r1, r2])
        assert agg.total == 2
        assert agg.hit_at_1 == 0.5
        assert agg.hit_at_5 == 0.5
        assert agg.mrr == 0.5
        assert agg.total_cost_usd == pytest.approx(0.015)

    def test_to_dict(self) -> None:
        agg = aggregate_results([])
        d = agg.to_dict()
        assert "hit_at_5" in d
        assert "mrr" in d
