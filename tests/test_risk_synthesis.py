"""Tests for Stage 9: Risk Synthesis L1."""

from __future__ import annotations

from cr_analyzer.models.enums import FindingDimension, Recommendation, Severity
from cr_analyzer.models.findings import Finding, FindingEvidence
from cr_analyzer.models.outputs import ScheduleSlaOutput
from cr_analyzer.stages.risk_synthesis import (
    render_cr_report_md,
    render_summary_md,
    synthesize_report,
    synthesize_summary,
)


def _finding(dim: FindingDimension, sev: Severity) -> Finding:
    return Finding(
        dimension=dim,
        severity=sev,
        finding=f"Test {dim.value} {sev.value}",
        evidence=FindingEvidence(artifact="test"),
        remediation=f"Fix {dim.value}",
    )


class TestR1Blocker:
    """R1: any blocker → reject."""

    def test_single_blocker_rejects(self) -> None:
        findings = [_finding(FindingDimension.RUNBOOK_VALIDITY, Severity.BLOCKER)]
        report = synthesize_report("CR-1", findings, [], 6)
        assert report.recommendation == Recommendation.REJECT
        assert report.risk_level == "critical"


class TestR2MultiWarning:
    """R2: >=2 warnings across >=2 dimensions → conditional."""

    def test_two_warnings_different_dims_conditional(self) -> None:
        findings = [
            _finding(FindingDimension.SCHEDULING_CONFLICTS, Severity.WARNING),
            _finding(FindingDimension.COMMUNICATION_GAPS, Severity.WARNING),
        ]
        report = synthesize_report("CR-2", findings, [], 6)
        assert report.recommendation == Recommendation.CONDITIONAL
        assert report.risk_level == "high"

    def test_two_warnings_same_dim_approve(self) -> None:
        findings = [
            _finding(FindingDimension.COMPLETENESS, Severity.WARNING),
            _finding(FindingDimension.COMPLETENESS, Severity.WARNING),
        ]
        report = synthesize_report("CR-3", findings, [], 6)
        assert report.recommendation == Recommendation.APPROVE


class TestR3SingleWarning:
    """R3: 1 warning or only info → approve."""

    def test_single_warning_approves(self) -> None:
        findings = [_finding(FindingDimension.HISTORICAL_PATTERN, Severity.WARNING)]
        report = synthesize_report("CR-4", findings, [], 6)
        assert report.recommendation == Recommendation.APPROVE
        assert report.risk_level == "medium"


class TestR4NoFindings:
    """R4: no findings → approve."""

    def test_clean_cr_approves(self) -> None:
        report = synthesize_report("CR-5", [], [], 6)
        assert report.recommendation == Recommendation.APPROVE
        assert report.risk_level == "low"


class TestReportStructure:
    """Report has correct structure."""

    def test_stages_skipped(self) -> None:
        report = synthesize_report("CR-6", [], ["runbook_validation", "rollback_feasibility"], 4)
        assert report.stages_skipped == ["runbook_validation", "rollback_feasibility"]
        assert report.analysis_coverage.executed == 4
        assert report.analysis_coverage.skipped == 2

    def test_conditional_actions_populated(self) -> None:
        findings = [
            _finding(FindingDimension.SCHEDULING_CONFLICTS, Severity.WARNING),
            _finding(FindingDimension.COMMUNICATION_GAPS, Severity.WARNING),
        ]
        report = synthesize_report("CR-7", findings, [], 6)
        assert len(report.conditional_actions) == 2

    def test_dimension_summary(self) -> None:
        findings = [
            _finding(FindingDimension.COMPLETENESS, Severity.BLOCKER),
            _finding(FindingDimension.COMPLETENESS, Severity.WARNING),
            _finding(FindingDimension.HISTORICAL_PATTERN, Severity.INFO),
        ]
        report = synthesize_report("CR-8", findings, [], 6)
        assert report.dimension_summary["completeness"].blocker == 1
        assert report.dimension_summary["completeness"].warning == 1
        assert report.dimension_summary["historical_pattern"].info == 1


class TestCabSummary:
    """CAB window summary."""

    def test_disposition_breakdown(self) -> None:
        reports = [
            synthesize_report("CR-A", [], [], 6),
            synthesize_report("CR-B", [
                _finding(FindingDimension.SCHEDULING_CONFLICTS, Severity.WARNING),
                _finding(FindingDimension.COMMUNICATION_GAPS, Severity.WARNING),
            ], [], 6),
            synthesize_report("CR-C", [
                _finding(FindingDimension.RUNBOOK_VALIDITY, Severity.BLOCKER),
            ], [], 6),
        ]
        summary = synthesize_summary("CAB-W23", reports)
        assert summary.total_crs == 3
        assert summary.disposition_breakdown.approve == 1
        assert summary.disposition_breakdown.conditional == 1
        assert summary.disposition_breakdown.reject == 1

    def test_cross_cr_conflicts(self) -> None:
        schedule = ScheduleSlaOutput(
            scheduling_conflicts=[{
                "cr_pair": ["CR-A", "CR-B"],
                "shared_services": ["svc-1"],
                "overlap_window": {
                    "start": "2026-06-07T02:00:00Z",
                    "end": "2026-06-07T03:00:00Z",
                },
                "severity": "warning",
            }],
        )
        summary = synthesize_summary("CAB-W23", [], schedule)
        assert len(summary.cross_cr_conflicts) == 1
        assert summary.cross_cr_conflicts[0].type == "scheduling_overlap"


class TestMarkdownRendering:
    """Template Markdown output."""

    def test_cr_report_md_has_header(self) -> None:
        report = synthesize_report("CR-MD", [], [], 6)
        md = render_cr_report_md(report)
        assert "# Change Risk Assessment: CR-MD" in md
        assert "APPROVE" in md

    def test_cr_report_md_has_findings(self) -> None:
        findings = [_finding(FindingDimension.COMPLETENESS, Severity.BLOCKER)]
        report = synthesize_report("CR-MD2", findings, [], 6)
        md = render_cr_report_md(report)
        assert "[BLOCKER]" in md
        assert "completeness" in md

    def test_summary_md_has_breakdown(self) -> None:
        reports = [synthesize_report("CR-A", [], [], 6)]
        summary = synthesize_summary("CAB-W23", reports)
        md = render_summary_md(summary)
        assert "# CAB Summary: CAB-W23" in md
        assert "Approve:** 1" in md
