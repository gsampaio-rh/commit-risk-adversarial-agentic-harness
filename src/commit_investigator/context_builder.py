"""Context builder: assembles investigation bundles from git, CSV features, and author stats.

Composes the full context an agent sees during investigation (turn 1).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from commit_investigator.git_context import (
    FileHistoryEntry,
    GitContextProvider,
    GitRepoNotFoundError,
)


@dataclass
class AuthorStats:
    """Precomputed statistics for a single author."""

    author: str
    total_commits: int
    buggy_commits: int
    buggy_rate: float
    avg_files_changed: float
    avg_lines_added: float
    avg_lines_deleted: float
    projects: list[str]


@dataclass
class InvestigationContext:
    """Full context bundle for a commit investigation."""

    commit_id: str
    project: str
    diff: str | None
    message: str | None
    touched_files: list[str]
    csv_features: dict[str, Any]
    file_histories: dict[str, list[FileHistoryEntry]]
    author_stats: AuthorStats | None
    missing_reasons: list[str] = field(default_factory=list)
    router_probability: float | None = None
    router_route: str | None = None


class AuthorStatsIndex:
    """Precomputed per-author statistics from the train split.

    Built exclusively from train data to prevent test leakage.
    """

    def __init__(self) -> None:
        self._stats: dict[str, AuthorStats] = {}
        self._project_stats: dict[str, AuthorStats] = {}

    @classmethod
    def from_train_csv(cls, csv_path: str | Path) -> AuthorStatsIndex:
        """Build author stats index from the train split CSV.

        Groups by author_date column proxy: uses commit count, buggy rate,
        and average change metrics per unique author (approximated from the CSV).
        """
        index = cls()
        author_data: dict[str, dict[str, Any]] = {}
        project_data: dict[str, dict[str, Any]] = {}

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                project = row.get("project", "unknown")
                author_proxy = row.get("author_date", "")
                if not author_proxy:
                    continue

                author_key = f"{project}:{author_proxy}"
                la = _safe_float(row.get("la", "0"))
                ld = _safe_float(row.get("ld", "0"))
                nf = _safe_float(row.get("nf", "0"))
                buggy = row.get("buggy", "False") in ("True", "true", "1")

                if author_key not in author_data:
                    author_data[author_key] = _empty_stats_bucket()

                d = author_data[author_key]
                d["commits"] += 1
                d["buggy"] += 1 if buggy else 0
                d["la_sum"] += la
                d["ld_sum"] += ld
                d["nf_sum"] += nf
                d["projects"].add(project)

                if project not in project_data:
                    project_data[project] = _empty_stats_bucket()

                p = project_data[project]
                p["commits"] += 1
                p["buggy"] += 1 if buggy else 0
                p["la_sum"] += la
                p["ld_sum"] += ld
                p["nf_sum"] += nf
                p["projects"].add(project)

        for author_key, data in author_data.items():
            index._stats[author_key] = _bucket_to_author_stats(author_key, data)

        for project_key, data in project_data.items():
            index._project_stats[project_key] = _bucket_to_author_stats(project_key, data)

        return index

    def get_stats(self, author_key: str) -> AuthorStats | None:
        """Get precomputed stats for an author key (project:author_date)."""
        return self._stats.get(author_key)

    def get_stats_for_project(self, project: str) -> AuthorStats | None:
        """Get aggregate stats for a project (fallback when author is unknown)."""
        return self._project_stats.get(_normalize_project(project))

    @property
    def authors(self) -> list[str]:
        return sorted(self._stats.keys())


class CommitContextBuilder:
    """Assembles investigation context bundles from multiple data sources.

    Composes git context, CSV features, and author stats into a single
    InvestigationContext that the agent receives at turn 1.
    """

    def __init__(
        self,
        git_provider: GitContextProvider,
        author_stats: AuthorStatsIndex | None = None,
    ) -> None:
        self._git = git_provider
        self._author_stats = author_stats

    def build(
        self,
        commit_id: str,
        project: str,
        csv_row: dict[str, Any] | None = None,
    ) -> InvestigationContext:
        """Build a complete investigation context for a commit.

        Handles missing data gracefully — partial context is valid.
        """
        missing_reasons: list[str] = []

        diff = self._git.get_diff(commit_id)
        if diff is None:
            missing_reasons.append(f"Diff unavailable for {commit_id}")

        message = self._git.get_commit_message(commit_id)
        if message is None:
            missing_reasons.append(f"Commit message unavailable for {commit_id}")

        touched_files = self._git.get_touched_files(commit_id) or []
        if not touched_files:
            missing_reasons.append(f"No touched files found for {commit_id}")

        csv_features: dict[str, Any] = {}
        if csv_row:
            csv_features = {
                k: _safe_float(v)
                for k, v in csv_row.items()
                if k in _NUMERIC_FEATURES
            }
        else:
            missing_reasons.append("No CSV row provided")

        file_histories: dict[str, list[FileHistoryEntry]] = {}
        for fpath in touched_files[:10]:
            history = self._git.get_file_history(fpath, n=3)
            if history:
                file_histories[fpath] = history

        author_stats = self._resolve_author_stats(project, csv_row)

        return InvestigationContext(
            commit_id=commit_id,
            project=project,
            diff=diff,
            message=message,
            touched_files=touched_files,
            csv_features=csv_features,
            file_histories=file_histories,
            author_stats=author_stats,
            missing_reasons=missing_reasons,
        )

    def _resolve_author_stats(
        self,
        project: str,
        csv_row: dict[str, Any] | None,
    ) -> AuthorStats | None:
        """Look up per-author stats, falling back to project aggregates."""
        if not self._author_stats:
            return None

        normalized = _normalize_project(project)
        if csv_row:
            row_project = _normalize_project(str(csv_row.get("project", project)))
            author_date = csv_row.get("author_date")
            if author_date:
                author_key = f"{row_project}:{author_date}"
                stats = self._author_stats.get_stats(author_key)
                if stats is not None:
                    return stats

        return self._author_stats.get_stats_for_project(normalized)


_NUMERIC_FEATURES = {"la", "ld", "nf", "nd", "ns", "ent", "ndev", "age", "nuc", "aexp", "arexp", "asexp"}

_PROJECT_ALIASES = {
    "camel": "apache/camel",
    "hadoop": "apache/hadoop",
}


def _normalize_project(project: str) -> str:
    """Map short v1 repo names to ApacheJIT CSV project identifiers."""
    lowered = project.strip().lower()
    return _PROJECT_ALIASES.get(lowered, project)


def _empty_stats_bucket() -> dict[str, Any]:
    return {
        "commits": 0,
        "buggy": 0,
        "la_sum": 0.0,
        "ld_sum": 0.0,
        "nf_sum": 0.0,
        "projects": set(),
    }


def _bucket_to_author_stats(label: str, data: dict[str, Any]) -> AuthorStats:
    n = data["commits"]
    return AuthorStats(
        author=label,
        total_commits=n,
        buggy_commits=data["buggy"],
        buggy_rate=data["buggy"] / n if n > 0 else 0.0,
        avg_files_changed=data["nf_sum"] / n if n > 0 else 0.0,
        avg_lines_added=data["la_sum"] / n if n > 0 else 0.0,
        avg_lines_deleted=data["ld_sum"] / n if n > 0 else 0.0,
        projects=sorted(data["projects"]),
    )


def _safe_float(value: Any) -> float:
    """Convert to float, returning 0.0 on failure."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0
