"""Phase 1 narrowing: pre-score + deterministic triage.

Entry point: narrow_candidates(candidate_set, problem) → TriageResult
"""

from commit_investigator.narrowing.models import (
    ScoredCandidate,
    ScoredShortlist,
    TriagedCandidate,
    TriageResult,
    TriageTier,
)
from commit_investigator.narrowing.pipeline import narrow_candidates
from commit_investigator.narrowing.scoring import (
    DEFAULT_SHORTLIST_SIZE,
    DEFAULT_WEIGHTS,
    compute_file_overlap,
    compute_pre_scores,
    get_signal_count,
)
from commit_investigator.narrowing.triage import (
    BLAME_ANCHOR_SLOTS,
    MUST_EXAMINE_SIZE,
    WATCHLIST_SIZE,
    assign_tiers,
)

__all__ = [
    "BLAME_ANCHOR_SLOTS",
    "DEFAULT_SHORTLIST_SIZE",
    "DEFAULT_WEIGHTS",
    "MUST_EXAMINE_SIZE",
    "WATCHLIST_SIZE",
    "ScoredCandidate",
    "ScoredShortlist",
    "TriagedCandidate",
    "TriageResult",
    "TriageTier",
    "assign_tiers",
    "compute_file_overlap",
    "compute_pre_scores",
    "get_signal_count",
    "narrow_candidates",
]
