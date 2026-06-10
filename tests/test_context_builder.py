"""Tests for context builder — oracle isolation and feature allowlisting."""

from unittest.mock import MagicMock

from commit_investigator.context_builder import CommitContextBuilder, _NUMERIC_FEATURES


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
