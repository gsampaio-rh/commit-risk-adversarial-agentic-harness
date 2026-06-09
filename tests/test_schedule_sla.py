"""Tests for Stage 6: Schedule & SLA Analysis."""

from __future__ import annotations

from typing import Any

from cr_analyzer.models.bundle import CRBundle, ScheduledWindow
from cr_analyzer.models.enums import ChangeType, RiskCategory, Severity, SlaAnalysisMode
from cr_analyzer.models.outputs import NormalizeOutput
from cr_analyzer.stages.ingest import run_ingest
from cr_analyzer.stages.normalize import run_normalize
from cr_analyzer.stages.schedule_sla import run_schedule_sla


def _make_norm(
    change_id: str,
    services: list[str],
    start: str,
    end: str,
    *,
    has_sla: bool = False,
) -> NormalizeOutput:
    """Helper to build a NormalizeOutput for schedule tests."""
    return NormalizeOutput(
        change_id=change_id,
        change_type=ChangeType.NORMAL,
        risk_category=RiskCategory.LOW,
        title="Test",
        description="Test",
        affected_services=services,
        requestor="user",
        approvers=["approver"],
        scheduled_window=ScheduledWindow(start=start, end=end),
        is_customer_facing=False,
        affected_tier=4,
        sla=[{"tier": 1, "monthly_downtime_budget_min": 43,
              "consumed_this_month_min": 0, "measurement_window": "2026-06"}] if has_sla else None,
    )


class TestOverlapDetection:
    """Interval overlap on shared services."""

    def test_overlapping_crs_shared_service(self) -> None:
        cr_a = _make_norm("CR-A", ["svc-1"], "2026-06-07T02:00:00Z", "2026-06-07T04:00:00Z")
        cr_b = _make_norm("CR-B", ["svc-1"], "2026-06-07T03:00:00Z", "2026-06-07T05:00:00Z")
        result = run_schedule_sla([cr_a, cr_b])
        assert len(result.scheduling_conflicts) == 1
        conflict = result.scheduling_conflicts[0]
        assert set(conflict.cr_pair) == {"CR-A", "CR-B"}
        assert "svc-1" in conflict.shared_services

    def test_non_overlapping_crs(self) -> None:
        cr_a = _make_norm("CR-A", ["svc-1"], "2026-06-07T02:00:00Z", "2026-06-07T03:00:00Z")
        cr_b = _make_norm("CR-B", ["svc-1"], "2026-06-07T04:00:00Z", "2026-06-07T05:00:00Z")
        result = run_schedule_sla([cr_a, cr_b])
        assert len(result.scheduling_conflicts) == 0

    def test_overlapping_but_no_shared_service(self) -> None:
        cr_a = _make_norm("CR-A", ["svc-1"], "2026-06-07T02:00:00Z", "2026-06-07T04:00:00Z")
        cr_b = _make_norm("CR-B", ["svc-2"], "2026-06-07T03:00:00Z", "2026-06-07T05:00:00Z")
        result = run_schedule_sla([cr_a, cr_b])
        assert len(result.scheduling_conflicts) == 0

    def test_exact_window_match_is_blocker(self) -> None:
        cr_a = _make_norm("CR-A", ["svc-1"], "2026-06-07T02:00:00Z", "2026-06-07T04:00:00Z")
        cr_b = _make_norm("CR-B", ["svc-1"], "2026-06-07T02:00:00Z", "2026-06-07T04:00:00Z")
        result = run_schedule_sla([cr_a, cr_b])
        assert len(result.scheduling_conflicts) == 1
        assert result.scheduling_conflicts[0].severity == Severity.BLOCKER

    def test_partial_overlap_severity(self) -> None:
        cr_a = _make_norm("CR-A", ["svc-1"], "2026-06-07T02:00:00Z", "2026-06-07T04:00:00Z")
        cr_b = _make_norm("CR-B", ["svc-1"], "2026-06-07T03:00:00Z", "2026-06-07T05:00:00Z")
        result = run_schedule_sla([cr_a, cr_b])
        # 60-minute overlap → warning
        assert result.scheduling_conflicts[0].severity == Severity.WARNING

    def test_short_overlap_is_info(self) -> None:
        cr_a = _make_norm("CR-A", ["svc-1"], "2026-06-07T02:00:00Z", "2026-06-07T03:00:00Z")
        cr_b = _make_norm("CR-B", ["svc-1"], "2026-06-07T02:30:00Z", "2026-06-07T03:30:00Z")
        result = run_schedule_sla([cr_a, cr_b])
        # 30-minute overlap → info
        assert result.scheduling_conflicts[0].severity == Severity.INFO


class TestFixtureWindow:
    """Test with the cr-001/002/003 fixture window."""

    def test_fixture_window_detects_overlap(
        self, cr_001_data: dict[str, Any], cr_003_data: dict[str, Any],
    ) -> None:
        norm_1 = run_normalize(run_ingest(CRBundle.model_validate(cr_001_data)))
        norm_3 = run_normalize(run_ingest(CRBundle.model_validate(cr_003_data)))
        result = run_schedule_sla([norm_1, norm_3])
        # cr-001 (02:00-04:00) and cr-003 (02:30-04:30) overlap on order-service
        assert len(result.scheduling_conflicts) == 1
        assert "order-service" in result.scheduling_conflicts[0].shared_services


class TestSlaMode:
    """SLA analysis mode detection."""

    def test_no_sla_is_overlap_only(self) -> None:
        cr = _make_norm("CR-A", ["svc-1"], "2026-06-07T02:00:00Z", "2026-06-07T04:00:00Z")
        result = run_schedule_sla([cr])
        assert result.sla_analysis_mode == SlaAnalysisMode.OVERLAP_ONLY

    def test_with_sla_is_full(self) -> None:
        cr = _make_norm(
            "CR-A", ["svc-1"], "2026-06-07T02:00:00Z", "2026-06-07T04:00:00Z",
            has_sla=True,
        )
        result = run_schedule_sla([cr])
        assert result.sla_analysis_mode == SlaAnalysisMode.FULL
