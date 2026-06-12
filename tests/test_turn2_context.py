"""Tests for turn-2 context injection."""

from __future__ import annotations

from commit_investigator.context.context_builder import InvestigationContext
from commit_investigator.context.git_context import GitContextProvider
from commit_investigator.context.smart_diff import AssembledDiff
from commit_investigator.context.turn2_context import build_turn2_follow_up, parse_diff_touched_lines


class _FakeGitProvider:
    """Minimal git provider stub for turn-2 context tests."""

    def get_file_at_commit(self, commit_id: str, path: str) -> str | None:
        return f"// full content of {path} at {commit_id[:8]}"

    def get_blame_snippet(
        self,
        commit_id: str,
        path: str,
        line_start: int,
        line_end: int,
        context_lines: int = 2,
    ) -> str | None:
        return f"{commit_id[:8]} ({path}:{line_start}-{line_end}) author line"


def _context_with_truncation() -> InvestigationContext:
    return InvestigationContext(
        commit_id="572f3cee35feabc1234567890abcdef",
        project="camel",
        diff="diff --git a/Foo.java b/Foo.java\n+change",
        message="fix",
        touched_files=["Foo.java", "pom.xml"],
        csv_features={},
        file_histories={},
        author_stats=None,
        truncation_metadata=AssembledDiff(
            text="partial",
            included_files=["Bar.java"],
            truncated_files=["Hidden.java"],
            total_chars=100,
        ),
    )


class TestParseDiffTouchedLines:
    def test_parses_added_line_numbers(self):
        diff = (
            "diff --git a/src/Main.java b/src/Main.java\n"
            "+++ b/src/Main.java\n"
            "@@ -10,3 +10,4 @@\n"
            " context\n"
            "-removed\n"
            "+added\n"
        )
        lines = parse_diff_touched_lines(diff)
        assert "src/Main.java" in lines
        assert 10 in lines["src/Main.java"]
        assert 11 in lines["src/Main.java"]


class TestBuildTurn2FollowUp:
    def test_truncated_files_section_present(self):
        ctx = _context_with_truncation()
        bundle = build_turn2_follow_up(ctx, _FakeGitProvider())  # type: ignore[arg-type]
        assert "## Truncated Files" in bundle.message
        assert "### Hidden.java" in bundle.message
        assert bundle.has_truncated_section

    def test_blame_section_for_production_files(self):
        ctx = InvestigationContext(
            commit_id="409664582f53abc",
            project="camel",
            diff=(
                "diff --git a/src/Producer.java b/src/Producer.java\n"
                "+++ b/src/Producer.java\n"
                "@@ -5,3 +5,4 @@\n"
                " unchanged\n"
                "+new line\n"
            ),
            message="fix",
            touched_files=["src/Producer.java"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        bundle = build_turn2_follow_up(ctx, _FakeGitProvider())  # type: ignore[arg-type]
        assert "## Git Blame" in bundle.message
        assert "Producer.java" in bundle.message
        assert bundle.has_blame_section

    def test_no_generic_follow_up_strings(self):
        ctx = _context_with_truncation()
        bundle = build_turn2_follow_up(ctx, _FakeGitProvider())  # type: ignore[arg-type]
        forbidden = [
            "Continue the investigation",
            "think harder",
            "Focus on areas of uncertainty",
        ]
        for phrase in forbidden:
            assert phrase not in bundle.message

    def test_skips_test_files_in_blame(self):
        ctx = InvestigationContext(
            commit_id="abc123",
            project="camel",
            diff=(
                "diff --git a/FooTest.java b/FooTest.java\n"
                "+++ b/FooTest.java\n"
                "@@ -1,1 +1,2 @@\n"
                "+test\n"
            ),
            message="test",
            touched_files=["FooTest.java"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        bundle = build_turn2_follow_up(ctx, _FakeGitProvider())  # type: ignore[arg-type]
        assert "## Git Blame" not in bundle.message
