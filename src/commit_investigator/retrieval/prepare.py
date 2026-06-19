"""Input pipeline entry — extraction + retrieval wired.

prepare_investigation() is the single entry point for the agent pipeline.
It runs extraction (zero LLM cost) and retrieval (git commands only) to produce
the ProblemStatement + CandidateSet that the investigation harness consumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, overload

from commit_investigator.extraction.jira_client import JiraIssue
from commit_investigator.extraction.problem_extractor import ProblemExtractor, ProblemStatement
from commit_investigator.infra.git_context import GitContextProvider
from commit_investigator.models.candidates import CandidateSet
from commit_investigator.retrieval.retriever import RetrievalConfig, retrieve_candidates

_WIDENED_MULTIPLIER = 2


@dataclass(frozen=True)
class RetrievalResult:
    """Output of prepare_investigation() — extraction + retrieval combined."""

    problem_statement: ProblemStatement
    candidate_set: CandidateSet
    metadata: dict[str, Any] = field(default_factory=dict)


def _widen_config(config: RetrievalConfig) -> RetrievalConfig:
    """Double per-strategy limits for brief-validation retry."""
    return replace(
        config,
        file_log_per_file=config.file_log_per_file * _WIDENED_MULTIPLIER,
        keyword_grep_per_kw=config.keyword_grep_per_kw * _WIDENED_MULTIPLIER,
        pickaxe_per_symbol=config.pickaxe_per_symbol * _WIDENED_MULTIPLIER,
        blame_per_file=config.blame_per_file * _WIDENED_MULTIPLIER,
    )


@overload
def prepare_investigation(
    source: JiraIssue,
    repo_path: str | Path,
    temporal_bound: str,
    project: str,
    *,
    config: RetrievalConfig | None = ...,
    fix_hash: str | None = ...,
) -> RetrievalResult: ...


@overload
def prepare_investigation(
    source: tuple[str, str],
    repo_path: str | Path,
    temporal_bound: str,
    project: str,
    *,
    config: RetrievalConfig | None = ...,
    issue_key: str = ...,
    fix_hash: str | None = ...,
) -> RetrievalResult: ...


def prepare_investigation(
    source: JiraIssue | tuple[str, str],
    repo_path: str | Path,
    temporal_bound: str,
    project: str,
    *,
    config: RetrievalConfig | None = None,
    issue_key: str = "",
    fix_hash: str | None = None,
) -> RetrievalResult:
    """Run Stage 0 (extraction) + Stage 1 (retrieval) for one investigation.

    Args:
        source: Either a JiraIssue or a (title, description) tuple.
        repo_path: Path to the cloned git repository.
        temporal_bound: Git ref for temporal bound (e.g., fix_hash~1).
        project: Project identifier (e.g., "CASSANDRA").
        config: Optional retrieval configuration override.
        issue_key: Issue key when source is a raw tuple.

    Returns:
        RetrievalResult with ProblemStatement, CandidateSet, and metadata.

    Raises:
        GitRepoNotFoundError: If repo_path does not contain a .git directory.
        GitCommitNotFoundError: If temporal_bound cannot be resolved.
    """
    extractor = ProblemExtractor()

    if isinstance(source, JiraIssue):
        problem = extractor.from_jira_issue(source, project=project)
    else:
        title, description = source
        problem = extractor.from_raw(title, description, project=project, issue_key=issue_key)

    git = GitContextProvider(repo_path=repo_path, temporal_bound=temporal_bound)
    cfg = config or RetrievalConfig()

    if fix_hash and not cfg.fix_hash:
        cfg = replace(cfg, fix_hash=fix_hash)

    candidate_set = retrieve_candidates(problem, git, cfg)
    retry_triggered = False

    if len(candidate_set.commits) < cfg.fallback_recency_threshold:
        widened = _widen_config(cfg)
        candidate_set = retrieve_candidates(problem, git, widened)
        retry_triggered = True

    metadata = {
        "retry_triggered": retry_triggered,
        "strategies_used": candidate_set.retrieval_metadata.get("strategies_used", []),
        "total_raw": candidate_set.retrieval_metadata.get("total_raw_candidates", 0),
        "fallback_triggered": candidate_set.retrieval_metadata.get("fallback_triggered", False),
    }

    return RetrievalResult(
        problem_statement=problem,
        candidate_set=candidate_set,
        metadata=metadata,
    )
