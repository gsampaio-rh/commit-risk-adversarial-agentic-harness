"""Tests for BPI 2014 adapter — requires data/bpi2014/ CSVs."""

from __future__ import annotations

from pathlib import Path

import pytest

from cr_analyzer.models.bundle import CRBundle

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "bpi2014"
SKIP_REASON = "BPI 2014 CSVs not found at data/bpi2014/"
has_data = (DATA_DIR / "Detail_Change.csv").exists()


@pytest.mark.skipif(not has_data, reason=SKIP_REASON)
class TestBpi2014Adapter:
    """Full adapter tests against real BPI 2014 data."""

    @pytest.fixture(scope="class")
    def bpi_data(self):
        from cr_analyzer.adapters.bpi2014 import load_bpi2014
        bundles, cab_windows = load_bpi2014(DATA_DIR)
        return bundles, cab_windows

    def test_change_count(self, bpi_data) -> None:
        bundles, _ = bpi_data
        assert len(bundles) >= 17000, f"Expected >=17K changes, got {len(bundles)}"

    def test_all_bundles_valid(self, bpi_data) -> None:
        bundles, _ = bpi_data
        for cid, bundle in list(bundles.items())[:100]:
            assert isinstance(bundle, CRBundle)
            assert bundle.itsm_record.change_id == cid

    def test_cab_window_count(self, bpi_data) -> None:
        _, cab_windows = bpi_data
        assert len(cab_windows) >= 49, f"Expected >=49 weeks, got {len(cab_windows)}"

    def test_cab_changes_count(self, bpi_data) -> None:
        """374 unique CAB IDs in CSV; 1 has empty Planned End → 373 parseable."""
        _, cab_windows = bpi_data
        total_cab = sum(len(w) for w in cab_windows.values())
        assert total_cab >= 373, f"Expected >=373 CAB changes, got {total_cab}"

    def test_risk_mapping(self, bpi_data) -> None:
        bundles, _ = bpi_data
        risk_values = {b.itsm_record.risk_category.value for b in bundles.values()}
        assert risk_values <= {"low", "medium", "high"}

    def test_type_derivation(self, bpi_data) -> None:
        bundles, _ = bpi_data
        types = {b.itsm_record.type.value for b in bundles.values()}
        assert types <= {"standard", "normal", "emergency"}
        assert "standard" in types
        assert "normal" in types

    def test_affected_services_populated(self, bpi_data) -> None:
        bundles, _ = bpi_data
        empty_count = sum(
            1 for b in bundles.values() if not b.itsm_record.affected_services
        )
        assert empty_count == 0, f"{empty_count} bundles have no affected services"

    def test_multi_ci_grouping(self, bpi_data) -> None:
        bundles, _ = bpi_data
        multi_ci = [
            b for b in bundles.values()
            if len(b.itsm_record.affected_services) > 1
        ]
        assert len(multi_ci) > 1000, "Expected >1000 multi-CI changes"

    def test_incident_enrichment(self, bpi_data) -> None:
        bundles, _ = bpi_data
        with_incidents = [
            b for b in bundles.values() if b.incident_history
        ]
        assert len(with_incidents) >= 100, f"Expected >=100 with incidents, got {len(with_incidents)}"

    def test_partial_bundle_shape(self, bpi_data) -> None:
        bundles, _ = bpi_data
        sample = next(iter(bundles.values()))
        assert sample.runbook is None
        assert sample.rollback_plan is None
        assert sample.cmdb_snapshot is None
        assert sample.sla_definitions is None
        assert sample.communication_plan is None
