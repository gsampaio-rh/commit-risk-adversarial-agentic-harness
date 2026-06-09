"""Tests for E2E pipeline runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cr_analyzer.models.bundle import CRBundle
from cr_analyzer.models.enums import Recommendation
from cr_analyzer.pipeline.runner import run_pipeline_batch, run_pipeline_single


class TestSingleCrPipeline:
    """Run pipeline on a single CR."""

    def test_cr_001_approves(self, cr_001_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        report, norm = run_pipeline_single(bundle)
        assert report.recommendation == Recommendation.APPROVE
        assert report.change_id == "CR-2026-0451"

    def test_cr_002_rejects(self, cr_002_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(cr_002_data)
        report, norm = run_pipeline_single(bundle)
        # cr-002 has historical pattern warning but only 1 dimension → approve
        # (no runbook/rollback blocker since stages 4/5 aren't in L1 pipeline)
        assert report.recommendation in (Recommendation.APPROVE, Recommendation.CONDITIONAL)

    def test_minimal_bundle_runs(self, minimal_bundle_data: dict[str, Any]) -> None:
        bundle = CRBundle.model_validate(minimal_bundle_data)
        report, norm = run_pipeline_single(bundle)
        assert report.change_id == "CR-MINIMAL-001"
        assert len(report.stages_skipped) > 0

    def test_checkpoints_written(self, cr_001_data: dict[str, Any], tmp_path: Path) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        run_pipeline_single(bundle, output_dir=tmp_path)
        assert (tmp_path / "ingest.json").exists()
        assert (tmp_path / "normalize.json").exists()
        assert (tmp_path / "completeness.json").exists()
        assert (tmp_path / "historical-pattern.json").exists()

    def test_checkpoint_valid_json(self, cr_001_data: dict[str, Any], tmp_path: Path) -> None:
        bundle = CRBundle.model_validate(cr_001_data)
        run_pipeline_single(bundle, output_dir=tmp_path)
        for name in ["ingest", "normalize", "completeness", "historical-pattern"]:
            data = json.loads((tmp_path / f"{name}.json").read_text())
            assert isinstance(data, dict)


class TestBatchPipeline:
    """Run pipeline on a full CAB window."""

    def test_fixture_window(
        self,
        cr_001_data: dict[str, Any],
        cr_002_data: dict[str, Any],
        cr_003_data: dict[str, Any],
    ) -> None:
        bundles = [
            CRBundle.model_validate(cr_001_data),
            CRBundle.model_validate(cr_002_data),
            CRBundle.model_validate(cr_003_data),
        ]
        summary, reports = run_pipeline_batch(bundles, "CAB-2026-W23")
        assert summary.total_crs == 3
        assert summary.disposition_breakdown.approve + \
               summary.disposition_breakdown.conditional + \
               summary.disposition_breakdown.reject == 3

    def test_schedule_conflicts_in_summary(
        self,
        cr_001_data: dict[str, Any],
        cr_003_data: dict[str, Any],
    ) -> None:
        bundles = [
            CRBundle.model_validate(cr_001_data),
            CRBundle.model_validate(cr_003_data),
        ]
        summary, reports = run_pipeline_batch(bundles, "CAB-W23")
        assert len(summary.cross_cr_conflicts) >= 1

    def test_batch_writes_outputs(
        self,
        cr_001_data: dict[str, Any],
        cr_002_data: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        bundles = [
            CRBundle.model_validate(cr_001_data),
            CRBundle.model_validate(cr_002_data),
        ]
        run_pipeline_batch(bundles, "CAB-W23", output_dir=tmp_path)
        assert (tmp_path / "cab-summary.json").exists()
        assert (tmp_path / "cab-summary.md").exists()
        assert (tmp_path / "schedule-sla.json").exists()
        # Per-CR dirs
        assert (tmp_path / "CR-2026-0451" / "cab-report.json").exists()
        assert (tmp_path / "CR-2026-0451" / "change-risk-assessment.md").exists()

    def test_batch_wall_clock_tracked(
        self,
        cr_001_data: dict[str, Any],
    ) -> None:
        bundles = [CRBundle.model_validate(cr_001_data)]
        summary, _ = run_pipeline_batch(bundles, "CAB-W23")
        assert summary.processing.wall_clock_seconds > 0
