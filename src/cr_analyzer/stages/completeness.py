"""Stage 3: Completeness Check — ITIL-required artifact checklist per change type."""

from __future__ import annotations

from cr_analyzer.models.enums import ChangeType, FindingDimension, Severity
from cr_analyzer.models.findings import Finding, FindingEvidence
from cr_analyzer.models.outputs import CompletenessOutput, NormalizeOutput


def _make_finding(
    severity: Severity,
    finding: str,
    artifact: str,
    remediation: str,
    *,
    dimension: FindingDimension = FindingDimension.COMPLETENESS,
    **extra_evidence: object,
) -> Finding:
    return Finding(
        dimension=dimension,
        severity=severity,
        finding=finding,
        evidence=FindingEvidence(artifact=artifact, **extra_evidence),
        remediation=remediation,
    )


def _check_universal_fields(norm: NormalizeOutput) -> list[Finding]:
    """Fields required for ALL change types."""
    findings: list[Finding] = []

    if not norm.title:
        findings.append(_make_finding(
            Severity.WARNING,
            "Change request has no title.",
            "itsm_record",
            "Add a descriptive title to the change request.",
            field="title",
        ))

    if not norm.description:
        findings.append(_make_finding(
            Severity.WARNING,
            "Change request has no description.",
            "itsm_record",
            "Add a description explaining what the change does and why.",
            field="description",
        ))

    if not norm.affected_services:
        findings.append(_make_finding(
            Severity.BLOCKER,
            "No affected services listed.",
            "itsm_record",
            "List all services impacted by this change.",
            field="affected_services",
        ))

    return findings


def _check_normal_type(norm: NormalizeOutput) -> list[Finding]:
    """Normal changes require runbook + rollback + risk assessment."""
    findings: list[Finding] = []

    if norm.runbook is None:
        findings.append(_make_finding(
            Severity.BLOCKER,
            "Normal change missing runbook. CAB requires procedural steps for review.",
            "runbook",
            "Provide a runbook with numbered steps, affected services, and rollback triggers.",
        ))

    if norm.rollback is None:
        findings.append(_make_finding(
            Severity.BLOCKER,
            "Normal change missing rollback plan. No documented revert procedure.",
            "rollback_plan",
            "Provide a rollback plan with revert steps, assumptions, and duration estimate.",
        ))

    return findings


def _check_emergency_type(norm: NormalizeOutput) -> list[Finding]:
    """Emergency changes require rollback and executive approver."""
    findings: list[Finding] = []

    if norm.rollback is None:
        findings.append(_make_finding(
            Severity.BLOCKER,
            "Emergency change missing rollback plan. Critical for fast revert.",
            "rollback_plan",
            "Provide a rollback plan with revert steps and estimated duration.",
        ))

    return findings


def _check_standard_type(norm: NormalizeOutput) -> list[Finding]:
    """Standard changes: runbook is optional (info if missing)."""
    findings: list[Finding] = []

    if norm.runbook is None:
        findings.append(_make_finding(
            Severity.INFO,
            "Standard change has no runbook. Recommended but not required.",
            "runbook",
            "Consider adding a runbook for operational reference.",
        ))

    return findings


def _check_customer_facing(norm: NormalizeOutput) -> list[Finding]:
    """Customer-facing scope requires communication plan."""
    findings: list[Finding] = []

    if norm.is_customer_facing and norm.comms is None:
        findings.append(_make_finding(
            Severity.WARNING,
            "Customer-facing change has no communication plan. "
            "Affected stakeholders may not be notified.",
            "communication_plan",
            "Add a communication plan covering customer notification, internal alerts, and escalation.",
            dimension=FindingDimension.COMMUNICATION_GAPS,
        ))

    return findings


def run_completeness(norm: NormalizeOutput) -> CompletenessOutput:
    """Check ITIL-required artifacts per change type."""
    findings: list[Finding] = []

    findings.extend(_check_universal_fields(norm))

    match norm.change_type:
        case ChangeType.NORMAL:
            findings.extend(_check_normal_type(norm))
        case ChangeType.EMERGENCY:
            findings.extend(_check_emergency_type(norm))
        case ChangeType.STANDARD:
            findings.extend(_check_standard_type(norm))

    findings.extend(_check_customer_facing(norm))

    has_blocking = any(
        f.severity in (Severity.BLOCKER, Severity.WARNING) for f in findings
    )

    return CompletenessOutput(
        change_id=norm.change_id,
        findings=findings,
        complete=not has_blocking,
    )
