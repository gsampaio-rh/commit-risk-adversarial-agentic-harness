"""Tests for Stage 1: Ingest."""

from __future__ import annotations

from typing import Any

from cr_analyzer.models.bundle import CRBundle
from cr_analyzer.stages.ingest import run_ingest


class TestIngestFullBundle:
    """Ingest on the full cr-001 fixture."""

    def test_change_id_preserved(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        out = run_ingest(bundle)
        assert out.change_id == "CR-2026-0451"

    def test_runbook_parsed(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        out = run_ingest(bundle)
        assert out.runbook is not None
        assert len(out.runbook.sections) > 0
        assert len(out.runbook.steps) > 0
        assert len(out.runbook.commands) > 0
        assert "order-service" in out.runbook.service_refs

    def test_rollback_parsed(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        out = run_ingest(bundle)
        assert out.rollback is not None
        assert len(out.rollback.steps) > 0
        assert len(out.rollback.commands) > 0

    def test_json_artifacts_passthrough(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        out = run_ingest(bundle)
        assert out.cmdb is not None
        assert len(out.cmdb.nodes) == 6
        assert out.sla is not None
        assert len(out.sla) == 3
        assert out.schedule is not None
        assert out.incidents is not None
        assert out.pr_flags is not None

    def test_comms_passthrough(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        out = run_ingest(bundle)
        assert out.comms is not None
        assert "Communication Plan" in out.comms

    def test_output_serializable(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        out = run_ingest(bundle)
        json_str = out.model_dump_json()
        assert len(json_str) > 0


class TestIngestPartialBundle:
    """Ingest on partial bundles (BPI 2014 path)."""

    def test_minimal_bundle(self, minimal_bundle_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(minimal_bundle_data)
        out = run_ingest(bundle)
        assert out.change_id == "CR-MINIMAL-001"
        assert out.runbook is None
        assert out.rollback is None
        assert out.cmdb is None
        assert out.sla is None
        assert out.schedule is None
        assert out.comms is None
        assert out.pr_flags is None

    def test_bpi_shaped_bundle(self, bpi_shaped_bundle_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(bpi_shaped_bundle_data)
        out = run_ingest(bundle)
        assert out.change_id == "CHG0012345"
        assert out.runbook is None
        assert out.incidents is not None
        assert len(out.incidents) == 2

    def test_empty_runbook_string(self) -> None:
        bundle = CRBundle.model_validate({
            "itsm_record": {
                "change_id": "CR-EMPTY",
                "type": "standard",
                "risk_category": "low",
                "title": "Empty runbook test",
                "description": "Test",
                "affected_services": ["svc"],
                "requestor": "user",
                "approvers": ["approver"],
                "scheduled_window": {
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-01T02:00:00Z",
                },
            },
            "runbook": "",
        })
        out = run_ingest(bundle)
        assert out.runbook is not None
        assert out.runbook.sections == []
        assert out.runbook.steps == []
        assert out.runbook.commands == []
        assert out.runbook.raw == ""
