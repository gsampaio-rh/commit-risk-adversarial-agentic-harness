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

from commit_investigator.infra.git_search import GitSearchMixin, parse_log_entries


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


class GitContextProvider(GitSearchMixin):
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
    def repo_path(self) -> Path:
        """Path to the git repository."""
        return self._repo_path

    @property
    def temporal_bound(self) -> str | None:
        """The temporal bound ref, if set."""
        return self._temporal_bound

    def resolve_ref(self, ref: str) -> str | None:
        """Resolve a ref to its full 40-char SHA. Public wrapper for blame normalization."""
        return self._resolve_ref(ref)

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

    def get_diff_summary(self, commit_id: str, max_lines: int = 15) -> str:
        """Return a compact diff summary: stat + first N lines of patch."""
        self._enforce_bound(commit_id)
        stat = self._run_git(["show", "--format=", "--stat", commit_id])
        patch = self._run_git(["show", "--format=", "--patch", "-U1", commit_id])
        parts = []
        if stat:
            parts.append(stat.strip())
        if patch:
            lines = patch.splitlines()[:max_lines]
            parts.append("\n".join(lines))
        return "\n".join(parts) if parts else ""

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

        Traverses ALL commits (no --first-parent) to reach commits
        introduced via merged branches/PRs.
        Bounded by temporal_bound when set.
        """
        args = ["log", f"-{n}", "--format=%H|%an|%ai|%s"]
        if self._temporal_bound:
            args.append(self._temporal_bound)
        args.extend(["--", path])

        result = self._run_git(args)
        return parse_log_entries(result)

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

    def _run_git(self, args: list[str]) -> str | None:
        """Run a git command in the repo. Returns stdout or None on failure."""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self._repo_path,
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")
                if "unknown revision" in stderr or "bad object" in stderr:
                    return None
                if "does not have" in stderr or "not a valid" in stderr:
                    return None
                return None
            return result.stdout.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return None
        except FileNotFoundError:
            raise GitRepoNotFoundError("git command not found. Is git installed?")
