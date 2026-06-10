"""Git context provider: reads diffs, messages, and file history from local clones.

Wraps git CLI for commit investigation. Requires repos cloned via scripts/clone_apache_repos.sh.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitRepoNotFoundError(Exception):
    """Raised when the expected git repo is not cloned."""


class GitCommitNotFoundError(Exception):
    """Raised when a commit ID is not found in the repository."""


@dataclass
class FileHistoryEntry:
    """A single commit in a file's history."""

    commit_id: str
    author: str
    date: str
    message: str


class GitContextProvider:
    """Provides git context (diffs, messages, history) from a local clone.

    Each instance is bound to a specific project repository.
    """

    def __init__(self, repo_path: str | Path) -> None:
        self._repo_path = Path(repo_path)
        if not (self._repo_path / ".git").exists():
            raise GitRepoNotFoundError(
                f"Git repo not found at {self._repo_path}. "
                f"Run scripts/clone_apache_repos.sh first."
            )

    @classmethod
    def for_project(cls, project: str, repos_dir: str | Path = "data/repos") -> GitContextProvider:
        """Create a provider for a named project (e.g., 'camel', 'hadoop')."""
        repo_path = Path(repos_dir) / project.lower()
        return cls(repo_path)

    def get_diff(self, commit_id: str) -> str | None:
        """Return unified diff for a commit. None if commit not found."""
        result = self._run_git(["show", "--format=", "--patch", commit_id])
        return result if result is not None else None

    def get_commit_message(self, commit_id: str) -> str | None:
        """Return the full commit message. None if commit not found."""
        return self._run_git(["log", "-1", "--format=%B", commit_id])

    def get_touched_files(self, commit_id: str) -> list[str] | None:
        """Return list of files modified by a commit. None if commit not found."""
        result = self._run_git(["diff-tree", "--no-commit-id", "-r", "--name-only", commit_id])
        if result is None:
            return None
        return [f for f in result.strip().splitlines() if f]

    def get_file_history(self, path: str, n: int = 3) -> list[FileHistoryEntry]:
        """Return last n commits touching the given file path.

        Works even for deleted files (uses --follow).
        """
        result = self._run_git([
            "log",
            f"-{n}",
            "--format=%H|%an|%ai|%s",
            "--follow",
            "--",
            path,
        ])
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

    def get_author_email(self, commit_id: str) -> str | None:
        """Return the author email for a commit."""
        return self._run_git(["log", "-1", "--format=%ae", commit_id])

    def commit_exists(self, commit_id: str) -> bool:
        """Check if a commit exists in the repository."""
        result = self._run_git(["cat-file", "-t", commit_id])
        return result is not None and result.strip() == "commit"

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
