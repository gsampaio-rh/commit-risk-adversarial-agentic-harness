"""Stage output models — one per pipeline stage."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .bundle import (
    CmdbSnapshot,
    Incident,
    PrScopeFlags,
    ScheduledWindow,
    ScheduleEntry,
    SlaDefinition,
)
from .enums import (
    ChangeType,
    Recommendation,
    RiskCategory,
    Severity,
    SlaAnalysisMode,
)
from .findings import Finding


# ---------------------------------------------------------------------------
# Stage 1: Ingest
# ---------------------------------------------------------------------------


class ParsedMarkdown(BaseModel):
    """Structured extraction from a Markdown artifact (runbook / rollback)."""

    sections: list[str] = Field(default_factory=list, description="Section headings")
    steps: list[str] = Field(default_factory=list, description="Numbered procedural steps")
    service_refs: list[str] = Field(default_factory=list, description="Service names found in text")
    commands: list[str] = Field(default_factory=list, description="Code-block / inline commands")
    raw: str = Field(description="Original Markdown content")


class IngestOutput(BaseModel):
    """Stage 1 output — parsed CR bundle artifacts."""

    change_id: str
    itsm_record: dict = Field(description="Raw ITSM record fields")
    runbook: ParsedMarkdown | None = None
    rollback: ParsedMarkdown | None = None
    cmdb: CmdbSnapshot | None = None
    sla: list[SlaDefinition] | None = None
    schedule: list[ScheduleEntry] | None = None
    comms: str | None = None
    incidents: list[Incident] | None = None
    pr_flags: PrScopeFlags | None = None


# ---------------------------------------------------------------------------
# Stage 2: Normalize
# ---------------------------------------------------------------------------


class NormalizeOutput(BaseModel):
    """Stage 2 output — vendor-neutral CR with derived fields."""

    change_id: str
    change_type: ChangeType
    risk_category: RiskCategory
    title: str
    description: str
    affected_services: list[str]
    requestor: str
    approvers: list[str]
    scheduled_window: ScheduledWindow
    expected_duration_min: int | None = None

    is_customer_facing: bool = Field(
        description="Derived from CMDB tier: tier 1-2 = True"
    )
    affected_tier: int = Field(
        default=4,
        description="Min tier across affected services in CMDB; 4 if no CMDB",
    )

    runbook: ParsedMarkdown | None = None
    rollback: ParsedMarkdown | None = None
    cmdb: CmdbSnapshot | None = None
    sla: list[SlaDefinition] | None = None
    schedule: list[ScheduleEntry] | None = None
    comms: str | None = None
    incidents: list[Incident] | None = None
    pr_flags: PrScopeFlags | None = None


# ---------------------------------------------------------------------------
# Stage 3: Completeness Check
# ---------------------------------------------------------------------------


class CompletenessOutput(BaseModel):
    """Stage 3 output — ITIL artifact completeness assessment."""

    change_id: str
    findings: list[Finding] = Field(default_factory=list)
    complete: bool = Field(description="True only when zero blocker/warning findings")


# ---------------------------------------------------------------------------
# Stage 6: Schedule & SLA Analysis (cross-CR)
# ---------------------------------------------------------------------------


class SchedulingConflict(BaseModel):
    """A pair of CRs with overlapping windows on shared services."""

    cr_pair: list[str] = Field(min_length=2, max_length=2)
    shared_services: list[str]
    overlap_window: ScheduledWindow
    severity: Severity


class SlaImpact(BaseModel):
    """Per-tier SLA budget impact assessment."""

    tier: int
    union_downtime_min: float
    remaining_budget_min: float
    breach: bool
    severity: Severity


class ScheduleSlaOutput(BaseModel):
    """Stage 6 output — schedule conflicts and SLA impact."""

    scheduling_conflicts: list[SchedulingConflict] = Field(default_factory=list)
    sla_impact: list[SlaImpact] = Field(default_factory=list)
    sla_analysis_mode: SlaAnalysisMode = SlaAnalysisMode.FULL


# ---------------------------------------------------------------------------
# Stage 8: Historical Pattern
# ---------------------------------------------------------------------------


class HistoricalPatternOutput(BaseModel):
    """Stage 8 output — incident history pattern alerts."""

    change_id: str
    findings: list[Finding] = Field(default_factory=list)
    method_used: Literal["exact_match", "embedding_similarity", "dual"] = Field(
        default="exact_match",
        description="L1=exact_match, L2=embedding_similarity, both=dual",
    )


# ---------------------------------------------------------------------------
# Stage 9: Risk Synthesis & CAB Report
# ---------------------------------------------------------------------------


class DimensionSeverityCounts(BaseModel):
    """Severity breakdown for a single assessment dimension."""

    blocker: int = 0
    warning: int = 0
    info: int = 0


class AnalysisCoverage(BaseModel):
    """Tracks which stages ran, skipped, or degraded."""

    executed: int = 0
    skipped: int = 0
    degraded: int = 0


class CabReport(BaseModel):
    """Per-CR risk assessment and CAB recommendation."""

    change_id: str
    risk_level: Literal["low", "medium", "high", "critical"]
    recommendation: Recommendation
    findings: list[Finding] = Field(default_factory=list)
    conditional_actions: list[str] = Field(default_factory=list)
    dimension_summary: dict[str, DimensionSeverityCounts] = Field(default_factory=dict)
    stages_skipped: list[str] = Field(default_factory=list)
    analysis_coverage: AnalysisCoverage = Field(default_factory=AnalysisCoverage)
    method_used: Literal["template", "llm_narrative"] = Field(
        default="template",
        description="L1=template, L2=llm_narrative",
    )
    narrative: str | None = Field(
        default=None,
        description="LLM-generated cross-dimension risk narrative (L2 only)",
    )


# ---------------------------------------------------------------------------
# CAB Window Summary (cross-CR)
# ---------------------------------------------------------------------------


class DispositionBreakdown(BaseModel):
    """Approve / conditional / reject counts for a CAB window."""

    approve: int = 0
    conditional: int = 0
    reject: int = 0


class CrossCrConflict(BaseModel):
    """A cross-CR conflict surfaced in the CAB summary."""

    type: str
    cr_pair: list[str]
    description: str


class AggregateSlaImpact(BaseModel):
    """Per-tier aggregate SLA impact across the CAB window."""

    tier: int
    total_union_downtime_min: float
    budget_min: float
    remaining_min: float
    breach: bool


class ProcessingInfo(BaseModel):
    """Pipeline processing stats for the batch."""

    wall_clock_seconds: float = 0
    cost_usd: float = 0


class CabSummary(BaseModel):
    """CAB window summary across all CRs in the batch."""

    window_id: str
    total_crs: int
    disposition_breakdown: DispositionBreakdown = Field(
        default_factory=DispositionBreakdown,
    )
    cross_cr_conflicts: list[CrossCrConflict] = Field(default_factory=list)
    aggregate_sla_impact: list[AggregateSlaImpact] = Field(default_factory=list)
    processing: ProcessingInfo = Field(default_factory=ProcessingInfo)
