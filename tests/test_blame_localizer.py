"""Tests for Phase 0 blame localizer (reverse SZZ)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from commit_investigator.localization.blame_localizer import (
    STRATEGY_NAME,
    _blame_lines,
    _filter_source_files,
    _get_old_side_hunks,
    localize_via_fix_diff,
)


class TestFilterSourceFiles:
    def test_keeps_java_files(self) -> None:
        files = ["src/main/Foo.java", "README.md", "pom.xml"]
        result = _filter_source_files(files, frozenset({".java"}))
        assert result == ["src/main/Foo.java"]

    def test_excludes_changelog(self) -> None:
        files = ["CHANGES.txt", "src/Foo.java"]
        result = _filter_source_files(files, frozenset({".java", ".txt"}))
        assert result == ["src/Foo.java"]

    def test_excludes_pom(self) -> None:
        files = ["pom.xml", "build.gradle"]
        result = _filter_source_files(files, frozenset({".xml", ".gradle"}))
        assert result == []

    def test_multiple_extensions(self) -> None:
        files = ["A.java", "B.scala", "C.groovy", "D.py", "E.txt"]
        result = _filter_source_files(
            files, frozenset({".java", ".scala", ".groovy", ".py"}),
        )
        assert result == ["A.java", "B.scala", "C.groovy", "D.py"]

    def test_empty_input(self) -> None:
        assert _filter_source_files([], frozenset({".java"})) == []


class TestGetOldSideHunks:
    @patch("commit_investigator.localization.blame_localizer._run_git")
    def test_parses_unified_diff(self, mock_git: object) -> None:
        mock_git.return_value = (  # type: ignore[attr-defined]
            "diff --git a/Foo.java b/Foo.java\n"
            "--- a/Foo.java\n"
            "+++ b/Foo.java\n"
            "@@ -10,5 +10,8 @@\n"
            " context\n"
            "-old line\n"
            "+new line\n"
            "@@ -30,3 +33,4 @@\n"
            " more context\n"
        )
        hunks = _get_old_side_hunks(Path("/fake"), "abc123", "Foo.java")
        assert hunks == [(10, 14), (30, 32)]

    @patch("commit_investigator.localization.blame_localizer._run_git")
    def test_single_line_hunk(self, mock_git: object) -> None:
        mock_git.return_value = (  # type: ignore[attr-defined]
            "@@ -42 +42,2 @@\n"
            "-old\n"
            "+new1\n"
            "+new2\n"
        )
        hunks = _get_old_side_hunks(Path("/fake"), "abc123", "X.java")
        assert hunks == [(42, 42)]

    @patch("commit_investigator.localization.blame_localizer._run_git")
    def test_empty_diff(self, mock_git: object) -> None:
        mock_git.return_value = ""  # type: ignore[attr-defined]
        hunks = _get_old_side_hunks(Path("/fake"), "abc123", "X.java")
        assert hunks == []


class TestBlameLines:
    @patch("commit_investigator.localization.blame_localizer._run_git")
    def test_extracts_shas(self, mock_git: object) -> None:
        mock_git.return_value = (  # type: ignore[attr-defined]
            "aabbccddee1122334455667788990011aabbccdd 10 10 1\n"
            "author Name\n"
            "1122334455667788990011aabbccddeeff001122 11 11 1\n"
            "author Name2\n"
        )
        shas = _blame_lines(Path("/fake"), "ref", "X.java", 10, 11)
        assert shas == [
            "aabbccddee1122334455667788990011aabbccdd",
            "1122334455667788990011aabbccddeeff001122",
        ]

    @patch("commit_investigator.localization.blame_localizer._run_git")
    def test_deduplicates(self, mock_git: object) -> None:
        mock_git.return_value = (  # type: ignore[attr-defined]
            "aabbccddee1122334455667788990011aabbccdd 10 10 1\n"
            "aabbccddee1122334455667788990011aabbccdd 11 11 1\n"
        )
        shas = _blame_lines(Path("/fake"), "ref", "X.java", 10, 11)
        assert len(shas) == 1

    @patch("commit_investigator.localization.blame_localizer._run_git")
    def test_filters_zero_sha(self, mock_git: object) -> None:
        mock_git.return_value = (  # type: ignore[attr-defined]
            "0000000000000000000000000000000000000000 10 10 1\n"
        )
        shas = _blame_lines(Path("/fake"), "ref", "X.java", 10, 10)
        assert shas == []

    @patch("commit_investigator.localization.blame_localizer._run_git")
    def test_empty_output(self, mock_git: object) -> None:
        mock_git.return_value = ""  # type: ignore[attr-defined]
        shas = _blame_lines(Path("/fake"), "ref", "X.java", 10, 10)
        assert shas == []


class TestLocalizeViaFixDiff:
    @patch("commit_investigator.localization.blame_localizer._blame_lines")
    @patch("commit_investigator.localization.blame_localizer._get_old_side_hunks")
    @patch("commit_investigator.localization.blame_localizer._filter_source_files")
    @patch("commit_investigator.localization.blame_localizer._get_fix_changed_files")
    def test_normal_hit(
        self, mock_files: object, mock_filter: object,
        mock_hunks: object, mock_blame: object,
    ) -> None:
        mock_files.return_value = ["Foo.java", "Bar.java"]  # type: ignore[attr-defined]
        mock_filter.return_value = ["Foo.java"]  # type: ignore[attr-defined]
        mock_hunks.return_value = [(10, 20)]  # type: ignore[attr-defined]
        mock_blame.return_value = [  # type: ignore[attr-defined]
            "aabbccddee1122334455667788990011aabbccdd",
            "1122334455667788990011aabbccddeeff001122",
        ]

        hits = localize_via_fix_diff(Path("/fake"), "abc123")
        assert len(hits) == 2
        assert all(h.strategy == STRATEGY_NAME for h in hits)
        assert hits[0].commit_id == "aabbccddee1122334455667788990011aabbccdd"
        assert hits[0].rank == 1
        assert hits[1].rank == 2

    @patch("commit_investigator.localization.blame_localizer._get_fix_changed_files")
    def test_no_files_in_diff(self, mock_files: object) -> None:
        mock_files.return_value = []  # type: ignore[attr-defined]
        hits = localize_via_fix_diff(Path("/fake"), "abc123")
        assert hits == []

    @patch("commit_investigator.localization.blame_localizer._filter_source_files")
    @patch("commit_investigator.localization.blame_localizer._get_fix_changed_files")
    def test_no_source_files(self, mock_files: object, mock_filter: object) -> None:
        mock_files.return_value = ["CHANGES.txt", "README.md"]  # type: ignore[attr-defined]
        mock_filter.return_value = []  # type: ignore[attr-defined]
        hits = localize_via_fix_diff(Path("/fake"), "abc123")
        assert hits == []

    @patch("commit_investigator.localization.blame_localizer._blame_lines")
    @patch("commit_investigator.localization.blame_localizer._get_old_side_hunks")
    @patch("commit_investigator.localization.blame_localizer._filter_source_files")
    @patch("commit_investigator.localization.blame_localizer._get_fix_changed_files")
    def test_empty_hunks(
        self, mock_files: object, mock_filter: object,
        mock_hunks: object, mock_blame: object,
    ) -> None:
        mock_files.return_value = ["Foo.java"]  # type: ignore[attr-defined]
        mock_filter.return_value = ["Foo.java"]  # type: ignore[attr-defined]
        mock_hunks.return_value = []  # type: ignore[attr-defined]
        hits = localize_via_fix_diff(Path("/fake"), "abc123")
        assert hits == []
        mock_blame.assert_not_called()

    @patch("commit_investigator.localization.blame_localizer._blame_lines")
    @patch("commit_investigator.localization.blame_localizer._get_old_side_hunks")
    @patch("commit_investigator.localization.blame_localizer._filter_source_files")
    @patch("commit_investigator.localization.blame_localizer._get_fix_changed_files")
    def test_sha_dedup_across_files(
        self, mock_files: object, mock_filter: object,
        mock_hunks: object, mock_blame: object,
    ) -> None:
        """Same SHA from two different files should appear only once."""
        sha = "aabbccddee1122334455667788990011aabbccdd"
        mock_files.return_value = ["A.java", "B.java"]  # type: ignore[attr-defined]
        mock_filter.return_value = ["A.java", "B.java"]  # type: ignore[attr-defined]
        mock_hunks.return_value = [(1, 5)]  # type: ignore[attr-defined]
        mock_blame.return_value = [sha]  # type: ignore[attr-defined]

        hits = localize_via_fix_diff(Path("/fake"), "abc123")
        assert len(hits) == 1
        assert hits[0].commit_id == sha


class TestLocalizeIntegration:
    """Integration test using a real repo (skip if not available)."""

    @pytest.mark.skipif(
        not (Path(__file__).resolve().parents[1] / "data" / "repos" / "groovy" / ".git").exists(),
        reason="groovy repo not cloned",
    )
    def test_groovy_5003_real_repo(self) -> None:
        """GROOVY-5003 fix should blame a GT commit (ca13b1f1a5e3...)."""
        repo = Path(__file__).resolve().parents[1] / "data" / "repos" / "groovy"
        fix = "3c47089c9573df7d01227f07d3a6fac5886a61eb"
        gt_prefix = "ca13b1f1a5e3"

        hits = localize_via_fix_diff(repo, fix)
        assert len(hits) > 0
        assert any(h.commit_id.startswith(gt_prefix) for h in hits)
        assert all(h.strategy == STRATEGY_NAME for h in hits)
