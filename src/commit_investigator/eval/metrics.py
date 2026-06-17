"""Evaluation metrics: Hit@k, MRR, and 5-stage funnel computation.

Pure functions operating on pipeline outputs. All require ground_truth_sha
(eval-only — never enters investigation prompts).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from commit_investigator.harness.result import Suspect
from commit_investigator.harness.scoped_runner import ToolCallRecord
from commit_investigator.models.candidates import CandidateSet
from commit_investigator.narrowing.models import ScoredShortlist, TriageResult


@dataclass
class FunnelMetrics:
    """5-stage funnel metrics for a single investigation."""

    recall_100: bool | None = None
    pre_score_recall_15: bool | None = None
    triage_recall_7: bool | None = None
    exam_recall: bool | None = None
    hit_at_5: bool | None = None
    mrr: float | None = None
    hit_rank: int | None = None
    phase2b_triggered: bool = False


def compute_hit_at_k(
    ground_truth_sha: str,
    suspects: list[Suspect],
    k: int = 5,
) -> tuple[bool, int | None]:
    """Check if GT SHA appears in top-k suspects.

    Returns (hit, rank_or_None). Rank is 1-based.
    """
    target = ground_truth_sha.lower()
    for i, s in enumerate(suspects[:k]):
        if _sha_match(s.commit_id, target):
            return True, i + 1
    return False, None


def compute_mrr(
    ground_truth_sha: str,
    suspects: list[Suspect],
) -> float:
    """Compute Mean Reciprocal Rank (single query). 0.0 if not found."""
    target = ground_truth_sha.lower()
    for i, s in enumerate(suspects):
        if _sha_match(s.commit_id, target):
            return 1.0 / (i + 1)
    return 0.0


def compute_funnel(
    ground_truth_sha: str,
    candidate_set: CandidateSet,
    shortlist: ScoredShortlist,
    triage: TriageResult,
    tool_trace: list[ToolCallRecord],
    suspects: list[Suspect],
    phase2b_triggered: bool = False,
) -> FunnelMetrics:
    """Compute all 5 funnel stages + MRR from pipeline outputs."""
    target = ground_truth_sha.lower()

    recall_100 = _sha_in_list(target, [c.commit_id for c in candidate_set.commits])
    if not recall_100:
        return FunnelMetrics(recall_100=False, phase2b_triggered=phase2b_triggered)

    pre_score_recall_15 = _sha_in_list(
        target, [c.commit_id for c in shortlist.candidates]
    )
    if not pre_score_recall_15:
        return FunnelMetrics(
            recall_100=True, pre_score_recall_15=False,
            phase2b_triggered=phase2b_triggered,
        )

    triage_shas = triage.must_examine_shas + triage.watchlist_shas
    triage_recall_7 = _sha_in_list(target, triage_shas)

    diff_shas = [
        tc.args.get("commit_id", "")
        for tc in tool_trace
        if tc.tool == "get_commit_diff"
    ]
    exam_recall = _sha_in_list(target, diff_shas)

    hit, rank = compute_hit_at_k(ground_truth_sha, suspects)
    mrr = compute_mrr(ground_truth_sha, suspects)

    return FunnelMetrics(
        recall_100=True,
        pre_score_recall_15=True,
        triage_recall_7=triage_recall_7,
        exam_recall=exam_recall,
        hit_at_5=hit,
        mrr=mrr,
        hit_rank=rank,
        phase2b_triggered=phase2b_triggered,
    )


def _sha_match(sha: str, target_lower: str) -> bool:
    """Case-insensitive SHA match (full or prefix >=12 chars)."""
    sha_l = sha.lower()
    if sha_l == target_lower:
        return True
    min_len = min(len(sha_l), len(target_lower), 12)
    return min_len >= 12 and sha_l[:12] == target_lower[:12]


def _sha_in_list(target_lower: str, sha_list: list[str]) -> bool:
    return any(_sha_match(sha, target_lower) for sha in sha_list)
