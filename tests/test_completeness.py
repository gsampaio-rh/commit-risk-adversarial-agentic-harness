"""Tests for Stage 3: Completeness Check."""

from __future__ import annotations

from typing import Any

from cr_analyzer.models.bundle import CRBundle
from cr_analyzer.models.enums import FindingDimension, Severity
from cr_analyzer.stages.completeness import run_completeness
from cr_analyzer.stages.ingest import run_ingest
from cr_analyzer.stages.normalize import run_normalize


def _pipeline(data: dict[str, Any]):
    """Shortcut: CRBundle → Ingest → Normalize → Completeness."""
    bundle = CRBundle.model_validate(data)
    return run_completeness(run_normalize(run_ingest(bundle)))


class TestCompleteBundle:
    """cr-001 is a complete normal change — should pass cleanly."""

    def test_complete_cr_001(self, cr_001_data: dict[str, Any]) -> None:
        result = _pipeline(cr_001_data)
        assert result.complete is True
        blockers = [f for f in result.findings if f.severity == Severity.BLOCKER]
        warnings = [f for f in result.findings if f.severity == Severity.WARNING]
        assert len(blockers) == 0
        assert len(warnings) == 0

    def test_change_id_preserved(self, cr_001_data: dict[str, Any]) -> None:
        result = _pipeline(cr_001_data)
        assert result.change_id == "CR-2026-0451"


class TestIncompleteBundle:
    """BPI-shaped bundle has no runbook/rollback/comms — should flag."""

    def test_bpi_bundle_not_complete(self, bpi_shaped_bundle_data: dict[str, Any]) -> None:
        result = _pipeline(bpi_shaped_bundle_data)
        assert result.complete is False

    def test_bpi_bundle_flags_missing_title(self, bpi_shaped_bundle_data: dict[str, Any]) -> None:
        result = _pipeline(bpi_shaped_bundle_data)
        title_findings = [f for f in result.findings if "title" in f.finding.lower()]
        assert len(title_findings) >= 1

    def test_bpi_bundle_flags_missing_description(self, bpi_shaped_bundle_data: dict[str, Any]) -> None:
        result = _pipeline(bpi_shaped_bundle_data)
        desc_findings = [f for f in result.findings if "description" in f.finding.lower()]
        assert len(desc_findings) >= 1


class TestNormalTypeRules:
    """Normal type requires runbook + rollback."""

    def test_normal_without_runbook_is_blocker(self, minimal_bundle_data: dict[str, Any]) -> None:
        minimal_bundle_data["itsm_record"]["type"] = "normal"
        result = _pipeline(minimal_bundle_data)
        blockers = [
            f for f in result.findings
            if f.severity == Severity.BLOCKER and "runbook" in f.finding.lower()
        ]
        assert len(blockers) >= 1

    def test_normal_without_rollback_is_blocker(self, minimal_bundle_data: dict[str, Any]) -> None:
        minimal_bundle_data["itsm_record"]["type"] = "normal"
        result = _pipeline(minimal_bundle_data)
        blockers = [
            f for f in result.findings
            if f.severity == Severity.BLOCKER and "rollback" in f.finding.lower()
        ]
        assert len(blockers) >= 1


class TestEmergencyTypeRules:
    """Emergency type requires rollback."""

    def test_emergency_without_rollback_is_blocker(self, minimal_bundle_data: dict[str, Any]) -> None:
        minimal_bundle_data["itsm_record"]["type"] = "emergency"
        result = _pipeline(minimal_bundle_data)
        blockers = [
            f for f in result.findings
            if f.severity == Severity.BLOCKER and "rollback" in f.finding.lower()
        ]
        assert len(blockers) >= 1


class TestStandardTypeRules:
    """Standard type: missing runbook is info, not blocker."""

    def test_standard_without_runbook_is_info(self, minimal_bundle_data: dict[str, Any]) -> None:
        result = _pipeline(minimal_bundle_data)
        info_findings = [
            f for f in result.findings
            if f.severity == Severity.INFO and "runbook" in f.finding.lower()
        ]
        assert len(info_findings) >= 1


class TestCustomerFacingRules:
    """Customer-facing scope without comms plan gets warning."""

    def test_customer_facing_no_comms(self, cr_003_data: dict[str, Any]) -> None:
        result = _pipeline(cr_003_data)
        comms_findings = [
            f for f in result.findings
            if f.dimension == FindingDimension.COMMUNICATION_GAPS
        ]
        # cr-003 affects auth-gateway (tier 2) → customer-facing, has no comms
        assert len(comms_findings) >= 1
        assert comms_findings[0].severity == Severity.WARNING


class TestFindingStructure:
    """Findings have correct dimension, evidence, remediation."""

    def test_finding_has_evidence_artifact(self, bpi_shaped_bundle_data: dict[str, Any]) -> None:
        result = _pipeline(bpi_shaped_bundle_data)
        for f in result.findings:
            assert f.evidence.artifact != ""
            assert f.remediation is not None
            assert f.dimension in (
                FindingDimension.COMPLETENESS,
                FindingDimension.COMMUNICATION_GAPS,
            )
