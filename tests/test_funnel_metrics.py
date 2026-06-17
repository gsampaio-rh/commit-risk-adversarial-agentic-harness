"""Tests for eval metrics: Hit@k, MRR, 5-stage funnel, and trace extensions."""

from __future__ import annotations

import pytest

from commit_investigator.eval.metrics import (
    FunnelMetrics,
    compute_funnel,
    compute_hit_at_k,
    compute_mrr,
)
from commit_investigator.harness.result import Suspect
from commit_investigator.harness.scoped_runner import ToolCallRecord
from commit_investigator.models.candidates import CandidateCommit, CandidateSet
from commit_investigator.narrowing.models import (
    ScoredCandidate,
    ScoredShortlist,
    TriagedCandidate,
    TriageResult,
    TriageTier,
)

GT_SHA = "aaa111bbb222ccc333ddd444eee555fff666aaa1"
OTHER1 = "bbb222ccc333ddd444eee555fff666aaa111bbb2"
OTHER2 = "ccc333ddd444eee555fff666aaa111bbb222ccc3"
OTHER3 = "ddd444eee555fff666aaa111bbb222ccc333ddd4"
OTHER4 = "eee555fff666aaa111bbb222ccc333ddd444eee5"
OTHER5 = "fff666aaa111bbb222ccc333ddd444eee555fff6"


def _suspect(sha: str, confidence: float = 0.5) -> Suspect:
    return Suspect(commit_id=sha, confidence=confidence)


# ---------------------------------------------------------------------------
# compute_hit_at_k
# ---------------------------------------------------------------------------

class TestComputeHitAtK:
    def test_hit_at_rank_1(self):
        suspects = [_suspect(GT_SHA, 0.9), _suspect(OTHER1, 0.5)]
        hit, rank = compute_hit_at_k(GT_SHA, suspects)
        assert hit is True
        assert rank == 1

    def test_hit_at_rank_3(self):
        suspects = [_suspect(OTHER1), _suspect(OTHER2), _suspect(GT_SHA, 0.3)]
        hit, rank = compute_hit_at_k(GT_SHA, suspects, k=5)
        assert hit is True
        assert rank == 3

    def test_miss_when_gt_beyond_k(self):
        suspects = [_suspect(OTHER1), _suspect(OTHER2), _suspect(GT_SHA)]
        hit, rank = compute_hit_at_k(GT_SHA, suspects, k=2)
        assert hit is False
        assert rank is None

    def test_miss_when_not_present(self):
        suspects = [_suspect(OTHER1), _suspect(OTHER2)]
        hit, rank = compute_hit_at_k(GT_SHA, suspects)
        assert hit is False
        assert rank is None

    def test_empty_suspects(self):
        hit, rank = compute_hit_at_k(GT_SHA, [])
        assert hit is False
        assert rank is None

    def test_case_insensitive_match(self):
        suspects = [_suspect(GT_SHA.upper())]
        hit, rank = compute_hit_at_k(GT_SHA.lower(), suspects)
        assert hit is True


# ---------------------------------------------------------------------------
# compute_mrr
# ---------------------------------------------------------------------------

class TestComputeMRR:
    def test_rank_1_gives_1(self):
        suspects = [_suspect(GT_SHA)]
        assert compute_mrr(GT_SHA, suspects) == 1.0

    def test_rank_2_gives_0_5(self):
        suspects = [_suspect(OTHER1), _suspect(GT_SHA)]
        assert compute_mrr(GT_SHA, suspects) == pytest.approx(0.5)

    def test_rank_4_gives_0_25(self):
        suspects = [_suspect(OTHER1), _suspect(OTHER2), _suspect(OTHER3), _suspect(GT_SHA)]
        assert compute_mrr(GT_SHA, suspects) == pytest.approx(0.25)

    def test_not_found_gives_0(self):
        suspects = [_suspect(OTHER1)]
        assert compute_mrr(GT_SHA, suspects) == 0.0

    def test_empty_gives_0(self):
        assert compute_mrr(GT_SHA, []) == 0.0


# ---------------------------------------------------------------------------
# FunnelMetrics
# ---------------------------------------------------------------------------

class TestFunnelMetrics:
    def test_defaults(self):
        fm = FunnelMetrics()
        assert fm.recall_100 is None
        assert fm.phase2b_triggered is False
        assert fm.mrr is None

    def test_with_values(self):
        fm = FunnelMetrics(
            recall_100=True, pre_score_recall_15=True, triage_recall_7=True,
            exam_recall=True, hit_at_5=True, mrr=1.0, hit_rank=1,
            phase2b_triggered=True,
        )
        assert fm.hit_at_5 is True
        assert fm.hit_rank == 1


# ---------------------------------------------------------------------------
# compute_funnel
# ---------------------------------------------------------------------------

def _make_candidate_set(*shas: str) -> CandidateSet:
    return CandidateSet(
        commits=[
            CandidateCommit(
                commit_id=sha, rank=i + 1, retrieval_signal="test",
                summary="s", files_changed=["f.java"], date="2024-01-01",
            )
            for i, sha in enumerate(shas)
        ],
        temporal_bound="abc~1",
    )


def _make_shortlist(*shas: str) -> ScoredShortlist:
    return ScoredShortlist(
        candidates=[
            ScoredCandidate(
                commit_id=sha, original_rank=i + 1, pre_score=0.8 - i * 0.1,
                file_overlap=0.5, signal_count=2,
            )
            for i, sha in enumerate(shas)
        ],
        total_scored=100,
    )


def _make_triage(must: list[str], watch: list[str]) -> TriageResult:
    def _tc(sha, tier, rank):
        return TriagedCandidate(
            commit_id=sha, tier=tier, tier_rank=rank,
            pre_score=0.5, rationale="test",
        )
    return TriageResult(
        must_examine=[_tc(s, TriageTier.MUST_EXAMINE, i + 1) for i, s in enumerate(must)],
        watchlist=[_tc(s, TriageTier.WATCHLIST, i + 1) for i, s in enumerate(watch)],
    )


class TestComputeFunnel:
    def test_full_hit_at_rank_1(self):
        cs = _make_candidate_set(GT_SHA, OTHER1, OTHER2)
        sl = _make_shortlist(GT_SHA, OTHER1, OTHER2)
        tr = _make_triage([GT_SHA, OTHER1, OTHER2], [])
        tool_trace = [ToolCallRecord("get_commit_diff", {"commit_id": GT_SHA})]
        suspects = [_suspect(GT_SHA, 0.9), _suspect(OTHER1, 0.5)]

        fm = compute_funnel(GT_SHA, cs, sl, tr, tool_trace, suspects)
        assert fm.recall_100 is True
        assert fm.pre_score_recall_15 is True
        assert fm.triage_recall_7 is True
        assert fm.exam_recall is True
        assert fm.hit_at_5 is True
        assert fm.hit_rank == 1
        assert fm.mrr == 1.0

    def test_not_retrievable(self):
        """EC1: GT not in candidate set → all downstream None."""
        cs = _make_candidate_set(OTHER1, OTHER2)
        sl = _make_shortlist(OTHER1, OTHER2)
        tr = _make_triage([OTHER1], [OTHER2])

        fm = compute_funnel(GT_SHA, cs, sl, tr, [], [])
        assert fm.recall_100 is False
        assert fm.pre_score_recall_15 is None
        assert fm.triage_recall_7 is None
        assert fm.hit_at_5 is None

    def test_not_in_shortlist(self):
        cs = _make_candidate_set(GT_SHA, OTHER1)
        sl = _make_shortlist(OTHER1)  # GT dropped by pre-score
        tr = _make_triage([OTHER1], [])

        fm = compute_funnel(GT_SHA, cs, sl, tr, [], [])
        assert fm.recall_100 is True
        assert fm.pre_score_recall_15 is False
        assert fm.triage_recall_7 is None

    def test_in_triage_but_not_examined(self):
        cs = _make_candidate_set(GT_SHA, OTHER1)
        sl = _make_shortlist(GT_SHA, OTHER1)
        tr = _make_triage([OTHER1], [GT_SHA])
        tool_trace = [ToolCallRecord("get_commit_diff", {"commit_id": OTHER1})]

        fm = compute_funnel(GT_SHA, cs, sl, tr, tool_trace, [_suspect(OTHER1)])
        assert fm.triage_recall_7 is True
        assert fm.exam_recall is False
        assert fm.hit_at_5 is False

    def test_examined_but_not_in_suspects(self):
        """EC2: GT diffed but not ranked as suspect."""
        cs = _make_candidate_set(GT_SHA, OTHER1)
        sl = _make_shortlist(GT_SHA, OTHER1)
        tr = _make_triage([GT_SHA, OTHER1, OTHER2], [])
        tool_trace = [ToolCallRecord("get_commit_diff", {"commit_id": GT_SHA})]

        fm = compute_funnel(GT_SHA, cs, sl, tr, tool_trace, [_suspect(OTHER1)])
        assert fm.exam_recall is True
        assert fm.hit_at_5 is False

    def test_empty_suspects(self):
        """EC3: no suspects produced."""
        cs = _make_candidate_set(GT_SHA)
        sl = _make_shortlist(GT_SHA)
        tr = _make_triage([GT_SHA], [])

        fm = compute_funnel(GT_SHA, cs, sl, tr, [], [])
        assert fm.hit_at_5 is False
        assert fm.mrr == 0.0

    def test_phase2b_triggered_flag(self):
        cs = _make_candidate_set(GT_SHA)
        sl = _make_shortlist(GT_SHA)
        tr = _make_triage([GT_SHA], [])

        fm = compute_funnel(GT_SHA, cs, sl, tr, [], [], phase2b_triggered=True)
        assert fm.phase2b_triggered is True


# ---------------------------------------------------------------------------
# InvestigationTrace funnel fields + build_v42_trace
# ---------------------------------------------------------------------------

from commit_investigator.harness.trace_writer import (
    InvestigationTrace,
    build_v42_trace,
)


class TestTraceExtensions:
    def test_trace_has_funnel_fields(self):
        trace = InvestigationTrace(
            issue_key="TEST-1",
            pre_score_recall_15=True,
            triage_recall_7=True,
            exam_recall=False,
            phase2b_triggered=True,
        )
        assert trace.pre_score_recall_15 is True
        assert trace.triage_recall_7 is True
        assert trace.exam_recall is False
        assert trace.phase2b_triggered is True

    def test_trace_to_dict_includes_funnel(self):
        trace = InvestigationTrace(
            issue_key="TEST-1",
            pre_score_recall_15=True,
            triage_recall_7=False,
            exam_recall=None,
            phase2b_triggered=False,
        )
        d = trace.to_dict()
        assert d["pre_score_recall_15"] is True
        assert d["triage_recall_7"] is False
        assert d["exam_recall"] is None
        assert d["phase2b_triggered"] is False

    def test_trace_defaults_none(self):
        trace = InvestigationTrace()
        assert trace.pre_score_recall_15 is None
        assert trace.triage_recall_7 is None
        assert trace.exam_recall is None
        assert trace.phase2b_triggered is False


class TestBuildV42Trace:
    def test_populates_funnel_from_metrics(self):
        fm = FunnelMetrics(
            recall_100=True, pre_score_recall_15=True,
            triage_recall_7=True, exam_recall=True,
            hit_at_5=True, mrr=1.0, hit_rank=1,
            phase2b_triggered=False,
        )
        suspects = [{"commit_id": GT_SHA, "confidence": 0.9}]
        trace = build_v42_trace(
            issue_key="TEST-1", temporal_bound="abc~1",
            candidate_count=50, suspects=suspects,
            tool_trace=[], funnel=fm,
        )
        assert trace.retrieval_recall_100 is True
        assert trace.pre_score_recall_15 is True
        assert trace.triage_recall_7 is True
        assert trace.exam_recall is True
        assert trace.phase2b_triggered is False
        assert trace.outcome.hit_at_5 is True
        assert trace.outcome.mrr == 1.0

    def test_non_retrievable_trace(self):
        fm = FunnelMetrics(recall_100=False)
        trace = build_v42_trace(
            issue_key="TEST-2", temporal_bound="abc~1",
            candidate_count=100, suspects=[], tool_trace=[], funnel=fm,
        )
        assert trace.retrieval_recall_100 is False
        assert trace.pre_score_recall_15 is None
        assert trace.outcome.hit_at_5 is None
        assert trace.outcome.degraded is True
