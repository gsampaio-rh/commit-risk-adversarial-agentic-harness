"""Adversarial agreement tests for the evidence tagger spike.

AC-2: tag_hypothesis() achieves ≥80% strict 4-way tier agreement vs iter-2 panel labels.

This module also includes adversarial unit tests for tag_hypothesis() logic,
testing hallucinated-SUPPORTED detection and truncation-edge behavior.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from scripts.spike_evidence_tagger import (  # noqa: E402
    HypothesisFixture,
    decide_outcome,
    evaluate_agreement,
    load_corpus,
    load_diff,
    load_panel_diffs,
    quote_in_diff,
    tag_hypothesis,
)


# ---------------------------------------------------------------------------
# Unit: quote_in_diff matching cascade
# ---------------------------------------------------------------------------

class TestQuoteInDiff:
    def test_exact_match(self) -> None:
        found, method = quote_in_diff("setNoStart(true)", "foo\n-    setNoStart(true);\nbar")
        assert found is True
        assert method == "exact"

    def test_normalized_match(self) -> None:
        found, method = quote_in_diff(
            "exchange.getIn().setBody(body)",
            "-     exchange.getIn().setBody(body);",
        )
        assert found is True
        assert method in ("exact", "normalized")

    def test_no_match_returns_false(self) -> None:
        found, method = quote_in_diff("hallucinated_method()", "no relevant content here")
        assert found is False

    def test_absent_quote_short(self) -> None:
        found, method = quote_in_diff("abc", "abc is here")
        assert found is False
        assert method == "absent"

    def test_empty_quote(self) -> None:
        found, method = quote_in_diff("", "any diff content")
        assert found is False
        assert method == "absent"

    def test_none_quote(self) -> None:
        found, method = quote_in_diff(None, "any diff content")  # type: ignore[arg-type]
        assert found is False


# ---------------------------------------------------------------------------
# Unit: tag_hypothesis logic
# ---------------------------------------------------------------------------

class TestTagHypothesis:
    def test_supported_when_quote_in_diff(self) -> None:
        result = tag_hypothesis(
            "SpringCamelContext.setNoStart(true);",
            "  -        SpringCamelContext.setNoStart(true);",
            llm_tier="SUPPORTED",
        )
        assert result.tier == "SUPPORTED"
        assert result.quote_in_diff is True

    def test_speculative_when_no_quote_and_llm_supported(self) -> None:
        """Hallucinated SUPPORTED: LLM says SUPPORTED but provides no verifiable quote."""
        result = tag_hypothesis(
            "",
            "diff content without the claimed evidence",
            llm_tier="SUPPORTED",
        )
        assert result.tier == "SPECULATIVE"
        assert result.quote_in_diff is False

    def test_speculative_when_quote_not_in_diff_complete(self) -> None:
        """SUPPORTED downgraded when quote not found in complete diff."""
        result = tag_hypothesis(
            "nonexistent_method_call(True)",
            "+-some other diff content here and more",
            diff_was_truncated=False,
            llm_tier="SUPPORTED",
        )
        assert result.tier == "SPECULATIVE"
        assert result.quote_in_diff is False

    def test_speculative_when_hallucinated_supported_and_truncated_diff(self) -> None:
        """SUPPORTED with fake quote on truncated diff → downgrade to SPECULATIVE."""
        result = tag_hypothesis(
            "beyond_truncation_method()",
            "diff content that got cut off at 16K chars",
            diff_was_truncated=True,
            llm_tier="SUPPORTED",
        )
        assert result.tier == "SPECULATIVE"
        assert result.quote_in_diff is False

    def test_defers_to_llm_speculative_when_no_quote(self) -> None:
        """No quote + llm_tier=SPECULATIVE → defer → SPECULATIVE."""
        result = tag_hypothesis(None, "any diff", llm_tier="SPECULATIVE")
        assert result.tier == "SPECULATIVE"
        assert result.match_method == "deferred"

    def test_defers_to_llm_refuted_when_no_quote(self) -> None:
        """No quote + llm_tier=REFUTED → defer → REFUTED."""
        result = tag_hypothesis("", "any diff", llm_tier="REFUTED")
        assert result.tier == "REFUTED"
        assert result.match_method == "deferred"

    def test_defers_to_llm_unverifiable_when_no_quote(self) -> None:
        result = tag_hypothesis(None, "any diff", llm_tier="UNVERIFIABLE")
        assert result.tier == "UNVERIFIABLE"
        assert result.match_method == "deferred"

    def test_hybrid_does_not_upgrade_speculative_when_quote_in_diff(self) -> None:
        """Hybrid model: quote in diff does NOT upgrade LLM SPECULATIVE to SUPPORTED."""
        result = tag_hypothesis(
            "Boolean.class == type",
            "- if (boolean.class == type || Boolean.class == type) {",
            llm_tier="SPECULATIVE",
        )
        assert result.tier == "SPECULATIVE"
        assert result.match_method == "deferred"

    def test_generic_identifier_does_not_upgrade_refuted(self) -> None:
        """Generic identifier match must not upgrade REFUTED to SUPPORTED."""
        result = tag_hypothesis(
            "boolean.class",
            "- if (boolean.class == type || Boolean.class == type) {",
            llm_tier="REFUTED",
        )
        assert result.tier == "REFUTED"
        assert result.match_method == "deferred"

    def test_fbf0ff_ec1_setnostart(self) -> None:
        """EC-1: fbf0ff H1 — setNoStart guard removal is visible in truncated diff."""
        diff = load_diff("fbf0ffad627b", "camel")
        result = tag_hypothesis(
            "SpringCamelContext.setNoStart(true);",
            diff,
            diff_was_truncated=len(load_diff("fbf0ffad627b", "camel", full=True)) > 16000,
            llm_tier="SUPPORTED",
        )
        assert result.tier == "SUPPORTED", (
            f"EC-1 failed: expected SUPPORTED for fbf0ff H1, got {result.tier}"
        )

    def test_fbf0ff_ec1_h2_not_supported(self) -> None:
        """EC-1: fbf0ff H2 — truncated diff, UNVERIFIABLE must not become SUPPORTED."""
        diff = load_diff("fbf0ffad627b", "camel")
        result = tag_hypothesis(
            "",
            diff,
            diff_was_truncated=len(load_diff("fbf0ffad627b", "camel", full=True)) > 16000,
            llm_tier="UNVERIFIABLE",
        )
        assert result.tier == "UNVERIFIABLE"
        assert result.tier != "SUPPORTED"

    def test_7cff_getout_substitution(self) -> None:
        """7cff H1 — getIn→getOut substitution visible in diff."""
        diff = load_diff("7cff0990283b", "camel")
        result = tag_hypothesis(
            "exchange.getIn().setBody(body);",
            diff,
            llm_tier="SUPPORTED",
        )
        assert result.tier == "SUPPORTED"


# ---------------------------------------------------------------------------
# AC-2: Agreement on full corpus (primary gate)
# ---------------------------------------------------------------------------

class TestCorpusAgreement:
    """AC-2: ≥80% strict 4-way tier agreement on full 36-fixture corpus."""

    @pytest.fixture(scope="class")
    def corpus(self) -> list[HypothesisFixture]:
        return load_corpus()

    @pytest.fixture(scope="class")
    def diffs(self, corpus: list[HypothesisFixture]) -> dict[str, str]:
        return load_panel_diffs(corpus)

    @pytest.fixture(scope="class")
    def metrics(
        self, corpus: list[HypothesisFixture], diffs: dict[str, str]
    ) -> dict:
        return evaluate_agreement(corpus, diffs)

    def test_strict_agreement_above_threshold(self, metrics: dict) -> None:
        strict = metrics["strict_agreement"]
        failures = metrics["failures"]
        failure_summary = [
            f"{f['commit_id'][:8]} {f['hypothesis_id']}: expected={f['expected_tier']} got={f['script_tier']} method={f['match_method']}"
            for f in failures
        ]
        assert strict >= 0.80, (
            f"Strict agreement {strict:.1%} < 80%.\n"
            f"Failures ({len(failures)}):\n" + "\n".join(failure_summary)
        )

    def test_fixture_count(self, corpus: list[HypothesisFixture]) -> None:
        assert len(corpus) >= 12, f"Only {len(corpus)} fixtures — need ≥12"

    def test_all_12_commits_represented(self, corpus: list[HypothesisFixture]) -> None:
        commits = {fx.commit_id for fx in corpus}
        assert len(commits) == 12, f"Only {len(commits)} commits covered, need 12"

    def test_supported_fixtures_have_quotes(self, corpus: list[HypothesisFixture]) -> None:
        supported = [fx for fx in corpus if fx.expected_tier == "SUPPORTED"]
        missing = [fx for fx in supported if not fx.evidence_quote or len(fx.evidence_quote) < 8]
        assert not missing, (
            "SUPPORTED fixtures missing canonical quotes: "
            + ", ".join(f"{fx.commit_id[:8]} {fx.hypothesis_id}" for fx in missing)
        )

    def test_binary_supported_agreement(self, metrics: dict) -> None:
        binary = metrics["binary_supported_agreement"]
        assert binary >= 0.80, f"Binary SUPPORTED agreement {binary:.1%} < 80%"

    def test_supported_quote_extraction_rate(self, metrics: dict) -> None:
        rate = metrics["supported_quote_extraction_rate"]
        assert rate >= 0.70, f"Quote extraction rate {rate:.1%} < 70%"

    def test_supported_verification_rate(self, metrics: dict) -> None:
        rate = metrics.get("supported_verification_rate", 0)
        assert rate >= 0.90, f"SUPPORTED verification rate {rate:.1%} < 90%"


# ---------------------------------------------------------------------------
# EC-2: 572f3cee has corpus entry despite no defect hypotheses
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_ec2_572f3cee_in_corpus(self) -> None:
        """EC-2: 572f3cee (style/polish commit) must appear in corpus."""
        fixtures = load_corpus()
        commit_ids = {fx.commit_id for fx in fixtures}
        assert "572f3cee35fe" in commit_ids, (
            "EC-2: 572f3cee not in corpus — style-only commit must still be represented"
        )

    def test_report_exists_and_valid(self) -> None:
        """AC-4: spike report exists and has required schema fields."""
        report_path = PROJECT_ROOT / ".harness/evals/spike-evidence-tagger.json"
        assert report_path.exists(), "Spike report not found at .harness/evals/spike-evidence-tagger.json"

        report = json.loads(report_path.read_text())
        required_keys = {"task_id", "decision", "metrics", "failures", "recommendation_for_iter_3b"}
        missing = required_keys - set(report.keys())
        assert not missing, f"Spike report missing keys: {missing}"

        required_metric_keys = {
            "strict_agreement",
            "binary_supported_agreement",
            "fixture_count",
            "supported_verification_rate",
        }
        missing_metrics = required_metric_keys - set(report["metrics"].keys())
        assert not missing_metrics, f"Spike report metrics missing: {missing_metrics}"

        valid_decisions = {"pure_script", "hybrid", "blocked"}
        assert report["decision"] in valid_decisions, (
            f"Invalid decision: {report['decision']!r} — must be one of {valid_decisions}"
        )
        assert report["decision"] == "hybrid", (
            "Spike must conclude hybrid: script verifies SUPPORTED, LLM provides tier"
        )
        assert "auto_extract_pipeline" in report["metrics"], (
            "Report must document auto-extract pipeline metrics"
        )

    def test_decision_matches_metrics(self) -> None:
        """Decision in report should match what decide_outcome() computes from metrics."""
        report_path = PROJECT_ROOT / ".harness/evals/spike-evidence-tagger.json"
        if not report_path.exists():
            pytest.skip("Spike report not yet generated")

        report = json.loads(report_path.read_text())
        metrics = {
            **report["metrics"],
            "supported_quote_extraction_rate": report["metrics"].get(
                "supported_quote_extraction_rate", 0
            ),
            "auto_extract_pipeline": report["metrics"].get("auto_extract_pipeline"),
        }
        expected_decision = decide_outcome(metrics)
        assert report["decision"] == expected_decision, (
            f"Report decision {report['decision']!r} disagrees with decide_outcome() → {expected_decision!r}"
        )
