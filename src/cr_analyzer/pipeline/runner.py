"""E2E pipeline runner — sequential stage execution with disk checkpoints."""

from __future__ import annotations

import json
import time
from pathlib import Path

from cr_analyzer.models.bundle import CRBundle
from cr_analyzer.models.outputs import (
    CabReport,
    CabSummary,
    CompletenessOutput,
    HistoricalPatternOutput,
    NormalizeOutput,
    ScheduleSlaOutput,
)
from cr_analyzer.pipeline.skip import determine_skips
from cr_analyzer.stages.completeness import run_completeness
from cr_analyzer.stages.historical_pattern import run_historical_pattern
from cr_analyzer.stages.ingest import run_ingest
from cr_analyzer.stages.normalize import run_normalize
from cr_analyzer.stages.risk_synthesis import (
    render_cr_report_md,
    render_summary_md,
    synthesize_report,
    synthesize_summary,
)
from cr_analyzer.stages.schedule_sla import run_schedule_sla


def _checkpoint(output_dir: Path, name: str, data: object) -> None:
    """Write a stage output to disk as JSON checkpoint."""
    if output_dir is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.json"
    if hasattr(data, "model_dump_json"):
        path.write_text(data.model_dump_json(indent=2))
    else:
        path.write_text(json.dumps(data, indent=2, default=str))


def run_pipeline_single(
    bundle: CRBundle,
    output_dir: Path | None = None,
) -> tuple[CabReport, NormalizeOutput]:
    """Run the full pipeline on a single CR bundle.

    Returns (CabReport, NormalizeOutput) for use in batch aggregation.
    """
    # Stage 1: Ingest
    ingest_out = run_ingest(bundle)
    if output_dir:
        _checkpoint(output_dir, "ingest", ingest_out)

    # Stage 2: Normalize
    norm_out = run_normalize(ingest_out)
    if output_dir:
        _checkpoint(output_dir, "normalize", norm_out)

    # Determine skips
    stages_skipped, skip_findings = determine_skips(norm_out)

    # Stage 3: Completeness
    completeness_out = run_completeness(norm_out)
    if output_dir:
        _checkpoint(output_dir, "completeness", completeness_out)

    # Stage 8: Historical Pattern
    hp_out = run_historical_pattern(norm_out)
    if output_dir:
        _checkpoint(output_dir, "historical-pattern", hp_out)

    # Aggregate findings
    all_findings = (
        skip_findings
        + completeness_out.findings
        + hp_out.findings
    )

    stages_executed = 4  # ingest, normalize, completeness, historical-pattern
    stages_degraded = 0

    return (
        synthesize_report(
            norm_out.change_id,
            all_findings,
            stages_skipped,
            stages_executed,
            stages_degraded,
        ),
        norm_out,
    )


def run_pipeline_batch(
    bundles: list[CRBundle],
    window_id: str,
    output_dir: Path | None = None,
) -> tuple[CabSummary, list[CabReport]]:
    """Run the pipeline on a CAB window batch of CRs.

    Executes per-CR stages, then cross-CR schedule analysis, then synthesis.
    """
    t0 = time.monotonic()

    reports: list[CabReport] = []
    norms: list[NormalizeOutput] = []
    per_cr_findings: dict[str, list] = {}

    for bundle in bundles:
        cr_dir = output_dir / bundle.itsm_record.change_id if output_dir else None
        report, norm = run_pipeline_single(bundle, cr_dir)
        reports.append(report)
        norms.append(norm)
        per_cr_findings[norm.change_id] = list(report.findings)

    # Stage 6: Schedule & SLA (cross-CR)
    schedule_out = run_schedule_sla(norms)
    if output_dir:
        _checkpoint(output_dir, "schedule-sla", schedule_out)

    # Re-synthesize reports with schedule findings injected
    final_reports: list[CabReport] = []
    for report in reports:
        cr_schedule_findings = [
            f for sc in schedule_out.scheduling_conflicts
            if report.change_id in sc.cr_pair
            for f in _schedule_conflict_to_findings(sc, report.change_id)
        ]
        if cr_schedule_findings:
            all_findings = list(report.findings) + cr_schedule_findings
            stages_degraded = 1 if schedule_out.sla_analysis_mode.value == "overlap_only" else 0
            new_report = synthesize_report(
                report.change_id,
                all_findings,
                report.stages_skipped,
                report.analysis_coverage.executed + 1,
                stages_degraded,
            )
            final_reports.append(new_report)
        else:
            final_reports.append(report)

    # Write per-CR outputs
    if output_dir:
        for report in final_reports:
            cr_dir = output_dir / report.change_id
            _checkpoint(cr_dir, "cab-report", report)
            md = render_cr_report_md(report)
            (cr_dir / "change-risk-assessment.md").write_text(md)

    elapsed = time.monotonic() - t0
    summary = synthesize_summary(
        window_id, final_reports, schedule_out,
        wall_clock_seconds=elapsed,
    )

    if output_dir:
        _checkpoint(output_dir, "cab-summary", summary)
        md = render_summary_md(summary)
        (output_dir / "cab-summary.md").write_text(md)

    return summary, final_reports


def _schedule_conflict_to_findings(sc, change_id: str):
    """Convert a SchedulingConflict into Finding objects for the affected CR."""
    from cr_analyzer.models.enums import FindingDimension
    from cr_analyzer.models.findings import Finding, FindingEvidence

    other_cr = [cid for cid in sc.cr_pair if cid != change_id]
    other_label = other_cr[0] if other_cr else "unknown"

    return [Finding(
        dimension=FindingDimension.SCHEDULING_CONFLICTS,
        severity=sc.severity,
        finding=(
            f"Scheduling overlap with {other_label} on "
            f"{', '.join(sc.shared_services)} "
            f"({sc.overlap_window.start.isoformat()} to {sc.overlap_window.end.isoformat()})."
        ),
        evidence=FindingEvidence(
            artifact="maintenance_schedule",
            cr_pair=sc.cr_pair,
            shared_services=sc.shared_services,
        ),
        remediation=f"Reschedule to avoid overlap with {other_label} or confirm non-conflicting changes.",
    )]
