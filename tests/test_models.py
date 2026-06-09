"""Tests for Pydantic models — fixture roundtrips, minimal bundles, enum validation."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from cr_analyzer.models import (
    AnalysisCoverage,
    CabReport,
    CabSummary,
    ChangeType,
    CompletenessOutput,
    CRBundle,
    Finding,
    FindingDimension,
    FindingEvidence,
    HistoricalPatternOutput,
    IngestOutput,
    NormalizeOutput,
    Recommendation,
    RiskCategory,
    ScheduleSlaOutput,
    Severity,
    SlaAnalysisMode,
)


# ---------------------------------------------------------------------------
# CRBundle roundtrips
# ---------------------------------------------------------------------------


class TestCRBundleRoundtrip:
    """CRBundle should validate full fixture data and minimal bundles."""

    def test_cr_001_full_roundtrip(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        assert bundle.itsm_record.change_id == "CR-2026-0451"
        assert bundle.itsm_record.type == ChangeType.NORMAL
        assert bundle.itsm_record.risk_category == RiskCategory.LOW
        assert bundle.runbook is not None
        assert bundle.rollback_plan is not None
        assert bundle.cmdb_snapshot is not None
        assert len(bundle.cmdb_snapshot.nodes) == 6
        assert len(bundle.cmdb_snapshot.edges) == 5
        assert bundle.sla_definitions is not None
        assert len(bundle.sla_definitions) == 3
        assert bundle.maintenance_schedule is not None
        assert len(bundle.maintenance_schedule) == 3
        assert bundle.incident_history is not None
        assert bundle.pr_scope_flags is not None
        assert bundle.communication_plan is not None

    def test_cr_002_full_roundtrip(self, cr_002_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_002_data)
        assert bundle.itsm_record.change_id == "CR-2026-0452"
        assert bundle.itsm_record.type == ChangeType.NORMAL
        assert bundle.itsm_record.risk_category == RiskCategory.HIGH
        assert bundle.incident_history is not None
        assert len(bundle.incident_history) == 2

    def test_cr_003_roundtrip(self, cr_003_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_003_data)
        assert bundle.itsm_record.change_id == "CR-2026-0453"
        assert bundle.itsm_record.risk_category == RiskCategory.MEDIUM

    def test_minimal_bundle(self, minimal_bundle_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(minimal_bundle_data)
        assert bundle.itsm_record.change_id == "CR-MINIMAL-001"
        assert bundle.runbook is None
        assert bundle.rollback_plan is None
        assert bundle.cmdb_snapshot is None
        assert bundle.sla_definitions is None
        assert bundle.maintenance_schedule is None
        assert bundle.communication_plan is None
        assert bundle.incident_history is None
        assert bundle.pr_scope_flags is None

    def test_bpi_shaped_bundle(self, bpi_shaped_bundle_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(bpi_shaped_bundle_data)
        assert bundle.itsm_record.change_id == "CHG0012345"
        assert bundle.itsm_record.title == ""
        assert bundle.runbook is None
        assert bundle.rollback_plan is None
        assert bundle.cmdb_snapshot is None
        assert bundle.incident_history is not None
        assert len(bundle.incident_history) == 2

    def test_json_roundtrip(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        json_str = bundle.model_dump_json()
        restored = CRBundle.model_validate_json(json_str)
        assert restored.itsm_record.change_id == bundle.itsm_record.change_id
        assert restored.cmdb_snapshot is not None
        assert len(restored.cmdb_snapshot.nodes) == len(bundle.cmdb_snapshot.nodes)


# ---------------------------------------------------------------------------
# Enum validation
# ---------------------------------------------------------------------------


class TestEnumValidation:
    """Invalid enum values must raise ValidationError."""

    def test_invalid_change_type(self, minimal_bundle_data: dict[str, Any]) -> None:
        minimal_bundle_data["itsm_record"]["type"] = "urgent"
        with pytest.raises(ValidationError, match="type"):
            CRBundle.model_validate(minimal_bundle_data)

    def test_invalid_risk_category(self, minimal_bundle_data: dict[str, Any]) -> None:
        minimal_bundle_data["itsm_record"]["risk_category"] = "extreme"
        with pytest.raises(ValidationError, match="risk_category"):
            CRBundle.model_validate(minimal_bundle_data)

    def test_invalid_severity_in_finding(self) -> None:
        with pytest.raises(ValidationError):
            Finding(
                dimension=FindingDimension.COMPLETENESS,
                severity="critical",  # type: ignore[arg-type]
                finding="test",
                evidence=FindingEvidence(artifact="test"),
            )

    def test_invalid_dimension_in_finding(self) -> None:
        with pytest.raises(ValidationError):
            Finding(
                dimension="nonexistent",  # type: ignore[arg-type]
                severity=Severity.WARNING,
                finding="test",
                evidence=FindingEvidence(artifact="test"),
            )

    def test_all_change_types(self) -> None:
        assert set(ChangeType) == {ChangeType.STANDARD, ChangeType.NORMAL, ChangeType.EMERGENCY}

    def test_all_finding_dimensions(self) -> None:
        assert len(FindingDimension) == 8

    def test_all_severities(self) -> None:
        assert set(Severity) == {Severity.BLOCKER, Severity.WARNING, Severity.INFO}

    def test_all_recommendations(self) -> None:
        assert set(Recommendation) == {
            Recommendation.APPROVE,
            Recommendation.CONDITIONAL,
            Recommendation.REJECT,
        }

    def test_all_sla_modes(self) -> None:
        assert set(SlaAnalysisMode) == {
            SlaAnalysisMode.FULL,
            SlaAnalysisMode.OVERLAP_ONLY,
            SlaAnalysisMode.NOT_APPLICABLE,
        }


# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------


class TestFinding:
    """Finding model with flexible evidence."""

    def test_finding_with_minimal_evidence(self) -> None:
        f = Finding(
            dimension=FindingDimension.COMPLETENESS,
            severity=Severity.WARNING,
            finding="Missing rollback plan",
            evidence=FindingEvidence(artifact="rollback_plan"),
        )
        assert f.evidence.artifact == "rollback_plan"
        assert f.remediation is None

    def test_finding_with_remediation(self) -> None:
        f = Finding(
            dimension=FindingDimension.RUNBOOK_VALIDITY,
            severity=Severity.BLOCKER,
            finding="Stale reference",
            evidence=FindingEvidence(artifact="runbook", step_number=3, service_ref="payment-db-v3"),
            remediation="Update runbook",
        )
        assert f.remediation == "Update runbook"
        extras = f.evidence.model_extra or {}
        assert extras.get("step_number") == 3
        assert extras.get("service_ref") == "payment-db-v3"

    def test_historical_pattern_evidence(self) -> None:
        evidence = FindingEvidence(
            artifact="incident_history",
            service="payment-api",
            change_category="schema_migration",
            matching_incidents=[
                {"incident_id": "INC-001", "severity": "P2", "date": "2026-02-18",
                 "root_cause_summary": "Column mismatch"},
            ],
        )
        extras = evidence.model_extra or {}
        assert extras["service"] == "payment-api"
        assert extras["change_category"] == "schema_migration"
        assert len(extras["matching_incidents"]) == 1

    def test_finding_json_roundtrip(self) -> None:
        f = Finding(
            dimension=FindingDimension.HISTORICAL_PATTERN,
            severity=Severity.WARNING,
            finding="Pattern match",
            evidence=FindingEvidence(
                artifact="incident_history",
                service="svc-1",
                change_category="config_update",
            ),
        )
        json_str = f.model_dump_json()
        restored = Finding.model_validate_json(json_str)
        assert restored.dimension == FindingDimension.HISTORICAL_PATTERN
        extras = restored.evidence.model_extra or {}
        assert extras["service"] == "svc-1"


# ---------------------------------------------------------------------------
# Stage output models existence and basic validation
# ---------------------------------------------------------------------------


class TestStageOutputModels:
    """Verify all stage output models can be instantiated."""

    def test_ingest_output(self) -> None:
        out = IngestOutput(change_id="CR-001", itsm_record={"change_id": "CR-001"})
        assert out.runbook is None
        assert out.rollback is None

    def test_normalize_output(self) -> None:
        out = NormalizeOutput(
            change_id="CR-001",
            change_type=ChangeType.NORMAL,
            risk_category=RiskCategory.LOW,
            title="Test",
            description="Test change",
            affected_services=["svc-1"],
            requestor="user",
            approvers=["approver"],
            scheduled_window={"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T02:00:00Z"},
            is_customer_facing=False,
            affected_tier=4,
        )
        assert out.is_customer_facing is False
        assert out.affected_tier == 4

    def test_completeness_output(self) -> None:
        out = CompletenessOutput(change_id="CR-001", findings=[], complete=True)
        assert out.complete is True

    def test_schedule_sla_output(self) -> None:
        out = ScheduleSlaOutput()
        assert out.scheduling_conflicts == []
        assert out.sla_analysis_mode == SlaAnalysisMode.FULL

    def test_historical_pattern_output(self) -> None:
        out = HistoricalPatternOutput(change_id="CR-001")
        assert out.findings == []
        assert out.method_used == "exact_match"

    def test_cab_report(self) -> None:
        report = CabReport(
            change_id="CR-001",
            risk_level="low",
            recommendation=Recommendation.APPROVE,
        )
        assert report.stages_skipped == []
        assert report.analysis_coverage.executed == 0
        assert report.analysis_coverage.skipped == 0
        assert report.analysis_coverage.degraded == 0
        assert report.method_used == "template"

    def test_cab_report_with_coverage(self) -> None:
        report = CabReport(
            change_id="CR-001",
            risk_level="high",
            recommendation=Recommendation.REJECT,
            stages_skipped=["runbook_validation", "rollback_feasibility"],
            analysis_coverage=AnalysisCoverage(executed=5, skipped=2, degraded=1),
        )
        assert len(report.stages_skipped) == 2
        assert report.analysis_coverage.executed == 5

    def test_cab_summary(self) -> None:
        summary = CabSummary(window_id="CAB-2026-W23", total_crs=3)
        assert summary.disposition_breakdown.approve == 0
        assert summary.cross_cr_conflicts == []
