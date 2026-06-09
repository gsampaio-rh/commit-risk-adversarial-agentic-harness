"""Tests for conditional skip logic."""

from __future__ import annotations

from typing import Any

from cr_analyzer.models.bundle import CRBundle
from cr_analyzer.models.enums import FindingDimension, Severity
from cr_analyzer.pipeline.skip import determine_skips
from cr_analyzer.stages.ingest import run_ingest
from cr_analyzer.stages.normalize import run_normalize


class TestSkipLogic:
    """Skip rules for partial bundles."""

    def test_full_bundle_no_skips(self, cr_001_data: dict[str, Any]) -> None:
        norm = run_normalize(run_ingest(CRBundle.model_validate(cr_001_data)))
        skipped, findings = determine_skips(norm)
        assert skipped == []
        assert findings == []

    def test_minimal_bundle_skips_three_stages(self, minimal_bundle_data: dict[str, Any]) -> None:
        norm = run_normalize(run_ingest(CRBundle.model_validate(minimal_bundle_data)))
        skipped, findings = determine_skips(norm)
        assert "runbook_validation" in skipped
        assert "rollback_feasibility" in skipped
        assert "dependency_chain" in skipped
        assert len(findings) == 3

    def test_bpi_bundle_skips_all_three(self, bpi_shaped_bundle_data: dict[str, Any]) -> None:
        norm = run_normalize(run_ingest(CRBundle.model_validate(bpi_shaped_bundle_data)))
        skipped, findings = determine_skips(norm)
        assert set(skipped) == {"runbook_validation", "rollback_feasibility", "dependency_chain"}

    def test_skip_findings_are_info(self, minimal_bundle_data: dict[str, Any]) -> None:
        norm = run_normalize(run_ingest(CRBundle.model_validate(minimal_bundle_data)))
        _, findings = determine_skips(norm)
        for f in findings:
            assert f.severity == Severity.INFO
            assert f.dimension == FindingDimension.COMPLETENESS

    def test_skip_finding_has_artifact_evidence(self, minimal_bundle_data: dict[str, Any]) -> None:
        norm = run_normalize(run_ingest(CRBundle.model_validate(minimal_bundle_data)))
        _, findings = determine_skips(norm)
        artifacts = {f.evidence.artifact for f in findings}
        assert "runbook" in artifacts
        assert "rollback" in artifacts
        assert "cmdb" in artifacts
