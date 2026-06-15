"""Tests for the ProblemExtractor and ProblemStatement."""

import pytest

from commit_investigator.context.problem_extractor import ProblemExtractor, ProblemStatement
from commit_investigator.infra.jira_client import JiraIssue


def _make_jira_issue(
    key: str = "CAMEL-1234",
    summary: str = "NPE in RouteBuilder when context is null",
    description: str | None = "Stack trace:\njava.lang.NullPointerException\n  at RouteBuilder.configure()",
    priority: str | None = "Major",
) -> JiraIssue:
    return JiraIssue(
        key=key,
        summary=summary,
        description=description,
        priority=priority,
        components=["camel-core"],
        resolution=None,
        status="Open",
    )


class TestProblemStatementFrozen:
    def test_is_frozen(self) -> None:
        ps = ProblemStatement(title="t", description="d", project="camel")
        with pytest.raises(AttributeError):
            ps.title = "changed"  # type: ignore[misc]


class TestProblemStatementIsEmpty:
    def test_empty_when_both_blank(self) -> None:
        ps = ProblemStatement(title="", description="", project="camel")
        assert ps.is_empty is True

    def test_empty_when_whitespace_only(self) -> None:
        ps = ProblemStatement(title="  ", description=" \n ", project="camel")
        assert ps.is_empty is True

    def test_not_empty_with_title(self) -> None:
        ps = ProblemStatement(title="NPE in foo", description="", project="camel")
        assert ps.is_empty is False


class TestProblemStatementToPromptText:
    def test_includes_title(self) -> None:
        ps = ProblemStatement(title="NPE in foo", description="bar baz", project="camel")
        text = ps.to_prompt_text()
        assert "## Bug Report: NPE in foo" in text
        assert "bar baz" in text

    def test_no_description(self) -> None:
        ps = ProblemStatement(title="NPE in foo", description="", project="camel")
        text = ps.to_prompt_text()
        assert "## Bug Report: NPE in foo" in text


class TestProblemExtractorFromJira:
    def test_extracts_title_and_description(self) -> None:
        issue = _make_jira_issue()
        extractor = ProblemExtractor()
        ps = extractor.from_jira_issue(issue, project="camel")

        assert ps.title == "NPE in RouteBuilder when context is null"
        assert "NullPointerException" in ps.description
        assert ps.project == "camel"
        assert ps.issue_key == "CAMEL-1234"

    def test_empty_description_preserved(self) -> None:
        issue = _make_jira_issue(description=None)
        extractor = ProblemExtractor()
        ps = extractor.from_jira_issue(issue, project="camel")

        assert ps.description == ""

    def test_whitespace_description_preserved(self) -> None:
        issue = _make_jira_issue(description="   ")
        extractor = ProblemExtractor()
        ps = extractor.from_jira_issue(issue, project="camel")

        assert ps.description == "   "


class TestProblemExtractorFromRaw:
    def test_creates_from_raw_strings(self) -> None:
        extractor = ProblemExtractor()
        ps = extractor.from_raw(
            title="test bug",
            description="repro steps here",
            project="hadoop",
            issue_key="HADOOP-5678",
        )
        assert ps.title == "test bug"
        assert ps.project == "hadoop"
        assert ps.issue_key == "HADOOP-5678"
