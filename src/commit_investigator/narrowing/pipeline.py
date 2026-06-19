"""Phase 1 pipeline: CandidateSet + ProblemStatement → TriageResult.

Composes pre-scoring (Phase 1a) and deterministic triage (Phase 1b)
into a single entry point. Zero LLM cost.
"""

from __future__ import annotations

from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.models.candidates import CandidateSet
from commit_investigator.narrowing.models import TriageResult
from commit_investigator.narrowing.scoring import (
    DEFAULT_SHORTLIST_SIZE,
    DEFAULT_WEIGHTS,
    compute_pre_scores,
)
from commit_investigator.narrowing.triage import (
    BLAME_ANCHOR_SLOTS,
    MUST_EXAMINE_SIZE,
    WATCHLIST_SIZE,
    assign_tiers,
)


def narrow_candidates(
    candidate_set: CandidateSet,
    problem: ProblemStatement,
    *,
    weights: dict[str, float] | None = None,
    shortlist_size: int = DEFAULT_SHORTLIST_SIZE,
    must_examine_size: int = MUST_EXAMINE_SIZE,
    watchlist_size: int = WATCHLIST_SIZE,
    blame_anchor_slots: int = BLAME_ANCHOR_SLOTS,
) -> TriageResult:
    """Run the full Phase 1 narrowing pipeline.

    Phase 1a: Score all candidates using pre-score formula, keep top-K.
    Phase 1b: Assign top-3 to must_examine, next-4 to watchlist,
              + blame-anchor up to blame_anchor_slots from positions 8+.

    Args:
        candidate_set: Full retrieval output (50-100 candidates).
        problem: Extracted problem statement with file/symbol hints.
        weights: Override pre-score weights (default: 0.5/0.3/0.2).
        shortlist_size: Pre-score shortlist size (default: 20).
        must_examine_size: Must-examine tier size (default: 3).
        watchlist_size: Watchlist tier size (default: 4).
        blame_anchor_slots: Extra watchlist slots for blame-sourced
            candidates outside standard window (default: 1).

    Returns:
        TriageResult with must_examine and watchlist candidates.
    """
    shortlist = compute_pre_scores(
        candidate_set,
        problem,
        weights=weights,
        shortlist_size=shortlist_size,
    )

    return assign_tiers(
        shortlist,
        must_examine_size=must_examine_size,
        watchlist_size=watchlist_size,
        blame_anchor_slots=blame_anchor_slots,
    )
