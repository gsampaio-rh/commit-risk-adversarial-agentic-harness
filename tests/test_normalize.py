"""Tests for Stage 2: Normalize."""

from __future__ import annotations

from typing import Any

from cr_analyzer.models.bundle import CRBundle
from cr_analyzer.models.enums import ChangeType, RiskCategory
from cr_analyzer.stages.ingest import run_ingest
from cr_analyzer.stages.normalize import run_normalize


class TestNormalizeFullBundle:
    """Normalize on the full cr-001 fixture (has CMDB)."""

    def test_change_id_preserved(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        norm = run_normalize(run_ingest(bundle))
        assert norm.change_id == "CR-2026-0451"

    def test_enums_standardized(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        norm = run_normalize(run_ingest(bundle))
        assert norm.change_type == ChangeType.NORMAL
        assert norm.risk_category == RiskCategory.LOW

    def test_customer_facing_from_cmdb(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        norm = run_normalize(run_ingest(bundle))
        # order-service is tier 2 → customer-facing
        assert norm.is_customer_facing is True

    def test_affected_tier_from_cmdb(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        norm = run_normalize(run_ingest(bundle))
        # affected: order-service (tier 2), notification-queue (tier 3) → min = 2
        assert norm.affected_tier == 2

    def test_artifacts_pass_through(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        norm = run_normalize(run_ingest(bundle))
        assert norm.runbook is not None
        assert norm.rollback is not None
        assert norm.cmdb is not None
        assert norm.sla is not None
        assert norm.incidents is not None

    def test_all_itsm_fields_present(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        norm = run_normalize(run_ingest(bundle))
        assert norm.title != ""
        assert norm.description != ""
        assert len(norm.affected_services) > 0
        assert norm.requestor != ""
        assert len(norm.approvers) > 0
        assert norm.scheduled_window is not None

    def test_output_serializable(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        norm = run_normalize(run_ingest(bundle))
        json_str = norm.model_dump_json()
        assert len(json_str) > 0
        restored = type(norm).model_validate_json(json_str)
        assert restored.change_id == norm.change_id


class TestNormalizePartialBundle:
    """Normalize on partial bundles (no CMDB path)."""

    def test_no_cmdb_not_customer_facing(self, minimal_bundle_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(minimal_bundle_data)
        norm = run_normalize(run_ingest(bundle))
        assert norm.is_customer_facing is False

    def test_no_cmdb_tier_defaults_to_4(self, minimal_bundle_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(minimal_bundle_data)
        norm = run_normalize(run_ingest(bundle))
        assert norm.affected_tier == 4

    def test_bpi_shaped_bundle(self, bpi_shaped_bundle_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(bpi_shaped_bundle_data)
        norm = run_normalize(run_ingest(bundle))
        assert norm.change_type == ChangeType.STANDARD
        assert norm.risk_category == RiskCategory.LOW
        assert norm.is_customer_facing is False
        assert norm.affected_tier == 4
        assert norm.runbook is None
        assert norm.rollback is None
        assert norm.cmdb is None
        assert norm.incidents is not None

    def test_empty_title_preserved(self, bpi_shaped_bundle_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(bpi_shaped_bundle_data)
        norm = run_normalize(run_ingest(bundle))
        assert norm.title == ""


class TestNormalizeCr002:
    """Normalize on cr-002 (high risk, has CMDB with tier 1 services)."""

    def test_high_risk_payment_change(self, cr_002_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_002_data)
        norm = run_normalize(run_ingest(bundle))
        assert norm.risk_category == RiskCategory.HIGH
        assert norm.change_type == ChangeType.NORMAL

    def test_tier_1_customer_facing(self, cr_002_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_002_data)
        norm = run_normalize(run_ingest(bundle))
        # payment-api and payment-db are tier 1 → customer-facing, tier = 1
        assert norm.is_customer_facing is True
        assert norm.affected_tier == 1
