"""Phase 1a: Pre-score formula for candidate narrowing.

Scores each candidate using deterministic signals from retrieval metadata:
  pre_score = w_fo·file_overlap + w_sc·norm(signal_count) + w_r·(1 - norm(rank))

Default weights: 0.5/0.3/0.2. When the JIRA has no extracted files (file_overlap
is meaningless for all candidates), weights adapt to 0.0/0.6/0.4.
"""

from __future__ import annotations

from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.models.candidates import CandidateCommit, CandidateSet
from commit_investigator.narrowing.models import ScoredCandidate, ScoredShortlist

DEFAULT_WEIGHTS = {"file_overlap": 0.5, "signal_count": 0.3, "rank": 0.2}
ADAPTIVE_WEIGHTS_NO_FILES = {"file_overlap": 0.0, "signal_count": 0.6, "rank": 0.4}
DEFAULT_SHORTLIST_SIZE = 20


def _file_matches(changed_file: str, extracted_hint: str) -> bool:
    """Suffix match: changed file ends with extracted hint path."""
    cf = changed_file.lower()
    eh = extracted_hint.lower()
    return cf == eh or cf.endswith("/" + eh)


def compute_file_overlap(
    files_changed: list[str],
    extracted_files: list[str],
) -> float:
    """Fraction of extracted files matched by at least one changed file."""
    if not extracted_files:
        return 0.0
    matches = sum(
        1 for ef in extracted_files
        if any(_file_matches(fc, ef) for fc in files_changed)
    )
    return matches / len(extracted_files)


def get_signal_count(candidate: CandidateCommit) -> int:
    """Count distinct retrieval strategies that found this candidate."""
    parts = [
        s for s in candidate.retrieval_signal.split(",")
        if s and s != "recency_fallback"
    ]
    return len(parts)


def compute_pre_scores(
    candidate_set: CandidateSet,
    problem: ProblemStatement,
    *,
    weights: dict[str, float] | None = None,
    shortlist_size: int = DEFAULT_SHORTLIST_SIZE,
) -> ScoredShortlist:
    """Score all candidates and return the top-K as a ScoredShortlist.

    Args:
        candidate_set: Full retrieval output (typically 50-100 candidates).
        problem: Extracted problem statement with file/symbol hints.
        weights: Override pre-score weights (default: 0.5/0.3/0.2).
        shortlist_size: How many top candidates to keep (default: 20).

    Returns:
        ScoredShortlist containing the top-K candidates sorted by pre_score desc.
    """
    if weights is not None:
        w = weights
    elif not problem.extracted_files:
        w = ADAPTIVE_WEIGHTS_NO_FILES
    else:
        w = DEFAULT_WEIGHTS
    candidates = candidate_set.commits

    if not candidates:
        return ScoredShortlist(candidates=[], total_scored=0)

    max_signals = max(get_signal_count(c) for c in candidates) or 1
    n = len(candidates)

    scored: list[ScoredCandidate] = []
    for c in candidates:
        fo = compute_file_overlap(c.files_changed, problem.extracted_files)
        sc = get_signal_count(c)

        norm_sc = sc / max_signals
        norm_rank = (c.rank - 1) / (n - 1) if n > 1 else 0.0

        pre_score = (
            w["file_overlap"] * fo
            + w["signal_count"] * norm_sc
            + w["rank"] * (1 - norm_rank)
        )

        scored.append(ScoredCandidate(
            commit_id=c.commit_id,
            original_rank=c.rank,
            pre_score=round(pre_score, 6),
            file_overlap=round(fo, 6),
            signal_count=sc,
            summary=c.summary,
            files_changed=c.files_changed,
            date=c.date,
            retrieval_signal=c.retrieval_signal,
            diff_summary=c.diff_summary,
        ))

    scored.sort(key=lambda s: (-s.pre_score, s.commit_id))

    top_k = scored[:shortlist_size]
    return ScoredShortlist(candidates=top_k, total_scored=n)
