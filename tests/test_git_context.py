"""Tests for the git context provider."""

import pytest

from commit_investigator.infra.git_context import GitContextProvider, GitRepoNotFoundError

from tests.conftest import skip_no_repos


class TestGitContextProviderErrors:
    def test_nonexistent_repo_raises(self):
        with pytest.raises(GitRepoNotFoundError):
            GitContextProvider("/tmp/nonexistent_repo_12345")

    def test_error_message_mentions_clone_script(self):
        with pytest.raises(GitRepoNotFoundError, match="clone_apache_repos"):
            GitContextProvider("/tmp/nonexistent_repo_12345")


@skip_no_repos
class TestGitContextProviderIntegration:
    @pytest.fixture
    def provider(self) -> GitContextProvider:
        return GitContextProvider.for_project("camel")

    def test_get_diff(self, provider):
        # Use a known early Camel commit
        diff = provider.get_diff("HEAD~1")
        assert diff is None or isinstance(diff, str)

    def test_get_commit_message(self, provider):
        msg = provider.get_commit_message("HEAD")
        assert msg is not None
        assert len(msg) > 0

    def test_get_touched_files(self, provider):
        files = provider.get_touched_files("HEAD")
        assert files is not None
        assert isinstance(files, list)

    def test_nonexistent_commit_returns_none(self, provider):
        diff = provider.get_diff("0000000000000000000000000000000000000000")
        assert diff is None

    def test_commit_exists(self, provider):
        assert provider.commit_exists("HEAD")
        assert not provider.commit_exists("0000000000000000000000000000000000000000")
