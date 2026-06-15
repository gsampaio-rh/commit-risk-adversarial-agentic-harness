"""Git context provider: temporally-bounded git access for bug attribution.

Wraps git CLI for repository search and commit inspection. When a
temporal_bound is set, all operations verify that requested commits
are ancestors of the bound (COMMIT_B~1) before returning data.

Requires repos cloned via scripts/clone_apache_repos.sh.
"""

from __future__ import annotations

import functools
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitRepoNotFoundError(Exception):
    """Raised when the expected git repo is not cloned."""


class GitCommitNotFoundError(Exception):
    """Raised when a commit ID is not found in the repository."""


class TemporalBoundViolation(Exception):
    """Raised when a request targets a commit beyond the temporal bound."""


@dataclass
class FileHistoryEntry:
    """A single commit in a file's history."""

    commit_id: str
    author: str
    date: str
    message: str


class GitContextProvider:
    """Provides temporally-bounded git context from a local clone.

    When temporal_bound is set, every commit access is verified:
    the commit must be an ancestor of the bound ref. This prevents
    the attribution agent from seeing the fix commit or any post-fix history.
    """

    def __init__(
        self,
        repo_path: str | Path,
        temporal_bound: str | None = None,
    ) -> None:
        self._repo_path = Path(repo_path)
        if not (self._repo_path / ".git").exists():
            raise GitRepoNotFoundError(
                f"Git repo not found at {self._repo_path}. "
                f"Run scripts/clone_apache_repos.sh first."
            )
        self._temporal_bound = temporal_bound
        self._bound_sha: str | None = None
        if temporal_bound:
            self._bound_sha = self._resolve_ref(temporal_bound)
            if self._bound_sha is None:
                raise GitCommitNotFoundError(
                    f"Temporal bound ref '{temporal_bound}' not found in repo"
                )

    @classmethod
    def for_project(
        cls,
        project: str,
        repos_dir: str | Path = "data/repos",
        temporal_bound: str | None = None,
    ) -> GitContextProvider:
        """Create a provider for a named project (e.g., 'camel', 'hadoop')."""
        repo_path = Path(repos_dir) / project.lower()
        return cls(repo_path, temporal_bound=temporal_bound)

    @property
    def temporal_bound(self) -> str | None:
        """The temporal bound ref, if set."""
        return self._temporal_bound

    def _resolve_ref(self, ref: str) -> str | None:
        """Resolve a ref to its full SHA."""
        result = self._run_git(["rev-parse", "--verify", ref])
        return result.strip() if result else None

    @functools.lru_cache(maxsize=512)
    def _is_ancestor(self, commit_sha: str, bound_sha: str) -> bool:
        """Check if commit_sha is an ancestor of (or equal to) bound_sha."""
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit_sha, bound_sha],
            cwd=self._repo_path,
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0

    def _enforce_bound(self, commit_id: str) -> None:
        """Verify a commit is within temporal bounds. Raises on violation."""
        if self._bound_sha is None:
            return
        resolved = self._resolve_ref(commit_id)
        if resolved is None:
            return
        if not self._is_ancestor(resolved, self._bound_sha):
            raise TemporalBoundViolation(
                f"Commit {commit_id[:12]} is beyond temporal bound "
                f"{self._temporal_bound}. Access denied."
            )

    def get_diff(self, commit_id: str) -> str | None:
        """Return unified diff for a commit. None if commit not found."""
        self._enforce_bound(commit_id)
        result = self._run_git(["show", "--format=", "--patch", commit_id])
        return result if result is not None else None

    def get_commit_message(self, commit_id: str) -> str | None:
        """Return the full commit message. None if commit not found."""
        self._enforce_bound(commit_id)
        return self._run_git(["log", "-1", "--format=%B", commit_id])

    def get_touched_files(self, commit_id: str) -> list[str] | None:
        """Return list of files modified by a commit. None if commit not found."""
        self._enforce_bound(commit_id)
        result = self._run_git(["diff-tree", "--no-commit-id", "-r", "--name-only", commit_id])
        if result is None:
            return None
        return [f for f in result.strip().splitlines() if f]

    def get_file_history(
        self,
        path: str,
        n: int = 10,
    ) -> list[FileHistoryEntry]:
        """Return last n commits touching the given file path.

        When temporal_bound is set, restricts to commits reachable
        from the bound ref using --first-parent for linear history.
        """
        args = ["log", f"-{n}", "--format=%H|%an|%ai|%s", "--first-parent"]
        if self._temporal_bound:
            args.append(self._temporal_bound)
        args.extend(["--", path])

        result = self._run_git(args)
        return self._parse_log_entries(result)

    def get_author_email(self, commit_id: str) -> str | None:
        """Return the author email for a commit."""
        self._enforce_bound(commit_id)
        return self._run_git(["log", "-1", "--format=%ae", commit_id])

    def get_file_at_commit(self, commit_id: str, path: str) -> str | None:
        """Return file contents at a specific commit (git show commit:path)."""
        self._enforce_bound(commit_id)
        spec = f"{commit_id}:{path}"
        return self._run_git(["show", spec])

    def get_blame_snippet(
        self,
        commit_id: str,
        path: str,
        line_start: int,
        line_end: int,
        context_lines: int = 2,
    ) -> str | None:
        """Return git blame output for a line range at a commit."""
        self._enforce_bound(commit_id)
        if line_start < 1:
            line_start = 1
        if line_end < line_start:
            line_end = line_start
        start = max(1, line_start - context_lines)
        end = line_end + context_lines
        return self._run_git([
            "blame",
            "-L", f"{start},{end}",
            commit_id,
            "--",
            path,
        ])

    def search_commits_by_file(
        self,
        path: str,
        max_results: int = 20,
    ) -> list[FileHistoryEntry]:
        """Find commits that touched a specific file path.

        Bounded by temporal_bound when set. Returns most recent first.
        """
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
        return self._parse_log_entries(result)

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
        return self._parse_log_entries(result)

    def get_blame(
        self,
        path: str,
        line_start: int = 1,
        line_end: int | None = None,
    ) -> str | None:
        """Get blame for a file at the temporal bound (or HEAD if unbounded).

        Returns blame output showing which commit last modified each line.
        """
        ref = self._temporal_bound or "HEAD"
        args = ["blame"]
        if line_end is not None:
            start = max(1, line_start)
            args.extend(["-L", f"{start},{line_end}"])
        args.extend([ref, "--", path])
        return self._run_git(args)

    def commit_exists(self, commit_id: str) -> bool:
        """Check if a commit exists in the repository."""
        result = self._run_git(["cat-file", "-t", commit_id])
        return result is not None and result.strip() == "commit"

    def _parse_log_entries(self, result: str | None) -> list[FileHistoryEntry]:
        """Parse git log output in %H|%an|%ai|%s format."""
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

    def _run_git(self, args: list[str]) -> str | None:
        """Run a git command in the repo. Returns stdout or None on failure."""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self._repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                if "unknown revision" in result.stderr or "bad object" in result.stderr:
                    return None
                if "does not have" in result.stderr or "not a valid" in result.stderr:
                    return None
                return None
            return result.stdout
        except subprocess.TimeoutExpired:
            return None
        except FileNotFoundError:
            raise GitRepoNotFoundError("git command not found. Is git installed?")
