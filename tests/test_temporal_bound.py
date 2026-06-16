"""Tests for temporal bound enforcement in GitContextProvider.

Uses the project's own git repo (this repository) for integration tests.
No Apache repos required.
"""

import subprocess
from pathlib import Path

import pytest

from commit_investigator.infra.git_context import (
    GitCommitNotFoundError,
    GitContextProvider,
    GitRepoNotFoundError,
    TemporalBoundViolation,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _has_git_repo() -> bool:
    return (REPO_ROOT / ".git").exists()


skip_no_git = pytest.mark.skipif(
    not _has_git_repo(),
    reason="No git repo at project root",
)


def _get_commits(n: int = 5) -> list[str]:
    """Get n recent commit SHAs from this repo."""
    result = subprocess.run(
        ["git", "log", f"-{n}", "--format=%H"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]


class TestTemporalBoundInit:
    @skip_no_git
    def test_no_bound_creates_provider(self) -> None:
        provider = GitContextProvider(REPO_ROOT)
        assert provider.temporal_bound is None

    @skip_no_git
    def test_valid_bound_creates_provider(self) -> None:
        provider = GitContextProvider(REPO_ROOT, temporal_bound="HEAD~2")
        assert provider.temporal_bound == "HEAD~2"

    @skip_no_git
    def test_invalid_bound_raises(self) -> None:
        with pytest.raises(GitCommitNotFoundError, match="not found"):
            GitContextProvider(REPO_ROOT, temporal_bound="nonexistent_ref_xyz")


@skip_no_git
class TestTemporalBoundEnforcement:
    """Verify that commits beyond the bound are rejected."""

    @pytest.fixture
    def commits(self) -> list[str]:
        shas = _get_commits(5)
        assert len(shas) >= 3, "Need at least 3 commits for temporal tests"
        return shas

    @pytest.fixture
    def bounded_provider(self, commits: list[str]) -> GitContextProvider:
        """Provider bounded at the 3rd-most-recent commit (commits[2]).

        commits[0] = HEAD (newest, BEYOND bound)
        commits[1] = HEAD~1 (BEYOND bound)
        commits[2] = HEAD~2 (AT bound, should be accessible)
        commits[3] = HEAD~3 (BEFORE bound, should be accessible)
        """
        return GitContextProvider(REPO_ROOT, temporal_bound=commits[2])

    def test_commit_at_bound_is_accessible(
        self, bounded_provider: GitContextProvider, commits: list[str]
    ) -> None:
        msg = bounded_provider.get_commit_message(commits[2])
        assert msg is not None

    def test_commit_before_bound_is_accessible(
        self, bounded_provider: GitContextProvider, commits: list[str]
    ) -> None:
        if len(commits) < 4:
            pytest.skip("Need at least 4 commits")
        msg = bounded_provider.get_commit_message(commits[3])
        assert msg is not None

    def test_commit_after_bound_raises(
        self, bounded_provider: GitContextProvider, commits: list[str]
    ) -> None:
        with pytest.raises(TemporalBoundViolation, match="beyond temporal bound"):
            bounded_provider.get_diff(commits[0])

    def test_get_commit_message_beyond_raises(
        self, bounded_provider: GitContextProvider, commits: list[str]
    ) -> None:
        with pytest.raises(TemporalBoundViolation):
            bounded_provider.get_commit_message(commits[0])

    def test_get_touched_files_beyond_raises(
        self, bounded_provider: GitContextProvider, commits: list[str]
    ) -> None:
        with pytest.raises(TemporalBoundViolation):
            bounded_provider.get_touched_files(commits[0])

    def test_get_author_email_beyond_raises(
        self, bounded_provider: GitContextProvider, commits: list[str]
    ) -> None:
        with pytest.raises(TemporalBoundViolation):
            bounded_provider.get_author_email(commits[0])


@skip_no_git
class TestTemporalBoundOnHistory:
    """Verify file history respects temporal bound."""

    @pytest.fixture
    def commits(self) -> list[str]:
        return _get_commits(5)

    def test_file_history_bounded_returns_only_pre_bound(
        self, commits: list[str]
    ) -> None:
        if len(commits) < 3:
            pytest.skip("Need at least 3 commits")
        bounded = GitContextProvider(REPO_ROOT, temporal_bound=commits[2])
        history = bounded.get_file_history("AGENTS.md", n=50)
        for entry in history:
            assert entry.commit_id != commits[0], (
                f"File history returned commit {commits[0][:8]} which is beyond bound"
            )


@skip_no_git
class TestUnboundedProviderBackwardsCompat:
    """Verify that an unbounded provider still works as before."""

    @pytest.fixture
    def provider(self) -> GitContextProvider:
        return GitContextProvider(REPO_ROOT)

    def test_get_diff_works(self, provider: GitContextProvider) -> None:
        diff = provider.get_diff("HEAD~1")
        assert diff is None or isinstance(diff, str)

    def test_get_commit_message_works(self, provider: GitContextProvider) -> None:
        msg = provider.get_commit_message("HEAD")
        assert msg is not None

    def test_commit_exists_works(self, provider: GitContextProvider) -> None:
        assert provider.commit_exists("HEAD")


@skip_no_git
class TestForProjectWithBound:
    """Verify the factory method accepts temporal_bound."""

    def test_for_project_raises_on_missing_repo(self) -> None:
        with pytest.raises(GitRepoNotFoundError):
            GitContextProvider.for_project(
                "nonexistent_project_xyz",
                repos_dir="/tmp",
                temporal_bound="HEAD",
            )
