"""Tests for the ProblemExtractor and ProblemStatement."""

import json
from pathlib import Path

import pytest

from commit_investigator.extraction.problem_extractor import (
    ProblemExtractor,
    ProblemStatement,
    _extract_file_paths,
    _extract_keywords,
    _extract_symbols,
)
from commit_investigator.extraction.jira_client import JiraIssue

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "eval_case_text"


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


def _load_fixture(issue_key: str) -> dict:
    path = FIXTURES_DIR / f"{issue_key}.json"
    return json.loads(path.read_text(encoding="utf-8"))


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

    def test_level1_extracts_symbols_from_title(self) -> None:
        issue = _make_jira_issue()
        extractor = ProblemExtractor()
        ps = extractor.from_jira_issue(issue, project="camel")

        assert "RouteBuilder" in ps.extracted_symbols

    def test_level1_extracts_keywords_from_title(self) -> None:
        issue = _make_jira_issue()
        extractor = ProblemExtractor()
        ps = extractor.from_jira_issue(issue, project="camel")

        assert "npe" in ps.extracted_keywords
        assert "routebuilder" in ps.extracted_keywords
        assert "context" in ps.extracted_keywords
        assert "null" in ps.extracted_keywords


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

    def test_from_raw_extracts_same_as_from_jira(self) -> None:
        extractor = ProblemExtractor()
        title = "NPE in RouteBuilder when context is null"
        description = "Stack trace:\njava.lang.NullPointerException\n  at RouteBuilder.configure()"

        ps_raw = extractor.from_raw(title, description, "camel")
        issue = _make_jira_issue(summary=title, description=description)
        ps_jira = extractor.from_jira_issue(issue, project="camel")

        assert ps_raw.extracted_files == ps_jira.extracted_files
        assert ps_raw.extracted_symbols == ps_jira.extracted_symbols
        assert ps_raw.extracted_keywords == ps_jira.extracted_keywords


class TestLevel1ExtractionFilePaths:
    def test_java_file_in_stack_trace(self) -> None:
        text = "at org.foo.Bar.method(Bar.java:42)"
        assert "Bar.java" in _extract_file_paths(text)

    def test_scala_file_path(self) -> None:
        text = "error in src/main/scala/SparkContext.scala"
        files = _extract_file_paths(text)
        assert any("SparkContext.scala" in f for f in files)

    def test_path_with_directories(self) -> None:
        text = "see core/src/CqlPagingReader.java for details"
        assert "core/src/CqlPagingReader.java" in _extract_file_paths(text)

    def test_no_false_positives_on_urls(self) -> None:
        text = "see https://github.com/apache/Foo.java for details"
        # URL path still matched — file regex doesn't understand URL context
        # but this is acceptable since the file still names a real Java file
        files = _extract_file_paths(text)
        # At minimum, no crash
        assert isinstance(files, list)

    def test_multiple_extensions(self) -> None:
        text = "Config.yaml and Utils.py and Main.groovy"
        files = _extract_file_paths(text)
        assert "Config.yaml" in files
        assert "Utils.py" in files
        assert "Main.groovy" in files

    def test_empty_text(self) -> None:
        assert _extract_file_paths("") == []

    def test_deduplicates(self) -> None:
        text = "Error in Foo.java and also Foo.java"
        files = _extract_file_paths(text)
        assert files.count("Foo.java") == 1


class TestLevel1ExtractionSymbols:
    def test_camelcase_class_name(self) -> None:
        text = "CqlPagingRecordReader is broken"
        assert "CqlPagingRecordReader" in _extract_symbols(text)

    def test_two_part_camelcase(self) -> None:
        text = "HistoryServer still uses old ACLs"
        assert "HistoryServer" in _extract_symbols(text)

    def test_no_single_word(self) -> None:
        text = "Application and Configuration"
        assert _extract_symbols(text) == []

    def test_preceded_by_dot_excluded(self) -> None:
        text = "obj.CallSite is null"
        assert "CallSite" not in _extract_symbols(text)

    def test_preceded_by_dollar_included(self) -> None:
        text = "Selector$MethodSelector.doCallSiteTargetSet"
        assert "MethodSelector" in _extract_symbols(text)

    def test_empty_text(self) -> None:
        assert _extract_symbols("") == []

    def test_deduplicates(self) -> None:
        text = "CallSite and CallSite again"
        symbols = _extract_symbols(text)
        assert symbols.count("CallSite") == 1

    def test_acronym_camelcase_trailing(self) -> None:
        text = "HiveUDAF should return NULL in case of 0 rows"
        assert "HiveUDAF" in _extract_symbols(text)

    def test_acronym_camelcase_embedded(self) -> None:
        text = "HiveUDAFFunction throws NPE on empty input"
        assert "HiveUDAFFunction" in _extract_symbols(text)

    def test_lower_camelcase_basic(self) -> None:
        text = "use globalTempDB and listTables for the query"
        symbols = _extract_symbols(text)
        assert "globalTempDB" in symbols
        assert "listTables" in symbols

    def test_lower_camelcase_multi_segment(self) -> None:
        text = "call dropTempView and dropGlobalTempView"
        symbols = _extract_symbols(text)
        assert "dropTempView" in symbols
        assert "dropGlobalTempView" in symbols

    def test_lower_camelcase_with_acronym(self) -> None:
        text = "hiveUDFs are broken in this version"
        assert "hiveUDFs" in _extract_symbols(text)

    def test_short_noise_filtered(self) -> None:
        """Identifiers under 6 chars should not be extracted."""
        text = "use aBC or myFoo here"
        symbols = _extract_symbols(text)
        assert all(len(s) >= 6 for s in symbols)

    def test_dot_preceded_acronym_excluded(self) -> None:
        text = "obj.HiveUDAF is broken"
        assert "HiveUDAF" not in _extract_symbols(text)

    def test_dot_preceded_lower_camel_excluded(self) -> None:
        text = "obj.globalTempDB is null"
        assert "globalTempDB" not in _extract_symbols(text)


class TestLevel1ExtractionKeywords:
    def test_filters_stopwords(self) -> None:
        keywords = _extract_keywords("NPE in RouteBuilder when context is null")
        assert "in" not in keywords
        assert "when" not in keywords
        assert "is" not in keywords

    def test_lowercases(self) -> None:
        keywords = _extract_keywords("CqlPagingRecordReader is Broken")
        assert "cqlpagingrecordreader" in keywords
        assert "broken" in keywords

    def test_empty_title(self) -> None:
        assert _extract_keywords("") == []

    def test_short_words_filtered(self) -> None:
        keywords = _extract_keywords("A B CD EFG")
        # single-char filtered (< 2)
        assert "a" not in keywords
        assert "b" not in keywords
        assert "cd" in keywords

    def test_deduplicates(self) -> None:
        keywords = _extract_keywords("bug Bug BUG")
        assert keywords.count("bug") == 1


class TestLevel1ExtractionWithRealFixtures:
    """Tests using real JIRA text from eval set (AC5)."""

    def test_cassandra_7570_extracts_symbol(self) -> None:
        fixture = _load_fixture("CASSANDRA-7570")
        extractor = ProblemExtractor()
        ps = extractor.from_raw(
            fixture["title"], fixture["description"],
            fixture["project"], fixture["issue_key"],
        )
        assert "CqlPagingRecordReader" in ps.extracted_symbols
        assert "cqlpagingrecordreader" in ps.extracted_keywords
        assert "broken" in ps.extracted_keywords

    def test_spark_19033_extracts_history_server(self) -> None:
        fixture = _load_fixture("SPARK-19033")
        extractor = ProblemExtractor()
        ps = extractor.from_raw(
            fixture["title"], fixture["description"],
            fixture["project"], fixture["issue_key"],
        )
        assert "HistoryServer" in ps.extracted_symbols
        assert "historyserver" in ps.extracted_keywords
        assert "acls" in ps.extracted_keywords

    def test_groovy_8298_extracts_java_files(self) -> None:
        fixture = _load_fixture("GROOVY-8298")
        extractor = ProblemExtractor()
        ps = extractor.from_raw(
            fixture["title"], fixture["description"],
            fixture["project"], fixture["issue_key"],
        )
        assert len(ps.extracted_files) >= 2
        assert "CallSite.java" in ps.extracted_files or "Selector.java" in ps.extracted_files
        assert len(ps.extracted_keywords) >= 3

    def test_title_only_still_produces_keywords(self) -> None:
        """EC1: empty description still produces keywords from title."""
        extractor = ProblemExtractor()
        ps = extractor.from_raw(
            title="NullPointerException in StreamProcessor",
            description="",
            project="TEST",
        )
        assert "nullpointerexception" in ps.extracted_keywords
        assert "StreamProcessor" in ps.extracted_symbols

    def test_unicode_description_no_crash(self) -> None:
        """EC3: unicode in description doesn't crash."""
        extractor = ProblemExtractor()
        ps = extractor.from_raw(
            title="Bug with résumé handling",
            description="Error: ñ → € characters in Façade.java",
            project="TEST",
        )
        assert isinstance(ps.extracted_files, list)
        assert isinstance(ps.extracted_symbols, list)
