"""Git search operations mixin — commit search and file resolution."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from commit_investigator.infra.git_context import FileHistoryEntry


def parse_log_entries(result: str | None) -> list[FileHistoryEntry]:
    """Parse git log output in %H|%an|%ai|%s format."""
    from commit_investigator.infra.git_context import FileHistoryEntry

    if not result:
        return []
    entries = []
    for line in result.strip().splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            entries.append(FileHistoryEntry(
                commit_id=parts[0],
                author=parts[1],
                date=parts[2],
                message=parts[3],
            ))
    return entries


class GitSearchMixin:
    """Search/query operations for git repositories.

    Mixed into GitContextProvider. Uses self._run_git, self._temporal_bound.
    """

    def search_commits_by_file(
        self,
        path: str,
        max_results: int = 20,
    ) -> list[FileHistoryEntry]:
        """Find commits that touched a specific file path."""
        return self.get_file_history(path, n=max_results)

    def search_commits_by_keyword(
        self,
        keyword: str,
        max_results: int = 20,
    ) -> list[FileHistoryEntry]:
        """Search commit messages for a keyword (case-insensitive).

        Uses git log --grep with --first-parent for linear history.
        Bounded by temporal_bound when set.
        """
        args = [
            "log", f"-{max_results}",
            "--format=%H|%an|%ai|%s",
            "--first-parent",
            "--grep", keyword,
            "-i",
        ]
        if self._temporal_bound:
            args.append(self._temporal_bound)

        result = self._run_git(args)
        return parse_log_entries(result)

    def list_recent_commits(
        self,
        max_results: int = 20,
        path: str | None = None,
    ) -> list[FileHistoryEntry]:
        """List recent commits, optionally restricted to a path.

        Bounded by temporal_bound when set. Returns most recent first.
        """
        args = [
            "log", f"-{max_results}",
            "--format=%H|%an|%ai|%s",
            "--first-parent",
        ]
        if self._temporal_bound:
            args.append(self._temporal_bound)
        if path:
            args.extend(["--", path])

        result = self._run_git(args)
        return parse_log_entries(result)

    def get_blame(
        self,
        path: str,
        line_start: int = 1,
        line_end: int | None = None,
    ) -> str | None:
        """Get blame for a file at the temporal bound (or HEAD if unbounded)."""
        ref = self._temporal_bound or "HEAD"
        args = ["blame"]
        if line_end is not None:
            start = max(1, line_start)
            args.extend(["-L", f"{start},{line_end}"])
        args.extend([ref, "--", path])
        return self._run_git(args)

    def search_commits_by_pickaxe(
        self,
        symbol: str,
        max_results: int = 50,
    ) -> list[FileHistoryEntry]:
        """Search for commits that add or remove a string (git log -S).

        Pickaxe search finds commits where the number of occurrences of
        the given string changed. Traverses ALL commits (no --first-parent)
        to reach commits introduced via merged branches/PRs.
        Bounded by temporal_bound when set.
        """
        args = [
            "log", f"-{max_results}",
            "--format=%H|%an|%ai|%s",
            "-S", symbol,
        ]
        if self._temporal_bound:
            args.append(self._temporal_bound)

        result = self._run_git(args)
        return parse_log_entries(result)

    def list_commits_in_date_range(
        self,
        after: str | None = None,
        before: str | None = None,
        max_results: int = 200,
        path: str | None = None,
    ) -> list[FileHistoryEntry]:
        """List commits within a date range, bounded by temporal_bound.

        Dates should be ISO-8601 format (e.g., '2015-01-01').
        """
        args = [
            "log", f"-{max_results}",
            "--format=%H|%an|%ai|%s",
            "--first-parent",
        ]
        if after:
            args.extend(["--after", after])
        if before:
            args.extend(["--before", before])
        if self._temporal_bound:
            args.append(self._temporal_bound)
        if path:
            args.extend(["--", path])

        result = self._run_git(args)
        return parse_log_entries(result)

    def resolve_file_path(self, filename: str) -> list[str]:
        """Find full repo paths matching a bare filename or partial path.

        Uses git ls-tree at temporal bound (or HEAD) to search the tree.
        """
        all_files = self._list_tree_files()
        if not all_files:
            return []

        filename_lower = filename.lower()
        suffix = "/" + filename_lower

        matches = []
        for line in all_files:
            lower = line.lower()
            if lower.endswith(suffix) or lower == filename_lower:
                matches.append(line)

        matches.sort(key=len)
        return matches[:10]

    @functools.lru_cache(maxsize=4)
    def _list_tree_files(self) -> tuple[str, ...]:
        """List all files in the repo tree (cached)."""
        ref = self._temporal_bound or "HEAD"
        result = self._run_git(["ls-tree", "-r", "--name-only", ref])
        if not result:
            return ()
        return tuple(result.strip().splitlines())

    def commit_exists(self, commit_id: str) -> bool:
        """Check if a commit exists in the repository."""
        result = self._run_git(["cat-file", "-t", commit_id])
        return result is not None and result.strip() == "commit"
