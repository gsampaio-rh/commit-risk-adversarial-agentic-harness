"""Finding model — per-dimension assessment result with evidence."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .enums import FindingDimension, Severity


class FindingEvidence(BaseModel):
    """Evidence backing a finding.

    Only ``artifact`` is required. Dimension-specific fields (step_number,
    service_ref, cmdb_status, service, change_category, matching_incidents,
    etc.) are captured via ``extra="allow"`` so every dimension can attach
    its own evidence shape without a separate model per dimension.
    """

    model_config = ConfigDict(extra="allow")

    artifact: str = Field(description="Source artifact: runbook, rollback_plan, incident_history, …")


class Finding(BaseModel):
    """A single assessment finding from any pipeline stage."""

    dimension: FindingDimension
    severity: Severity
    finding: str = Field(description="Human-readable finding description")
    evidence: FindingEvidence
    remediation: str | None = Field(
        default=None, description="Suggested remediation action"
    )
