"""Conditional skip logic — determines which stages to skip for partial bundles."""

from __future__ import annotations

from cr_analyzer.models.enums import FindingDimension, Severity
from cr_analyzer.models.findings import Finding, FindingEvidence
from cr_analyzer.models.outputs import NormalizeOutput

SKIP_RULES: list[tuple[str, str, str]] = [
    ("runbook", "runbook_validation", "Runbook absent — skipping runbook validation stage."),
    ("rollback", "rollback_feasibility", "Rollback plan absent — skipping rollback feasibility stage."),
    ("cmdb", "dependency_chain", "CMDB snapshot absent — skipping dependency chain stage."),
]


def determine_skips(norm: NormalizeOutput) -> tuple[list[str], list[Finding]]:
    """Return (stages_to_skip, info_findings) based on missing artifacts."""
    skipped: list[str] = []
    findings: list[Finding] = []

    for artifact_attr, stage_name, message in SKIP_RULES:
        value = getattr(norm, artifact_attr, None)
        if value is None:
            skipped.append(stage_name)
            findings.append(Finding(
                dimension=FindingDimension.COMPLETENESS,
                severity=Severity.INFO,
                finding=message,
                evidence=FindingEvidence(
                    artifact=artifact_attr,
                    reason="artifact_absent",
                ),
            ))

    return skipped, findings
