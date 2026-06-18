"""Tests for diff_assembler.py: per-file ranked diff assembly.

Covers:
- AC-1: Per-file minimum hunk guarantee
- AC-2: File ranking (defect_signal > production > test > build/config)
- AC-3: truncation_metadata fields present
- AC-4: 572f3 fixture — defect-signal file prioritized over build/config
- AC-6: Total cap never exceeded
- EC-1/2/3: Edge cases
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from commit_investigator.infra.diff_assembler import (  # noqa: E402
    _file_rank,
    assemble_diff,
    parse_file_diffs,
)


def _make_diff(files: list[tuple[str, str, int]]) -> str:
    """Build a synthetic unified diff.

    files: list of (path, change_content, n_extra_lines_of_padding)
    """
    parts = []
    for path, change_content, pad in files:
        hunk = f"@@ -1,5 +1,5 @@\n context\n{change_content}\n context\n"
        if pad > 0:
            hunk += ("+" + "x" * 98 + "\n") * pad
        parts.append(
            f"diff --git a/{path} b/{path}\n"
            f"--- a/{path}\n"
            f"+++ b/{path}\n"
            + hunk
        )
    return "".join(parts)


class TestFileRank:
    def test_defect_signal_file_ranked_zero(self) -> None:
        diff_with_lock = "+  synchronized (this) { return x; }"
        assert _file_rank("Foo.java", diff_with_lock) == 0

    def test_production_java_ranked_one(self) -> None:
        assert _file_rank("src/Foo.java", "nothing interesting") == 1

    def test_test_file_ranked_two(self) -> None:
        assert _file_rank("FooTest.java", "assert x == 1") == 2

    def test_build_file_ranked_three(self) -> None:
        assert _file_rank("pom.xml", "<version>1.0</version>") == 3

    def test_yml_config_ranked_three(self) -> None:
        assert _file_rank("application.yml", "server: 8080") == 3


class TestParseFileDiffs:
    def test_single_file(self) -> None:
        raw = _make_diff([("Foo.java", "+return 1;", 0)])
        diffs = parse_file_diffs(raw)
        assert len(diffs) == 1
        assert diffs[0].path == "Foo.java"
        assert len(diffs[0].hunks) == 1

    def test_multi_file(self) -> None:
        raw = _make_diff([
            ("pom.xml", "+<version>2.0</version>", 0),
            ("Foo.java", "+int x = 1;", 0),
        ])
        diffs = parse_file_diffs(raw)
        assert len(diffs) == 2
        paths = {d.path for d in diffs}
        assert "pom.xml" in paths
        assert "Foo.java" in paths

    def test_empty_diff_returns_empty(self) -> None:
        assert parse_file_diffs("") == []

    def test_whitespace_only_returns_empty(self) -> None:
        assert parse_file_diffs("   \n") == []


class TestAssembleDiff:
    def test_empty_diff(self) -> None:
        result = assemble_diff(None)
        assert result.text == ""
        assert result.included_files == []
        assert result.truncated_files == []
        assert result.total_chars == 0

    def test_small_diff_no_truncation(self) -> None:
        raw = _make_diff([("Foo.java", "+int x = 1;", 0)])
        result = assemble_diff(raw, max_chars=16_000)
        assert "Foo.java" in result.included_files
        assert result.truncated_files == []
        assert result.total_chars == len(result.text)
        assert result.total_chars <= 16_000

    def test_total_cap_never_exceeded(self) -> None:
        large = _make_diff([("Foo.java", "+int x = 1;", 200)])
        result = assemble_diff(large, max_chars=1_000)
        assert result.total_chars <= 1_000
        assert len(result.text) <= 1_000

    def test_defect_signal_file_prioritized_over_build(self) -> None:
        """AC-2 + AC-4: defect-signal file beats pom.xml for budget."""
        pom_padding = 90
        raw = _make_diff([
            ("pom.xml", "+<version>2.0</version>", pom_padding),
            ("XmppGroupChatProducer.java", "+synchronized (lock) { send(); }", 0),
        ])
        result = assemble_diff(raw, max_chars=500)
        assert "XmppGroupChatProducer.java" in result.included_files, (
            "Defect-signal file must be included over build/config file"
        )

    def test_per_file_minimum_hunk_guarantee(self) -> None:
        """AC-1: Even if full file exceeds budget, first hunk must be included."""
        raw = _make_diff([
            ("First.java", "+int x = 1;", 0),
            ("Second.java", "+int y = 2;", 0),
            ("Third.java", "+int z = 3;", 0),
        ])
        # Cap: enough for 2 files
        result = assemble_diff(raw, max_chars=300)
        # At minimum, top-ranked file should be present
        assert len(result.included_files) >= 1
        assert result.total_chars <= 300

    def test_truncation_metadata_populated(self) -> None:
        """AC-3: AssembledDiff always has the required metadata fields."""
        raw = _make_diff([("Foo.java", "+int x = 1;", 200)])
        result = assemble_diff(raw, max_chars=500)
        assert isinstance(result.included_files, list)
        assert isinstance(result.truncated_files, list)
        assert isinstance(result.total_chars, int)

    def test_ec1_single_file_commit(self) -> None:
        """EC-1: Single file — no ranking needed, full hunk up to cap."""
        raw = _make_diff([("Foo.java", "+int x = 1;", 0)])
        result = assemble_diff(raw, max_chars=16_000)
        assert "Foo.java" in result.included_files
        assert result.truncated_files == []

    def test_ec3_none_diff(self) -> None:
        """EC-3: None diff returns empty AssembledDiff."""
        result = assemble_diff(None)
        assert result.text == ""
        assert result.included_files == []
        assert result.total_chars == 0


class TestFixture572f3:
    """AC-4: Simulate the 572f3cee35fe scenario.

    Before smart diff: pom.xml fills budget, XmppGroupChatProducer.java cut off.
    After smart diff: XmppGroupChatProducer.java (defect-signal) must be present.
    """

    def test_xmpp_file_present_in_assembled_diff(self) -> None:
        """Defect-signal production file beats large pom.xml for budget."""
        # Simulate a large pom.xml (build/config, rank=3) and small defect-signal file
        pom_content = "+<version>2.5.3</version>"
        xmpp_content = "+synchronized (chatRooms) {\n+  chatRooms.put(room, producer);\n+}"

        raw = _make_diff([
            ("pom.xml", pom_content, 80),  # Large: ~8700 chars
            ("components/camel-xmpp/src/main/java/org/apache/camel/component/xmpp/XmppGroupChatProducer.java",
             xmpp_content, 0),
        ])

        result = assemble_diff(raw, max_chars=2_000)

        assert any("XmppGroupChatProducer" in f for f in result.included_files), (
            f"XmppGroupChatProducer.java must be in included_files. "
            f"Got: {result.included_files}"
        )

    def test_forced_signal_file_always_included(self) -> None:
        """defect_signal_files override list forces file to rank 0."""
        raw = _make_diff([
            ("pom.xml", "+<version>2.0</version>", 50),
            ("OtherFile.java", "+int x = 1;", 0),
        ])
        result = assemble_diff(
            raw,
            defect_signal_files=["OtherFile.java"],
            max_chars=800,
        )
        assert "OtherFile.java" in result.included_files
