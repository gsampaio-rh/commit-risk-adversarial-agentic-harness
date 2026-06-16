"""Tests for deterministic baselines."""

from pathlib import Path

import pytest

from commit_investigator.infra.git_context import GitContextProvider
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.eval.baselines import (
    _extract_class_names,
    _extract_file_hints,
    file_history_recency,
    git_blame_naive,
)


REPO_ROOT = Path(__file__).resolve().parents[1]

skip_no_git = pytest.mark.skipif(
    not (REPO_ROOT / ".git").exists(),
    reason="No git repo at project root",
)


class TestExtractFileHints:
    def test_finds_java_file(self) -> None:
        hints = _extract_file_hints("Error in RouteBuilder.java when context is null")
        assert "RouteBuilder.java" in hints

    def test_finds_python_file(self) -> None:
        hints = _extract_file_hints("Fix the bug in src/utils/parser.py")
        assert "src/utils/parser.py" in hints

    def test_no_files(self) -> None:
        hints = _extract_file_hints("General description of a bug")
        assert isinstance(hints, list)


class TestExtractClassNames:
    def test_finds_camel_case(self) -> None:
        names = _extract_class_names("Error in RouteBuilder when CamelContext starts")
        assert "RouteBuilder" in names
        assert "CamelContext" in names

    def test_no_classes(self) -> None:
        assert _extract_class_names("simple lowercase text") == []


@skip_no_git
class TestGitBlameNaive:
    def test_returns_baseline_result(self) -> None:
        problem = ProblemStatement(
            title="Bug in AGENTS.md processing",
            description="Error when reading AGENTS.md file",
            project="test",
        )
        git = GitContextProvider(REPO_ROOT)
        result = git_blame_naive(problem, git)

        assert result.name == "git-blame-naive"
        assert result.report.problem_title == problem.title
        assert result.report.metadata["baseline"] == "git-blame-naive"


@skip_no_git
class TestFileHistoryRecency:
    def test_returns_baseline_result(self) -> None:
        problem = ProblemStatement(
            title="Bug in AGENTS.md",
            description="Error processing AGENTS.md content",
            project="test",
        )
        git = GitContextProvider(REPO_ROOT)
        result = file_history_recency(problem, git)

        assert result.name == "file-history-recency"
        assert result.report.problem_title == problem.title
