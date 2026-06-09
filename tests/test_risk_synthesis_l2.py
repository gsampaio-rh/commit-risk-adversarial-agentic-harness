"""Tests for Risk Synthesis L2 — LLM narrative with selective routing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cr_analyzer.models.enums import FindingDimension, Recommendation, Severity
from cr_analyzer.models.findings import Finding, FindingEvidence
from cr_analyzer.stages.risk_synthesis import (
    CostTracker,
    RiskSynthesisConfig,
    render_cr_report_l2_md,
    synthesize_report,
    synthesize_report_l2,
)


@pytest.fixture
def blocker_findings() -> list[Finding]:
    return [
        Finding(
            dimension=FindingDimension.HISTORICAL_PATTERN,
            severity=Severity.BLOCKER,
            finding="5 P1/P2 incidents on payment-api for schema_migration",
            evidence=FindingEvidence(artifact="incident_history"),
            remediation="Review past incidents and add mitigations",
        ),
    ]


@pytest.fixture
def warning_findings() -> list[Finding]:
    return [
        Finding(
            dimension=FindingDimension.COMPLETENESS,
            severity=Severity.WARNING,
            finding="Missing rollback plan",
            evidence=FindingEvidence(artifact="rollback_plan"),
            remediation="Add a rollback plan before proceeding",
        ),
        Finding(
            dimension=FindingDimension.SCHEDULING_CONFLICTS,
            severity=Severity.WARNING,
            finding="Scheduling conflict with CR-002",
            evidence=FindingEvidence(artifact="schedule"),
            remediation="Reschedule to avoid overlap",
        ),
    ]


@pytest.fixture
def clean_findings() -> list[Finding]:
    return [
        Finding(
            dimension=FindingDimension.COMPLETENESS,
            severity=Severity.INFO,
            finding="All artifacts present",
            evidence=FindingEvidence(artifact="completeness"),
        ),
    ]


def _mock_llm_response() -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": (
                        "This change carries elevated risk due to the combination of "
                        "incomplete documentation and scheduling conflicts. The missing "
                        "rollback plan must be addressed before approval."
                    ),
                }
            }
        ],
        "usage": {
            "prompt_tokens": 150,
            "completion_tokens": 45,
        },
    }


class TestSelectiveRouting:
    """LLM is only called for conditional/reject CRs."""

    @patch("cr_analyzer.stages.risk_synthesis._call_llm")
    def test_approve_cr_uses_template(self, mock_llm, clean_findings) -> None:
        config = RiskSynthesisConfig(method="llm_narrative", api_key="test-key")
        report = synthesize_report_l2(
            "CR-APPROVE", clean_findings, [], 5, config=config
        )
        mock_llm.assert_not_called()
        assert report.method_used == "template"
        assert report.recommendation == Recommendation.APPROVE

    @patch("cr_analyzer.stages.risk_synthesis._call_llm")
    def test_conditional_cr_calls_llm(self, mock_llm, warning_findings) -> None:
        mock_llm.return_value = ("Risk narrative here.", 100, 30)
        config = RiskSynthesisConfig(method="llm_narrative", api_key="test-key")
        report = synthesize_report_l2(
            "CR-COND", warning_findings, [], 5, config=config
        )
        mock_llm.assert_called_once()
        assert report.method_used == "llm_narrative"
        assert report.narrative == "Risk narrative here."
        assert report.recommendation == Recommendation.CONDITIONAL

    @patch("cr_analyzer.stages.risk_synthesis._call_llm")
    def test_reject_cr_calls_llm(self, mock_llm, blocker_findings) -> None:
        mock_llm.return_value = ("Critical risk narrative.", 120, 35)
        config = RiskSynthesisConfig(method="llm_narrative", api_key="test-key")
        report = synthesize_report_l2(
            "CR-REJECT", blocker_findings, [], 5, config=config
        )
        mock_llm.assert_called_once()
        assert report.method_used == "llm_narrative"
        assert report.recommendation == Recommendation.REJECT


class TestCostTracking:
    """Cost tracker records tokens and enforces ceiling."""

    @patch("cr_analyzer.stages.risk_synthesis._call_llm")
    def test_cost_tracking(self, mock_llm, warning_findings) -> None:
        mock_llm.return_value = ("Narrative.", 200, 50)
        config = RiskSynthesisConfig(method="llm_narrative", api_key="test-key")
        tracker = CostTracker()

        synthesize_report_l2(
            "CR-001", warning_findings, [], 5, config=config, cost_tracker=tracker
        )
        assert tracker.total_input_tokens == 200
        assert tracker.total_output_tokens == 50
        assert tracker.total_cost_usd > 0
        assert "CR-001" in tracker.cr_costs

    @patch("cr_analyzer.stages.risk_synthesis._call_llm")
    def test_cost_ceiling_fallback(self, mock_llm, warning_findings) -> None:
        config = RiskSynthesisConfig(
            method="llm_narrative", api_key="test-key", cost_ceiling_usd=0.001
        )
        tracker = CostTracker()
        tracker.total_cost_usd = 0.002  # Already over ceiling

        report = synthesize_report_l2(
            "CR-OVER-BUDGET", warning_findings, [], 5,
            config=config, cost_tracker=tracker,
        )
        mock_llm.assert_not_called()
        assert report.method_used == "template"


class TestGracefulFallback:
    """LLM failures fall back to template."""

    @patch("cr_analyzer.stages.risk_synthesis._call_llm")
    def test_llm_error_falls_back(self, mock_llm, warning_findings) -> None:
        mock_llm.side_effect = RuntimeError("API unavailable")
        config = RiskSynthesisConfig(method="llm_narrative", api_key="test-key")

        report = synthesize_report_l2(
            "CR-FAIL", warning_findings, [], 5, config=config
        )
        assert report.method_used == "template"
        assert report.narrative is None
        assert report.recommendation == Recommendation.CONDITIONAL

    @patch("cr_analyzer.stages.risk_synthesis._call_llm")
    def test_timeout_falls_back(self, mock_llm, blocker_findings) -> None:
        mock_llm.side_effect = TimeoutError("LLM timeout")
        config = RiskSynthesisConfig(method="llm_narrative", api_key="test-key")

        report = synthesize_report_l2(
            "CR-TIMEOUT", blocker_findings, [], 5, config=config
        )
        assert report.method_used == "template"
        assert report.recommendation == Recommendation.REJECT


class TestMarkdownRendering:
    """L2 markdown includes narrative section."""

    @patch("cr_analyzer.stages.risk_synthesis._call_llm")
    def test_l2_md_has_narrative(self, mock_llm, warning_findings) -> None:
        mock_llm.return_value = ("AI risk narrative content.", 100, 30)
        config = RiskSynthesisConfig(method="llm_narrative", api_key="test-key")

        report = synthesize_report_l2(
            "CR-NAR", warning_findings, [], 5, config=config
        )
        md = render_cr_report_l2_md(report)
        assert "Risk Narrative (AI-generated)" in md
        assert "AI risk narrative content." in md

    def test_l1_md_no_narrative(self, clean_findings) -> None:
        report = synthesize_report(
            "CR-L1", clean_findings, [], 5
        )
        md = render_cr_report_l2_md(report)
        assert "Risk Narrative" not in md


class TestMethodUsedField:
    """method_used correctly reflects synthesis method."""

    def test_l1_is_template(self, clean_findings) -> None:
        report = synthesize_report("CR-L1", clean_findings, [], 5)
        assert report.method_used == "template"

    @patch("cr_analyzer.stages.risk_synthesis._call_llm")
    def test_l2_approve_is_template(self, mock_llm, clean_findings) -> None:
        config = RiskSynthesisConfig(method="llm_narrative", api_key="test-key")
        report = synthesize_report_l2("CR-L2", clean_findings, [], 5, config=config)
        assert report.method_used == "template"
        mock_llm.assert_not_called()

    @patch("cr_analyzer.stages.risk_synthesis._call_llm")
    def test_l2_conditional_is_llm(self, mock_llm, warning_findings) -> None:
        mock_llm.return_value = ("Narrative.", 100, 30)
        config = RiskSynthesisConfig(method="llm_narrative", api_key="test-key")
        report = synthesize_report_l2("CR-L2", warning_findings, [], 5, config=config)
        assert report.method_used == "llm_narrative"


class TestConfigDefaults:
    """Config handles env vars and defaults."""

    def test_default_method_is_template(self) -> None:
        config = RiskSynthesisConfig()
        assert config.method == "template"

    def test_default_ceiling(self) -> None:
        config = RiskSynthesisConfig()
        assert config.cost_ceiling_usd == 2.0

    def test_cost_tracker_budget(self) -> None:
        tracker = CostTracker()
        assert tracker.budget_remaining == 2.0
        tracker.record("CR-001", 1000, 100)
        assert tracker.budget_remaining < 2.0
