"""Tests for context builder — oracle isolation and feature allowlisting."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from commit_investigator.context.context_builder import CommitContextBuilder, _NUMERIC_FEATURES

FIXTURES = Path(__file__).parent / "fixtures"


ORACLE_FIELDS = {"buggy", "fix"}
METADATA_FIELDS = {"commit_id", "project", "year", "author_date"}


def _make_csv_row(**overrides: str) -> dict[str, str]:
    """Build a realistic CSV row with all ApacheJIT columns."""
    base = {
        "commit_id": "abc123",
        "project": "apache/camel",
        "buggy": "True",
        "fix": "True",
        "year": "2015",
        "author_date": "2015-06-01",
        "la": "100",
        "ld": "20",
        "nf": "5",
        "nd": "3",
        "ns": "2",
        "ent": "3.5",
        "ndev": "1",
        "age": "30",
        "nuc": "10",
        "aexp": "50",
        "arexp": "25",
        "asexp": "12",
    }
    base.update(overrides)
    return base


def _make_builder() -> CommitContextBuilder:
    git = MagicMock()
    git.get_diff.return_value = "diff --git a/Foo.java b/Foo.java"
    git.get_commit_message.return_value = "fix: something"
    git.get_touched_files.return_value = ["Foo.java"]
    git.get_file_history.return_value = []
    return CommitContextBuilder(git)


class TestOracleIsolation:
    """Verify that ground truth labels never reach the agent."""

    def test_buggy_excluded_from_csv_features(self) -> None:
        builder = _make_builder()
        ctx = builder.build("abc123", "camel", _make_csv_row())
        assert "buggy" not in ctx.csv_features

    def test_fix_excluded_from_csv_features(self) -> None:
        builder = _make_builder()
        ctx = builder.build("abc123", "camel", _make_csv_row())
        assert "fix" not in ctx.csv_features

    def test_year_excluded_from_csv_features(self) -> None:
        builder = _make_builder()
        ctx = builder.build("abc123", "camel", _make_csv_row())
        assert "year" not in ctx.csv_features

    def test_commit_id_excluded_from_csv_features(self) -> None:
        builder = _make_builder()
        ctx = builder.build("abc123", "camel", _make_csv_row())
        assert "commit_id" not in ctx.csv_features

    def test_only_numeric_features_pass_through(self) -> None:
        """Allowlist test: csv_features keys must be a subset of _NUMERIC_FEATURES."""
        builder = _make_builder()
        ctx = builder.build("abc123", "camel", _make_csv_row())
        assert set(ctx.csv_features.keys()) == _NUMERIC_FEATURES

    def test_no_oracle_fields_in_allowlist(self) -> None:
        """The allowlist itself must not contain oracle fields."""
        assert _NUMERIC_FEATURES.isdisjoint(ORACLE_FIELDS)

    def test_numeric_features_are_floats(self) -> None:
        builder = _make_builder()
        ctx = builder.build("abc123", "camel", _make_csv_row())
        for key, val in ctx.csv_features.items():
            assert isinstance(val, float), f"{key} should be float, got {type(val)}"


class TestBundleExpandGoldenNoOp:
    """AC-1: default flags produce golden no-expand output."""

    def _fixture_builder(self, raw_diff: str | None) -> CommitContextBuilder:
        git = MagicMock()
        git.get_diff.return_value = raw_diff
        git.get_commit_message.return_value = "fix: cache lifecycle"
        git.get_touched_files.return_value = [
            "src/main/java/org/example/Foo.java",
            "src/test/java/org/example/FooTest.java",
        ]
        git.get_file_history.return_value = []
        return CommitContextBuilder(git)

    def test_default_flags_match_golden_snapshot(self) -> None:
        raw = (FIXTURES / "context_expansion_fixture_diff.txt").read_text(encoding="utf-8")
        golden = json.loads(
            (FIXTURES / "context_expansion_golden_no_expand.json").read_text(encoding="utf-8"),
        )
        ctx = self._fixture_builder(raw).build("abc123", "camel")
        assert ctx.diff == golden["diff"]
        assert "## Test Adjacency" not in (ctx.diff or "")
        assert "## Git Blame" not in (ctx.diff or "")
        assert ctx.missing_reasons == golden["missing_reasons"]
        assert ctx.truncation_metadata is not None
        assert ctx.truncation_metadata.included_files == golden["truncation_metadata"]["included_files"]
        assert ctx.truncation_metadata.truncated_files == golden["truncation_metadata"]["truncated_files"]


class TestContextExpansionAdjacency:
    def test_injects_paired_test_hunks_on_identifier_overlap(self) -> None:
        raw = (FIXTURES / "context_expansion_fixture_diff.txt").read_text(encoding="utf-8")
        git = MagicMock()
        git.get_diff.return_value = raw
        git.get_commit_message.return_value = "fix"
        git.get_touched_files.return_value = ["Foo.java", "FooTest.java"]
        git.get_file_history.return_value = []
        ctx = CommitContextBuilder(git).build(
            "abc123",
            "camel",
            include_test_adjacency=True,
        )
        assert ctx.diff is not None
        assert "## Test Adjacency" in ctx.diff
        assert "FooTest.java" in ctx.diff
        assert "cacheManager" in ctx.diff

    def test_no_section_when_no_identifier_overlap(self) -> None:
        raw = """diff --git a/Foo.java b/Foo.java
--- a/Foo.java
+++ b/Foo.java
@@ -1 +1,2 @@
 class Foo {}
diff --git a/BarTest.java b/BarTest.java
--- a/BarTest.java
+++ b/BarTest.java
@@ -1 +1,2 @@
 class BarTest {}
"""
        git = MagicMock()
        git.get_diff.return_value = raw
        git.get_commit_message.return_value = "fix"
        git.get_touched_files.return_value = ["Foo.java", "BarTest.java"]
        git.get_file_history.return_value = []
        ctx = CommitContextBuilder(git).build(
            "abc123",
            "camel",
            include_test_adjacency=True,
        )
        assert "## Test Adjacency" not in (ctx.diff or "")
        assert any("test_adjacency" in r for r in ctx.missing_reasons)


class TestBundleExpandBlame:
    def test_blame_called_at_most_twice(self) -> None:
        raw = """diff --git a/Alpha.java b/Alpha.java
--- a/Alpha.java
+++ b/Alpha.java
@@ -1,3 +1,4 @@
 class Alpha {
+    if (value == null) return;
 }
diff --git a/Beta.java b/Beta.java
--- a/Beta.java
+++ b/Beta.java
@@ -1,3 +1,4 @@
 class Beta {
+    if (other == null) return;
 }
diff --git a/Gamma.java b/Gamma.java
--- a/Gamma.java
+++ b/Gamma.java
@@ -1,3 +1,4 @@
 class Gamma {
+    if (third == null) return;
 }
"""
        git = MagicMock()
        git.get_diff.return_value = raw
        git.get_commit_message.return_value = "fix"
        git.get_touched_files.return_value = ["Alpha.java", "Beta.java", "Gamma.java"]
        git.get_file_history.return_value = []
        git.get_blame_snippet.return_value = "blame line"
        CommitContextBuilder(git).build(
            "abc123",
            "camel",
            include_blame_snippets=True,
        )
        assert git.get_blame_snippet.call_count <= 2
        for call in git.get_blame_snippet.call_args_list:
            assert call.kwargs.get("context_lines") == 2 or call.args[-1] == 2


class TestBundleExpandBudget:
    def test_combined_expansion_stays_within_max_diff_chars(self) -> None:
        padding = "x" * 500
        raw = f"""diff --git a/Foo.java b/Foo.java
--- a/Foo.java
+++ b/Foo.java
@@ -1,3 +1,4 @@
 class Foo {{
+    cacheManager.init();
 {padding}
 }}
diff --git a/FooTest.java b/FooTest.java
--- a/FooTest.java
+++ b/FooTest.java
@@ -1,3 +1,4 @@
 class FooTest {{
+    assert cacheManager != null;
 {padding}
 }}
"""
        git = MagicMock()
        git.get_diff.return_value = raw
        git.get_commit_message.return_value = "fix"
        git.get_touched_files.return_value = ["Foo.java", "FooTest.java"]
        git.get_file_history.return_value = []
        git.get_blame_snippet.return_value = "b" * 800
        max_chars = 4000
        ctx = CommitContextBuilder(git).build(
            "abc123",
            "camel",
            max_diff_chars=max_chars,
            include_test_adjacency=True,
            include_blame_snippets=True,
        )
        assert ctx.diff is not None
        assert len(ctx.diff) <= max_chars
        assert "(expansion truncated for budget)" in ctx.diff or len(ctx.diff) <= max_chars


class TestBundleExpandRawDiffNone:
    def test_raw_diff_none_skips_expansion(self) -> None:
        git = MagicMock()
        git.get_diff.return_value = None
        git.get_commit_message.return_value = "fix"
        git.get_touched_files.return_value = ["Foo.java"]
        git.get_file_history.return_value = []
        ctx = CommitContextBuilder(git).build(
            "abc123",
            "camel",
            include_test_adjacency=True,
            include_blame_snippets=True,
        )
        assert ctx.diff in (None, "")
        assert "## Test Adjacency" not in (ctx.diff or "")
        assert "## Git Blame" not in (ctx.diff or "")
        git.get_blame_snippet.assert_not_called()
        assert any("Diff unavailable" in r for r in ctx.missing_reasons)

