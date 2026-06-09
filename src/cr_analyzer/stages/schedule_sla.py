"""Stage 6: Schedule & SLA Analysis — overlap detection on shared services."""

from __future__ import annotations

from itertools import combinations

from cr_analyzer.models.bundle import ScheduledWindow
from cr_analyzer.models.enums import Severity, SlaAnalysisMode
from cr_analyzer.models.outputs import (
    NormalizeOutput,
    ScheduleSlaOutput,
    SchedulingConflict,
)


def _windows_overlap(a: ScheduledWindow, b: ScheduledWindow) -> ScheduledWindow | None:
    """Return the overlap window if [a.start, a.end] ∩ [b.start, b.end] is non-empty."""
    start = max(a.start, b.start)
    end = min(a.end, b.end)
    if start < end:
        return ScheduledWindow(start=start, end=end)
    return None


def _overlap_severity(
    overlap: ScheduledWindow,
    cr_a: NormalizeOutput,
    cr_b: NormalizeOutput,
) -> Severity:
    """Determine severity based on overlap extent."""
    if cr_a.scheduled_window == cr_b.scheduled_window:
        return Severity.BLOCKER
    overlap_minutes = (overlap.end - overlap.start).total_seconds() / 60
    if overlap_minutes >= 60:
        return Severity.WARNING
    return Severity.INFO


def run_schedule_sla(crs: list[NormalizeOutput]) -> ScheduleSlaOutput:
    """Detect scheduling conflicts across CRs in a CAB window.

    Operates in overlap_only mode when SLA definitions are absent.
    """
    conflicts: list[SchedulingConflict] = []

    for cr_a, cr_b in combinations(crs, 2):
        shared = set(cr_a.affected_services) & set(cr_b.affected_services)
        if not shared:
            continue

        overlap = _windows_overlap(cr_a.scheduled_window, cr_b.scheduled_window)
        if overlap is None:
            continue

        severity = _overlap_severity(overlap, cr_a, cr_b)
        conflicts.append(SchedulingConflict(
            cr_pair=[cr_a.change_id, cr_b.change_id],
            shared_services=sorted(shared),
            overlap_window=overlap,
            severity=severity,
        ))

    has_sla = any(cr.sla for cr in crs)
    mode = SlaAnalysisMode.FULL if has_sla else SlaAnalysisMode.OVERLAP_ONLY

    return ScheduleSlaOutput(
        scheduling_conflicts=conflicts,
        sla_impact=[],
        sla_analysis_mode=mode,
    )
