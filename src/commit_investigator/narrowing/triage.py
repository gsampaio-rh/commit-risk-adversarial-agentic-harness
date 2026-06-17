"""Phase 1b: Deterministic triage — assign tiers from pre-score rank.

Post-gate decision: deterministic top-7 achieves TriageRecall@7=1.00 on
n=20 ApacheJIT eval cases. No LLM call needed.

Reintroduction trigger: TriageRecall@7 < 0.80 on dataset n>=50.
"""

from __future__ import annotations

from commit_investigator.narrowing.models import (
    ScoredCandidate,
    ScoredShortlist,
    TriagedCandidate,
    TriageResult,
    TriageTier,
)

MUST_EXAMINE_SIZE = 3
WATCHLIST_SIZE = 4


def _build_rationale(candidate: ScoredCandidate, tier: TriageTier, tier_rank: int) -> str:
    """Generate a template-based rationale string (no LLM)."""
    tier_label = "must-examine" if tier == TriageTier.MUST_EXAMINE else "watchlist"
    parts = [f"Pre-score rank #{tier_rank} in {tier_label} tier"]

    reasons: list[str] = []
    if candidate.file_overlap > 0:
        reasons.append(f"file_overlap={candidate.file_overlap:.2f}")
    if candidate.signal_count > 1:
        reasons.append(f"{candidate.signal_count} retrieval signals")
    if reasons:
        parts.append(f" ({', '.join(reasons)})")

    return "".join(parts)


def assign_tiers(
    shortlist: ScoredShortlist,
    *,
    must_examine_size: int = MUST_EXAMINE_SIZE,
    watchlist_size: int = WATCHLIST_SIZE,
) -> TriageResult:
    """Deterministic tier assignment: top-K by pre_score.

    Args:
        shortlist: Sorted candidates from Phase 1a pre-scoring.
        must_examine_size: Number of must-examine slots (default: 3).
        watchlist_size: Number of watchlist slots (default: 4).

    Returns:
        TriageResult with must_examine and watchlist populated.
    """
    must_examine: list[TriagedCandidate] = []
    watchlist: list[TriagedCandidate] = []

    for i, sc in enumerate(shortlist.candidates):
        if i < must_examine_size:
            tier = TriageTier.MUST_EXAMINE
            tier_rank = i + 1
            rationale = _build_rationale(sc, tier, tier_rank)
            must_examine.append(_scored_to_triaged(sc, tier, tier_rank, rationale))
        elif i < must_examine_size + watchlist_size:
            tier = TriageTier.WATCHLIST
            tier_rank = i - must_examine_size + 1
            rationale = _build_rationale(sc, tier, tier_rank)
            watchlist.append(_scored_to_triaged(sc, tier, tier_rank, rationale))
        else:
            break

    return TriageResult(
        must_examine=must_examine,
        watchlist=watchlist,
        shortlist_size=shortlist.size,
        total_scored=shortlist.total_scored,
    )


def _scored_to_triaged(
    sc: ScoredCandidate,
    tier: TriageTier,
    tier_rank: int,
    rationale: str,
) -> TriagedCandidate:
    return TriagedCandidate(
        commit_id=sc.commit_id,
        tier=tier,
        tier_rank=tier_rank,
        pre_score=sc.pre_score,
        rationale=rationale,
        file_overlap=sc.file_overlap,
        signal_count=sc.signal_count,
        original_rank=sc.original_rank,
        summary=sc.summary,
        files_changed=sc.files_changed,
        date=sc.date,
        retrieval_signal=sc.retrieval_signal,
        diff_summary=sc.diff_summary,
    )
