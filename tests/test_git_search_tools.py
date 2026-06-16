"""Tests for expanded git search tools in GitContextProvider.

Uses the project's own git repo for integration tests.
"""

from pathlib import Path

import pytest

from commit_investigator.infra.git_context import GitContextProvider


REPO_ROOT = Path(__file__).resolve().parents[1]

skip_no_git = pytest.mark.skipif(
    not (REPO_ROOT / ".git").exists(),
    reason="No git repo at project root",
)


@skip_no_git
class TestSearchCommitsByFile:
    @pytest.fixture
    def provider(self) -> GitContextProvider:
        return GitContextProvider(REPO_ROOT)

    def test_returns_entries_for_known_file(self, provider: GitContextProvider) -> None:
        entries = provider.search_commits_by_file("AGENTS.md", max_results=5)
        assert len(entries) > 0
        assert entries[0].commit_id

    def test_returns_empty_for_nonexistent_file(self, provider: GitContextProvider) -> None:
        entries = provider.search_commits_by_file("nonexistent_file_xyz.txt")
        assert entries == []

    def test_respects_max_results(self, provider: GitContextProvider) -> None:
        entries = provider.search_commits_by_file("AGENTS.md", max_results=2)
        assert len(entries) <= 2


@skip_no_git
class TestSearchCommitsByKeyword:
    @pytest.fixture
    def provider(self) -> GitContextProvider:
        return GitContextProvider(REPO_ROOT)

    def test_finds_commits_with_keyword(self, provider: GitContextProvider) -> None:
        entries = provider.search_commits_by_keyword("feat", max_results=5)
        assert len(entries) > 0

    def test_returns_empty_for_unique_keyword(self, provider: GitContextProvider) -> None:
        entries = provider.search_commits_by_keyword("xyzzy_nonexistent_keyword_12345")
        assert entries == []

    def test_case_insensitive(self, provider: GitContextProvider) -> None:
        lower = provider.search_commits_by_keyword("fix", max_results=10)
        upper = provider.search_commits_by_keyword("FIX", max_results=10)
        assert len(lower) == len(upper)


@skip_no_git
class TestListRecentCommits:
    @pytest.fixture
    def provider(self) -> GitContextProvider:
        return GitContextProvider(REPO_ROOT)

    def test_returns_recent_commits(self, provider: GitContextProvider) -> None:
        entries = provider.list_recent_commits(max_results=5)
        assert len(entries) > 0
        assert len(entries) <= 5

    def test_with_path_filter(self, provider: GitContextProvider) -> None:
        all_commits = provider.list_recent_commits(max_results=50)
        path_commits = provider.list_recent_commits(max_results=50, path="AGENTS.md")
        assert len(path_commits) <= len(all_commits)

    def test_respects_temporal_bound(self) -> None:
        unbounded = GitContextProvider(REPO_ROOT)
        all_commits = unbounded.list_recent_commits(max_results=50)
        if len(all_commits) < 3:
            pytest.skip("Need at least 3 commits")

        bound_sha = all_commits[2].commit_id
        bounded = GitContextProvider(REPO_ROOT, temporal_bound=bound_sha)
        bounded_commits = bounded.list_recent_commits(max_results=50)

        assert len(bounded_commits) <= len(all_commits)
        bounded_shas = {e.commit_id for e in bounded_commits}
        assert all_commits[0].commit_id not in bounded_shas


@skip_no_git
class TestGetBlame:
    @pytest.fixture
    def provider(self) -> GitContextProvider:
        return GitContextProvider(REPO_ROOT)

    def test_blame_returns_output(self, provider: GitContextProvider) -> None:
        blame = provider.get_blame("AGENTS.md")
        assert blame is not None
        assert len(blame) > 0

    def test_blame_with_line_range(self, provider: GitContextProvider) -> None:
        blame = provider.get_blame("AGENTS.md", line_start=1, line_end=5)
        assert blame is not None
        lines = blame.strip().splitlines()
        assert len(lines) <= 10

    def test_blame_nonexistent_file(self, provider: GitContextProvider) -> None:
        blame = provider.get_blame("nonexistent_xyz.txt")
        assert blame is None


@skip_no_git
class TestSearchToolsWithTemporalBound:
    """Verify search tools respect temporal bounds."""

    def test_keyword_search_bounded(self) -> None:
        unbounded = GitContextProvider(REPO_ROOT)
        all_commits = unbounded.list_recent_commits(max_results=10)
        if len(all_commits) < 3:
            pytest.skip("Need at least 3 commits")

        bound_sha = all_commits[2].commit_id
        bounded = GitContextProvider(REPO_ROOT, temporal_bound=bound_sha)

        bounded_results = bounded.search_commits_by_keyword("feat", max_results=50)
        for entry in bounded_results:
            assert entry.commit_id != all_commits[0].commit_id
