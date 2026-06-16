"""Tests for V4 input pipeline — prepare_investigation() entry point."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from commit_investigator.extraction.jira_client import JiraIssue
from commit_investigator.infra.git_context import GitContextProvider, GitRepoNotFoundError
from commit_investigator.models.candidates import CandidateCommit, CandidateSet
from commit_investigator.retrieval.pipeline import (
    RetrievalResult,
    _widen_config,
    prepare_investigation,
)
from commit_investigator.retrieval.retriever import RetrievalConfig

REPOS_DIR = Path(__file__).resolve().parents[1] / "data" / "repos"


def _make_jira_issue() -> JiraIssue:
    return JiraIssue(
        key="CASSANDRA-7570",
        summary="CqlPagingRecordReader is broken",
        description="As mentioned on CASSANDRA-7059, it broke CPRR.",
        priority="Normal",
        components=[],
        resolution="Fixed",
        status="Resolved",
    )


def _make_candidate_set(n: int = 20) -> CandidateSet:
    commits = [
        CandidateCommit(
            commit_id=f"{i:040x}",
            rank=i,
            retrieval_signal="file_log",
            summary=f"commit {i}",
            files_changed=["Foo.java"],
            date="2024-01-01",
        )
        for i in range(1, n + 1)
    ]
    return CandidateSet(
        commits=commits,
        retrieval_metadata={
            "strategies_used": ["file_log", "keyword_grep"],
            "total_raw_candidates": n,
            "fallback_triggered": False,
        },
        temporal_bound="abc123~1",
    )


class TestWidenConfig:
    def test_doubles_per_strategy_limits(self) -> None:
        cfg = RetrievalConfig(file_log_per_file=50, keyword_grep_per_kw=30)
        widened = _widen_config(cfg)
        assert widened.file_log_per_file == 100
        assert widened.keyword_grep_per_kw == 60
        assert widened.pickaxe_per_symbol == cfg.pickaxe_per_symbol * 2
        assert widened.blame_per_file == cfg.blame_per_file * 2

    def test_preserves_max_candidates(self) -> None:
        cfg = RetrievalConfig(max_candidates=100)
        widened = _widen_config(cfg)
        assert widened.max_candidates == 100

    def test_preserves_strategies(self) -> None:
        cfg = RetrievalConfig(strategies=["file_log", "blame"])
        widened = _widen_config(cfg)
        assert widened.strategies == ["file_log", "blame"]


class TestPrepareInvestigationWithJiraIssue:
    @patch("commit_investigator.retrieval.pipeline.GitContextProvider")
    @patch("commit_investigator.retrieval.pipeline.retrieve_candidates")
    def test_normal_path(self, mock_retrieve, mock_git_cls) -> None:
        mock_git_cls.return_value = MagicMock()
        mock_retrieve.return_value = _make_candidate_set(20)

        issue = _make_jira_issue()
        result = prepare_investigation(
            source=issue,
            repo_path="/fake/repo",
            temporal_bound="abc~1",
            project="CASSANDRA",
        )

        assert isinstance(result, RetrievalResult)
        assert result.problem_statement.title == "CqlPagingRecordReader is broken"
        assert result.problem_statement.issue_key == "CASSANDRA-7570"
        assert len(result.candidate_set.commits) == 20
        assert result.metadata["retry_triggered"] is False

    @patch("commit_investigator.retrieval.pipeline.GitContextProvider")
    @patch("commit_investigator.retrieval.pipeline.retrieve_candidates")
    def test_extracts_signals(self, mock_retrieve, mock_git_cls) -> None:
        mock_git_cls.return_value = MagicMock()
        mock_retrieve.return_value = _make_candidate_set(20)

        issue = _make_jira_issue()
        result = prepare_investigation(
            source=issue,
            repo_path="/fake/repo",
            temporal_bound="abc~1",
            project="CASSANDRA",
        )

        assert "CqlPagingRecordReader" in result.problem_statement.extracted_symbols
        assert len(result.problem_statement.extracted_keywords) > 0


class TestPrepareInvestigationWithRawInput:
    @patch("commit_investigator.retrieval.pipeline.GitContextProvider")
    @patch("commit_investigator.retrieval.pipeline.retrieve_candidates")
    def test_raw_tuple_input(self, mock_retrieve, mock_git_cls) -> None:
        mock_git_cls.return_value = MagicMock()
        mock_retrieve.return_value = _make_candidate_set(15)

        result = prepare_investigation(
            source=("NPE in FooBar", "Stack trace at FooBar.java:42"),
            repo_path="/fake/repo",
            temporal_bound="abc~1",
            project="TEST",
            issue_key="TEST-123",
        )

        assert result.problem_statement.title == "NPE in FooBar"
        assert result.problem_statement.issue_key == "TEST-123"
        assert "FooBar.java" in result.problem_statement.extracted_files
        assert "FooBar" in result.problem_statement.extracted_symbols


class TestRetryPath:
    @patch("commit_investigator.retrieval.pipeline.GitContextProvider")
    @patch("commit_investigator.retrieval.pipeline.retrieve_candidates")
    def test_retry_when_few_candidates(self, mock_retrieve, mock_git_cls) -> None:
        """AC4: retry with widened config when < fallback_recency_threshold."""
        mock_git_cls.return_value = MagicMock()
        small_set = _make_candidate_set(5)
        larger_set = _make_candidate_set(30)
        mock_retrieve.side_effect = [small_set, larger_set]

        result = prepare_investigation(
            source=("bug title", "desc"),
            repo_path="/fake/repo",
            temporal_bound="abc~1",
            project="TEST",
        )

        assert result.metadata["retry_triggered"] is True
        assert len(result.candidate_set.commits) == 30
        assert mock_retrieve.call_count == 2

        second_config = mock_retrieve.call_args_list[1][0][2]
        assert second_config.file_log_per_file == 100

    @patch("commit_investigator.retrieval.pipeline.GitContextProvider")
    @patch("commit_investigator.retrieval.pipeline.retrieve_candidates")
    def test_no_retry_when_enough_candidates(self, mock_retrieve, mock_git_cls) -> None:
        mock_git_cls.return_value = MagicMock()
        mock_retrieve.return_value = _make_candidate_set(20)

        result = prepare_investigation(
            source=("bug", "desc"),
            repo_path="/fake/repo",
            temporal_bound="abc~1",
            project="TEST",
        )

        assert result.metadata["retry_triggered"] is False
        assert mock_retrieve.call_count == 1


class TestDegradedMode:
    @patch("commit_investigator.retrieval.pipeline.GitContextProvider")
    @patch("commit_investigator.retrieval.pipeline.retrieve_candidates")
    def test_returns_even_with_zero_candidates(self, mock_retrieve, mock_git_cls) -> None:
        """AC5: degraded mode — returns result even if retry still produces few."""
        mock_git_cls.return_value = MagicMock()
        empty_set = CandidateSet(
            commits=[],
            retrieval_metadata={"strategies_used": [], "total_raw_candidates": 0, "fallback_triggered": True},
        )
        mock_retrieve.return_value = empty_set

        result = prepare_investigation(
            source=("", ""),
            repo_path="/fake/repo",
            temporal_bound="abc~1",
            project="TEST",
        )

        assert isinstance(result, RetrievalResult)
        assert len(result.candidate_set.commits) == 0
        assert result.metadata["retry_triggered"] is True


class TestErrorPropagation:
    def test_repo_not_found_raises(self) -> None:
        """EC1: GitRepoNotFoundError propagates."""
        with pytest.raises(GitRepoNotFoundError):
            prepare_investigation(
                source=("title", "desc"),
                repo_path="/nonexistent/repo",
                temporal_bound="abc~1",
                project="TEST",
            )


@pytest.mark.slow
class TestPipelineEndToEnd:
    """End-to-end test with real repo (AC8)."""

    @pytest.mark.skipif(
        not (REPOS_DIR / "cassandra" / ".git").exists(),
        reason="cassandra repo not cloned at data/repos/cassandra",
    )
    def test_cassandra_real_repo(self) -> None:
        result = prepare_investigation(
            source=("CqlPagingRecordReader is broken", "As mentioned on CASSANDRA-7059, it broke CPRR."),
            repo_path=REPOS_DIR / "cassandra",
            temporal_bound="7fa93a2ca7febbff593aafef0265daa8799a9fb3~1",
            project="CASSANDRA",
            issue_key="CASSANDRA-7570",
        )

        assert not result.problem_statement.is_empty
        assert len(result.candidate_set.commits) > 0
        assert result.metadata["retry_triggered"] is False
        assert "CqlPagingRecordReader" in result.problem_statement.extracted_symbols
