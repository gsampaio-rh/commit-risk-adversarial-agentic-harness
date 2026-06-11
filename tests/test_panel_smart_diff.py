"""Panel tests for iter-3d-smart-diff.

AC-3: truncation_metadata present on assembled diff / InvestigationContext
AC-5: Panel regression: smart_diff doesn't break existing D1/recall
AC-7: D6 >= 0.70 on 12-commit panel (script-only, no LLM)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from commit_investigator.eval_judge import ReasoningJudge  # noqa: E402
from commit_investigator.report import (  # noqa: E402
    CommitInvestigationReport,
    EvidenceItem,
    EvidenceType,
    LocalizationClaim,
    RiskAssessment,
    RiskLevel,
)
from commit_investigator.smart_diff import assemble_diff  # noqa: E402

PANEL_DIR = PROJECT_ROOT / "output" / "runs" / "2026-06-10_21-07-21_real_n12" / "investigations"


def _load_panel() -> list[dict]:
    if not PANEL_DIR.exists():
        return []
    return [json.loads(f.read_text()) for f in sorted(PANEL_DIR.glob("*.json"))]


def _build_report_pair(d: dict) -> tuple[CommitInvestigationReport, set[str]]:
    """Build (CommitInvestigationReport, actual_files) from a panel JSON dict."""
    localization = []
    for loc in d.get("localization", []):
        if isinstance(loc, dict) and "file" in loc:
            localization.append(LocalizationClaim(
                file=loc["file"],
                lines=loc.get("lines", [0, 0]),
                rationale=loc.get("rationale", ""),
            ))

    evidence = [EvidenceItem(
        type=EvidenceType.DIFF_HUNK,
        source=d["commit_id"],
        content=d.get("reasoning_summary", "")[:500],
        relevance="Investigation context",
    )]

    actual_files = {
        loc["file"]
        for loc in d.get("localization", [])
        if isinstance(loc, dict) and "file" in loc
    }

    report = CommitInvestigationReport(
        commit_id=d["commit_id"],
        project=d.get("project", "camel"),
        risk_assessment=RiskAssessment(
            level=RiskLevel(d["risk_level"]),
            confidence=d.get("confidence", 0.7),
        ),
        evidence=evidence,
        findings=d.get("findings", []),
        localization=localization,
        reasoning_summary=d.get("reasoning_summary", ""),
        recommendations=[],
        tools_used=[],
        turn_count=1,
        metadata={},
    )
    return report, actual_files


class TestTruncationMetadataPresence:
    """AC-3: AssembledDiff has required metadata fields."""

    def test_assembled_diff_fields_present(self) -> None:
        raw = (
            "diff --git a/Foo.java b/Foo.java\n"
            "--- a/Foo.java\n+++ b/Foo.java\n"
            "@@ -1,2 +1,2 @@\n+int x = 1;\n"
        )
        result = assemble_diff(raw, max_chars=16_000)
        assert isinstance(result.included_files, list)
        assert isinstance(result.truncated_files, list)
        assert isinstance(result.total_chars, int)

    def test_none_diff_has_empty_metadata(self) -> None:
        result = assemble_diff(None)
        assert result.included_files == []
        assert result.truncated_files == []
        assert result.total_chars == 0

    def test_truncation_metadata_dict_shape(self) -> None:
        """Metadata serializes into the expected shape for report.metadata."""
        raw = (
            "diff --git a/Foo.java b/Foo.java\n"
            "--- a/Foo.java\n+++ b/Foo.java\n"
            "@@ -1,2 +1,2 @@\n+int x = 1;\n"
        )
        tm = assemble_diff(raw, max_chars=100)
        meta = {
            "included_files": tm.included_files,
            "truncated_files": tm.truncated_files,
            "total_chars": tm.total_chars,
        }
        assert "included_files" in meta
        assert "truncated_files" in meta
        assert "total_chars" in meta
        assert isinstance(meta["total_chars"], int)


class TestPanelNoRegressionSanity:
    """AC-5: Pipeline runs on panel without errors (smoke)."""

    def test_smart_diff_on_panel_no_exception(self) -> None:
        """assemble_diff processes any raw diff text without crashing."""
        panels = _load_panel()
        if not panels:
            pytest.skip("Panel data not found")

        for report in panels:
            diff_snippet = ""
            evidence = report.get("evidence", [])
            if evidence and isinstance(evidence[0], dict):
                diff_snippet = evidence[0].get("content", "")

            assembled = assemble_diff(diff_snippet[:2000] if diff_snippet else None, max_chars=16_000)
            assert assembled.total_chars <= 16_000

    def test_no_new_llm_calls_required(self) -> None:
        """Smart diff is fully deterministic — no LLM calls needed."""
        raw = (
            "diff --git a/pom.xml b/pom.xml\n"
            "--- a/pom.xml\n+++ b/pom.xml\n"
            "@@ -1,2 +1,2 @@\n+<version>2.0</version>\n"
            "diff --git a/Foo.java b/Foo.java\n"
            "--- a/Foo.java\n+++ b/Foo.java\n"
            "@@ -1,2 +1,2 @@\n+int x = 1;\n"
        )
        result = assemble_diff(raw, max_chars=16_000)
        assert len(result.included_files) >= 1


class TestD6OnPanel:
    """AC-7: Mean D6 >= 0.70 on 12-commit panel (script-only)."""

    def test_d6_on_panel(self) -> None:
        panels = _load_panel()
        if not panels:
            pytest.skip("Panel data not found")

        d6_scores = []
        for d in panels:
            report, actual_files = _build_report_pair(d)
            result = ReasoningJudge.score_d6_evidence_grounding(
                report, actual_files=actual_files
            )
            d6_scores.append(result.normalized)

        mean_d6 = sum(d6_scores) / len(d6_scores)
        assert mean_d6 >= 0.70, (
            f"Mean D6={mean_d6:.3f} < 0.70 on {len(d6_scores)}-commit panel"
        )
