"""Investigation report schema: Pydantic v2 models for agent output.

CommitInvestigationReport is the primary output of the investigative agent.
Schema validation enforces that reports contain evidence (no empty reports).
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class RiskLevel(str, Enum):
    """Risk level assigned by the investigative agent."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EvidenceType(str, Enum):
    """Type of evidence cited in the investigation."""

    DIFF_HUNK = "diff_hunk"
    FILE_HISTORY = "file_history"
    COMMIT_MESSAGE = "commit_message"
    NUMERIC_FEATURE = "numeric_feature"
    AUTHOR_STATS = "author_stats"
    GIT_BLAME = "git_blame"
    OTHER = "other"


class RecommendationPriority(str, Enum):
    """Priority level for a recommendation."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EvidenceItem(BaseModel):
    """A single piece of evidence supporting the investigation findings."""

    type: EvidenceType
    source: str = Field(description="Where the evidence came from (e.g., file path, tool name)")
    content: str = Field(description="The actual evidence content")
    relevance: str = Field(description="Why this evidence matters to the assessment")


class LocalizationClaim(BaseModel):
    """A claim about where the risk is localized in the codebase."""

    file: str = Field(description="File path where the risk is located")
    lines: tuple[int, int] | None = Field(
        default=None, description="Start and end line numbers (optional)"
    )
    rationale: str = Field(description="Why this location is risky")


class Recommendation(BaseModel):
    """An actionable recommendation based on investigation findings."""

    action: str = Field(description="What should be done")
    priority: RecommendationPriority
    rationale: str = Field(description="Why this action is recommended")


class RiskAssessment(BaseModel):
    """Overall risk assessment for the commit."""

    level: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in the assessment (0-1)")

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v


class CommitInvestigationReport(BaseModel):
    """Primary output of the investigative agent.

    Contains the full investigation results: risk assessment, evidence,
    findings, localization claims, and recommendations.
    """

    commit_id: str
    project: str
    risk_assessment: RiskAssessment
    evidence: list[EvidenceItem] = Field(min_length=1)
    findings: list[str] = Field(default_factory=list)
    localization: list[LocalizationClaim] = Field(default_factory=list)
    reasoning_summary: str = Field(description="High-level summary of the investigation reasoning")
    recommendations: list[Recommendation] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    turn_count: int = Field(ge=1, le=10, description="Number of agent turns used")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence_not_empty(self) -> CommitInvestigationReport:
        if not self.evidence:
            raise ValueError("Investigation report must contain at least one evidence item")
        return self


def export_json_schema() -> str:
    """Export the JSON schema for CommitInvestigationReport.

    Returns a stable, indented JSON string suitable for documentation
    and downstream tooling integration.
    """
    schema = CommitInvestigationReport.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True)
