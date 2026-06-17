"""V4 deterministic retrieval — combined git-signal CandidateSet assembly."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.infra.git_context import GitContextProvider
from commit_investigator.models.candidates import CandidateCommit, CandidateSet
from commit_investigator.retrieval.strategies import (
    MergedCandidate,
    filter_valid_strategies,
    merge_hits,
    run_blame,
    run_file_log,
    run_keyword_grep,
    run_pickaxe,
)

DEFAULT_STRATEGIES = ["file_log", "keyword_grep", "pickaxe", "blame"]


@dataclass
class RetrievalConfig:
    """Configuration for combined git-signal retrieval."""

    max_candidates: int = 100
    strategies: list[str] = field(default_factory=lambda: list(DEFAULT_STRATEGIES))
    fallback_recency_threshold: int = 10
    file_log_per_file: int = 50
    keyword_grep_per_kw: int = 30
    pickaxe_per_symbol: int = 50
    blame_per_file: int = 100


@dataclass(frozen=True)
class RecallDiagnostic:
    """Result of recall@k check against a CandidateSet."""

    ground_truth_sha: str
    found: bool
    rank: int | None
    total_candidates: int
    strategies_that_found: list[str]


def compute_recall_at_k(
    candidate_set: CandidateSet,
    ground_truth_sha: str,
    k: int = 100,
) -> RecallDiagnostic:
    """Check whether ground truth appears within the top-k ranked candidates."""
    target = ground_truth_sha.lower()
    total = len(candidate_set.commits)
    for index, commit in enumerate(candidate_set.commits[:k], start=1):
        if commit.commit_id.lower() != target:
            continue
        strategies = [
            name
            for name in commit.retrieval_signal.split(",")
            if name and name != "recency_fallback"
        ]
        return RecallDiagnostic(
            ground_truth_sha=ground_truth_sha,
            found=True,
            rank=index,
            total_candidates=total,
            strategies_that_found=strategies,
        )
    return RecallDiagnostic(
        ground_truth_sha=ground_truth_sha,
        found=False,
        rank=None,
        total_candidates=total,
        strategies_that_found=[],
    )


def _run_enabled_strategies(
    problem: ProblemStatement,
    git: GitContextProvider,
    config: RetrievalConfig,
    enabled: list[str],
) -> list:
    """Execute enabled strategies and return all strategy hits."""
    from commit_investigator.retrieval.strategies import StrategyHit

    hits: list[StrategyHit] = []
    if "file_log" in enabled:
        hits.extend(run_file_log(problem, git, config.file_log_per_file))
    if "keyword_grep" in enabled:
        hits.extend(run_keyword_grep(problem, git, config.keyword_grep_per_kw))
    if "pickaxe" in enabled:
        hits.extend(run_pickaxe(problem, git, config.pickaxe_per_symbol))
    if "blame" in enabled:
        hits.extend(run_blame(problem, git, config.blame_per_file))
    return hits


def _rank_signal_candidates(candidates: dict[str, MergedCandidate]) -> list[MergedCandidate]:
    """Sort signal-driven candidates by signal_count desc, best_rank asc, commit_id asc."""
    return sorted(
        candidates.values(),
        key=lambda item: (
            -len(item.strategies),
            item.best_rank,
            item.commit_id,
        ),
    )


def _signal_count_for(candidate: MergedCandidate) -> int:
    if candidate.is_fallback:
        return 0
    return len(candidate.strategies)


def _build_signal_counts(candidates: list[MergedCandidate]) -> dict[int, int]:
    counts = Counter(_signal_count_for(candidate) for candidate in candidates)
    return dict(sorted(counts.items()))


def _retrieval_signal_for(candidate: MergedCandidate) -> str:
    if candidate.is_fallback:
        return "recency_fallback"
    return ",".join(sorted(candidate.strategies))


def _summary_for(candidate: MergedCandidate, git: GitContextProvider) -> str:
    if candidate.message:
        return candidate.message.splitlines()[0]
    full_message = git.get_commit_message(candidate.commit_id)
    if not full_message:
        return ""
    return full_message.splitlines()[0]


DIFF_SUMMARY_TOP_N = 20


def _to_candidate_commit(
    candidate: MergedCandidate,
    rank: int,
    git: GitContextProvider,
) -> CandidateCommit:
    touched = git.get_touched_files(candidate.commit_id)
    diff_summary = ""
    if rank <= DIFF_SUMMARY_TOP_N:
        diff_summary = git.get_diff_summary(candidate.commit_id, max_lines=10)
    return CandidateCommit(
        commit_id=candidate.commit_id,
        rank=rank,
        retrieval_signal=_retrieval_signal_for(candidate),
        summary=_summary_for(candidate, git),
        files_changed=list(touched) if touched else [],
        date=candidate.date,
        diff_summary=diff_summary,
    )


def retrieve_candidates(
    problem: ProblemStatement,
    git: GitContextProvider,
    config: RetrievalConfig | None = None,
) -> CandidateSet:
    """Run combined git-signal retrieval and return a ranked CandidateSet."""
    cfg = config or RetrievalConfig()
    enabled = filter_valid_strategies(cfg.strategies)
    temporal_bound = git.temporal_bound or ""

    hits = _run_enabled_strategies(problem, git, cfg, enabled)
    merged = merge_hits(hits)
    signal_count = len(merged)

    fallback_triggered = signal_count < cfg.fallback_recency_threshold
    pre_trim: list[MergedCandidate] = list(_rank_signal_candidates(merged))

    if fallback_triggered:
        existing_ids = {candidate.commit_id for candidate in pre_trim}
        recent_entries = git.list_recent_commits(max_results=cfg.max_candidates)
        for entry in recent_entries:
            sha = entry.commit_id.lower()
            if sha in existing_ids:
                continue
            pre_trim.append(
                MergedCandidate(
                    commit_id=sha,
                    strategies=set(),
                    best_rank=0,
                    message=entry.message.splitlines()[0] if entry.message else "",
                    date=entry.date,
                    is_fallback=True,
                )
            )
            existing_ids.add(sha)

    total_raw = len(pre_trim)
    signal_counts = _build_signal_counts(pre_trim)
    metadata = {
        "strategies_used": enabled,
        "total_raw_candidates": total_raw,
        "signal_counts": signal_counts,
        "fallback_triggered": fallback_triggered,
        "temporal_bound": temporal_bound,
    }

    if cfg.max_candidates <= 0:
        return CandidateSet(commits=[], retrieval_metadata=metadata, temporal_bound=temporal_bound)

    trimmed = pre_trim[: cfg.max_candidates]
    commits = [
        _to_candidate_commit(candidate, rank, git)
        for rank, candidate in enumerate(trimmed, start=1)
    ]
    return CandidateSet(
        commits=commits,
        retrieval_metadata=metadata,
        temporal_bound=temporal_bound,
    )
