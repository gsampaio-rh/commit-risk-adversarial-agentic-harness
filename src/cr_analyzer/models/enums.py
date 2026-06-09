"""Pipeline enums — single source of truth for all categorical values."""

from enum import Enum


class ChangeType(str, Enum):
    STANDARD = "standard"
    NORMAL = "normal"
    EMERGENCY = "emergency"


class RiskCategory(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Severity(str, Enum):
    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"


class Recommendation(str, Enum):
    APPROVE = "approve"
    CONDITIONAL = "conditional"
    REJECT = "reject"


class SlaAnalysisMode(str, Enum):
    FULL = "full"
    OVERLAP_ONLY = "overlap_only"
    NOT_APPLICABLE = "not_applicable"


class FindingDimension(str, Enum):
    COMPLETENESS = "completeness"
    RUNBOOK_VALIDITY = "runbook_validity"
    ROLLBACK_FEASIBILITY = "rollback_feasibility"
    SCHEDULING_CONFLICTS = "scheduling_conflicts"
    COMMUNICATION_GAPS = "communication_gaps"
    DEPENDENCY_CHAIN = "dependency_chain"
    SLA_IMPACT = "sla_impact"
    HISTORICAL_PATTERN = "historical_pattern"
