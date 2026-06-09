"""Input models — CR bundle and its constituent artifacts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .enums import ChangeType, RiskCategory


class ScheduledWindow(BaseModel):
    """Maintenance window start/end times."""

    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime


class ItsmRecord(BaseModel):
    """Core ITSM change request record."""

    model_config = ConfigDict(populate_by_name=True)

    change_id: str = Field(description="Unique change request identifier")
    type: ChangeType = Field(description="Change type: standard, normal, or emergency")
    risk_category: RiskCategory = Field(description="Risk assessment: low, medium, or high")
    title: str = Field(description="Change request title")
    description: str = Field(description="Change request description")
    affected_services: list[str] = Field(description="Services impacted by this change")
    requestor: str = Field(description="Person requesting the change")
    approvers: list[str] = Field(description="Required approvers")
    scheduled_window: ScheduledWindow = Field(description="Planned maintenance window")
    expected_duration_min: int | None = Field(
        default=None, description="Expected duration in minutes"
    )


class CmdbNode(BaseModel):
    """CMDB configuration item node."""

    id: str
    name: str
    type: str
    status: str
    version: str
    tier: int


class CmdbEdge(BaseModel):
    """CMDB relationship edge."""

    source: str
    target: str
    relation: str


class CmdbSnapshot(BaseModel):
    """CMDB service graph snapshot."""

    nodes: list[CmdbNode] = []
    edges: list[CmdbEdge] = []


class SlaDefinition(BaseModel):
    """Per-tier SLA downtime budget."""

    tier: int
    monthly_downtime_budget_min: int
    consumed_this_month_min: int
    measurement_window: str = Field(description="YYYY-MM format")


class ScheduleEntry(BaseModel):
    """A single CR's schedule within a CAB window."""

    change_id: str
    scheduled_start: datetime
    scheduled_end: datetime
    affected_services: list[str]
    expected_duration_min: int


class Incident(BaseModel):
    """Past incident record linked to a service + change category."""

    incident_id: str
    service: str
    change_category: str
    severity: str = Field(description="P1-P4 severity level")
    root_cause_summary: str
    date: str = Field(description="ISO date string YYYY-MM-DD")


class PrScopeFlags(BaseModel):
    """CI/CD scope signals — booleans only, no diff analysis."""

    model_config = ConfigDict(extra="allow")

    schema_migration: bool = False
    customer_facing_api: bool = False
    data_backfill: bool = False
    test_coverage_delta: float = 0.0


class CRBundle(BaseModel):
    """Complete CR bundle — only itsm_record is required.

    All other artifacts are nullable to support partial bundles
    (BPI 2014 has no runbooks, rollback plans, or CMDB edges).
    """

    itsm_record: ItsmRecord
    runbook: str | None = Field(
        default=None, description="Runbook Markdown content"
    )
    rollback_plan: str | None = Field(
        default=None, description="Rollback plan Markdown content"
    )
    cmdb_snapshot: CmdbSnapshot | None = Field(
        default=None, description="CMDB service graph snapshot"
    )
    sla_definitions: list[SlaDefinition] | None = Field(
        default=None, description="Per-tier SLA downtime budgets"
    )
    maintenance_schedule: list[ScheduleEntry] | None = Field(
        default=None, description="All CRs in the same CAB window"
    )
    communication_plan: str | None = Field(
        default=None, description="Communication plan Markdown content"
    )
    incident_history: list[Incident] | None = Field(
        default=None, description="Past incidents for service + change category"
    )
    pr_scope_flags: PrScopeFlags | None = Field(
        default=None, description="CI/CD scope signals"
    )
