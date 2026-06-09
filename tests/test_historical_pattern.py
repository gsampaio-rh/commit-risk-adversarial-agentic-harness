"""Tests for Stage 8: Historical Pattern (L1 exact match)."""

from __future__ import annotations

from typing import Any

from cr_analyzer.models.bundle import CRBundle
from cr_analyzer.models.enums import FindingDimension, Severity
from cr_analyzer.stages.historical_pattern import run_historical_pattern
from cr_analyzer.stages.ingest import run_ingest
from cr_analyzer.stages.normalize import run_normalize


def _pipeline(data: dict[str, Any]):
    return run_historical_pattern(run_normalize(run_ingest(CRBundle.model_validate(data))))


class TestHistoricalPatternCr002:
    """cr-002 has 2 P2 incidents for payment-api/schema_migration → warning."""

    def test_detects_pattern(self, cr_002_data: dict[str, Any]) -> None:
        result = _pipeline(cr_002_data)
        assert len(result.findings) >= 1

    def test_severity_is_warning(self, cr_002_data: dict[str, Any]) -> None:
        result = _pipeline(cr_002_data)
        pattern_finding = result.findings[0]
        assert pattern_finding.severity == Severity.WARNING

    def test_evidence_has_required_fields(self, cr_002_data: dict[str, Any]) -> None:
        result = _pipeline(cr_002_data)
        evidence = result.findings[0].evidence
        extras = evidence.model_extra or {}
        assert extras.get("service") == "payment-api"
        assert extras.get("change_category") == "schema_migration"
        assert len(extras.get("matching_incidents", [])) == 2


class TestHistoricalPatternNoIncidents:
    """cr-001 has 1 P4 incident — below threshold, no finding."""

    def test_no_findings_for_low_severity(self, cr_001_data: dict[str, Any]) -> None:
        result = _pipeline(cr_001_data)
        assert len(result.findings) == 0

    def test_method_is_exact_match(self, cr_001_data: dict[str, Any]) -> None:
        result = _pipeline(cr_001_data)
        assert result.method_used == "exact_match"


class TestHistoricalPatternEmpty:
    """No incident history → no findings."""

    def test_no_incidents(self, minimal_bundle_data: dict[str, Any]) -> None:
        result = _pipeline(minimal_bundle_data)
        assert len(result.findings) == 0
        assert result.method_used == "exact_match"


class TestHistoricalPatternBpiShaped:
    """BPI-shaped bundle with 2 P2 incidents → warning."""

    def test_bpi_pattern_detected(self, bpi_shaped_bundle_data: dict[str, Any]) -> None:
        result = _pipeline(bpi_shaped_bundle_data)
        assert len(result.findings) == 1
        assert result.findings[0].severity == Severity.WARNING
        assert result.findings[0].dimension == FindingDimension.HISTORICAL_PATTERN


class TestBlockerThreshold:
    """>=5 incidents OR P1 severity → blocker."""

    def test_p1_incident_is_blocker(self) -> None:
        data = {
            "itsm_record": {
                "change_id": "CR-P1",
                "type": "normal",
                "risk_category": "high",
                "title": "Test",
                "description": "Test",
                "affected_services": ["svc-critical"],
                "requestor": "user",
                "approvers": ["approver"],
                "scheduled_window": {
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-01T02:00:00Z",
                },
            },
            "incident_history": [
                {
                    "incident_id": "INC-P1-001",
                    "service": "svc-critical",
                    "change_category": "deploy",
                    "severity": "P1",
                    "root_cause_summary": "Major outage",
                    "date": "2025-01-01",
                },
            ],
        }
        result = _pipeline(data)
        assert len(result.findings) == 1
        assert result.findings[0].severity == Severity.BLOCKER

    def test_five_incidents_is_blocker(self) -> None:
        incidents = [
            {
                "incident_id": f"INC-{i}",
                "service": "svc",
                "change_category": "config",
                "severity": "P3",
                "root_cause_summary": f"Issue {i}",
                "date": f"2025-0{i}-01",
            }
            for i in range(1, 6)
        ]
        data = {
            "itsm_record": {
                "change_id": "CR-MANY",
                "type": "standard",
                "risk_category": "medium",
                "title": "Test",
                "description": "Test",
                "affected_services": ["svc"],
                "requestor": "user",
                "approvers": ["approver"],
                "scheduled_window": {
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-01T02:00:00Z",
                },
            },
            "incident_history": incidents,
        }
        result = _pipeline(data)
        assert len(result.findings) == 1
        assert result.findings[0].severity == Severity.BLOCKER
