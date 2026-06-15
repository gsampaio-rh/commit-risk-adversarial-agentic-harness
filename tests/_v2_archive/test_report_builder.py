"""Tests for SUPPORTED-only localization with defect-signal file ranking."""

from __future__ import annotations

from unittest.mock import MagicMock

from commit_investigator.analysis.evidence_tagger import TagResult
from commit_investigator.hypothesis.hypothesis_engine import HypothesisSpec
from commit_investigator.pipeline.report_builder import _build_localization


def _tag(tier: str) -> TagResult:
    return TagResult(tier=tier, quote_in_diff=False, match_method="none")


def _hyp(file: str, mechanism: str = "bug", evidence_quote: str = "q") -> HypothesisSpec:
    return HypothesisSpec(
        mechanism=mechanism,
        file=file,
        lines=[10, 20],
        evidence_quote=evidence_quote,
        suggested_action="fix it",
    )


# Minimal diff that makes FooManager.java a defect-signal file (rank 0)
_DEFECT_SIGNAL_DIFF = """\
diff --git a/src/main/java/FooManager.java b/src/main/java/FooManager.java
index 0000000..1111111 100644
--- a/src/main/java/FooManager.java
+++ b/src/main/java/FooManager.java
@@ -10,6 +10,7 @@ public class FooManager {
+    synchronized void processItem() { throw new RuntimeException(); }
"""


class TestLocalizationSupportedOnly:
    def test_all_speculative_produces_empty_localization(self) -> None:
        """AC-1: SPECULATIVE-only → localization == []"""
        hyps = [_hyp("Foo.java"), _hyp("Bar.java")]
        tags = [_tag("SPECULATIVE"), _tag("SPECULATIVE")]
        result = _build_localization(hyps, tags, None)
        assert result == []

    def test_all_refuted_produces_empty_localization(self) -> None:
        """AC-1 variant: REFUTED-only → localization == []"""
        hyps = [_hyp("Foo.java")]
        tags = [_tag("REFUTED")]
        result = _build_localization(hyps, tags, None)
        assert result == []

    def test_mixed_tiers_only_supported_included(self) -> None:
        """AC-2: 2 SUPPORTED + 2 SPECULATIVE → exactly 2 claims from SUPPORTED."""
        hyps = [
            _hyp("Supported1.java"),
            _hyp("Speculative1.java"),
            _hyp("Supported2.java"),
            _hyp("Speculative2.java"),
        ]
        tags = [_tag("SUPPORTED"), _tag("SPECULATIVE"), _tag("SUPPORTED"), _tag("SPECULATIVE")]
        result = _build_localization(hyps, tags, None)
        assert len(result) == 2
        files = {c.file for c in result}
        assert files == {"Supported1.java", "Supported2.java"}

    def test_empty_file_excluded_even_if_supported(self) -> None:
        """EC-2: SUPPORTED with empty file → excluded."""
        hyps = [_hyp(""), _hyp("Real.java")]
        tags = [_tag("SUPPORTED"), _tag("SUPPORTED")]
        result = _build_localization(hyps, tags, None)
        assert len(result) == 1
        assert result[0].file == "Real.java"

    def test_zero_supported_hypotheses(self) -> None:
        """EC-1: zero SUPPORTED → empty list, no exception."""
        result = _build_localization([], [], None)
        assert result == []


class TestLocalizationDefectSignalRanking:
    def test_defect_signal_file_ranked_first(self) -> None:
        """AC-3: SUPPORTED hyp on defect-signal file outranks SUPPORTED hyp on non-signal file."""
        hyps = [
            _hyp("src/main/java/Helper.java", evidence_quote="short"),
            _hyp("src/main/java/FooManager.java", evidence_quote="x"),
        ]
        tags = [_tag("SUPPORTED"), _tag("SUPPORTED")]
        result = _build_localization(hyps, tags, _DEFECT_SIGNAL_DIFF)
        assert result[0].file == "src/main/java/FooManager.java"

    def test_raw_diff_none_preserves_supported_order(self) -> None:
        """EC-3: raw_diff None → no signal ranking; SUPPORTED in original order."""
        hyps = [_hyp("First.java"), _hyp("Second.java")]
        tags = [_tag("SUPPORTED"), _tag("SUPPORTED")]
        result = _build_localization(hyps, tags, None)
        assert [c.file for c in result] == ["First.java", "Second.java"]

    def test_longer_evidence_quote_wins_tiebreak(self) -> None:
        """Longer evidence quote ranks higher when signal tier is equal."""
        hyps = [
            _hyp("A.java", evidence_quote="short"),
            _hyp("B.java", evidence_quote="a much longer evidence quote here"),
        ]
        tags = [_tag("SUPPORTED"), _tag("SUPPORTED")]
        result = _build_localization(hyps, tags, None)
        assert result[0].file == "B.java"

    def test_deduplication_by_file_and_lines(self) -> None:
        """EC-4: two SUPPORTED claims on same (file, lines) → deduplicated, keep first ranked."""
        hyp1 = HypothesisSpec(
            mechanism="bug A", file="Dup.java", lines=[10, 20],
            evidence_quote="longer quote wins", suggested_action=""
        )
        hyp2 = HypothesisSpec(
            mechanism="bug B", file="Dup.java", lines=[10, 20],
            evidence_quote="short", suggested_action=""
        )
        tags = [_tag("SUPPORTED"), _tag("SUPPORTED")]
        result = _build_localization([hyp1, hyp2], tags, None)
        assert len(result) == 1
        assert result[0].rationale == "bug A"


class TestBuildReportLocalizationIntegration:
    """AC-4: integration check — build_report localization never contains non-SUPPORTED claims."""

    def test_build_report_localization_only_supported(self) -> None:
        from unittest.mock import patch, MagicMock
        from commit_investigator.pipeline.report_builder import build_report
        from commit_investigator.hypothesis.hypothesis_engine import HypothesisResponse
        from commit_investigator.analysis.report import RiskLevel

        hyp_supported = _hyp("Production.java", "null pointer", "evidence quote")
        hyp_speculative = _hyp("Speculative.java", "wrong mechanism", "")

        hyp_response = MagicMock(spec=HypothesisResponse)
        hyp_response.hypotheses = [hyp_supported, hyp_speculative]
        hyp_response.summary = "test summary"

        tags = [_tag("SUPPORTED"), _tag("SPECULATIVE")]

        verdict = MagicMock()
        verdict.risk_level = RiskLevel.HIGH
        verdict.cap_applied = False

        context = MagicMock()
        context.commit_id = "abc123"
        context.project = "camel"
        context.diff = "diff text"
        context.raw_diff = None
        context.missing_reasons = []
        context.truncation_metadata = None

        last_response = MagicMock()
        last_response.model = "test-model"

        budget = MagicMock()
        budget.total_tokens = 100
        budget.total_cost = 0.001
        budget.budget_exceeded = False

        report = build_report(
            hyp_response=hyp_response,
            tagged=tags,
            verdict=verdict,
            context=context,
            last_response=last_response,
            checkpoints=[],
            budget=budget,
            tools_used=[],
            turns=1,
        )

        localization_files = {claim.file for claim in report.localization}
        assert "Production.java" in localization_files
        assert "Speculative.java" not in localization_files
