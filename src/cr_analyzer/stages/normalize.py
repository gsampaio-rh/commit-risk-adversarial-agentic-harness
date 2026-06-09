"""Stage 2: Normalize — vendor-neutral schema alignment and derived fields."""

from __future__ import annotations

from cr_analyzer.models.enums import ChangeType, RiskCategory
from cr_analyzer.models.outputs import IngestOutput, NormalizeOutput


def _derive_customer_facing(ingest: IngestOutput) -> bool:
    """Tier 1-2 in CMDB = customer-facing. No CMDB = False."""
    if ingest.cmdb is None:
        return False
    affected = set(ingest.itsm_record.get("affected_services", []))
    for node in ingest.cmdb.nodes:
        if node.id in affected and node.tier <= 2:
            return True
    return False


def _derive_affected_tier(ingest: IngestOutput) -> int:
    """Min tier across affected services in CMDB. No CMDB = 4."""
    if ingest.cmdb is None:
        return 4
    affected = set(ingest.itsm_record.get("affected_services", []))
    tiers = [node.tier for node in ingest.cmdb.nodes if node.id in affected]
    return min(tiers) if tiers else 4


def run_normalize(ingest: IngestOutput) -> NormalizeOutput:
    """Normalize an IngestOutput into a vendor-neutral NormalizeOutput."""
    rec = ingest.itsm_record

    return NormalizeOutput(
        change_id=ingest.change_id,
        change_type=ChangeType(rec["type"]),
        risk_category=RiskCategory(rec["risk_category"]),
        title=rec.get("title", ""),
        description=rec.get("description", ""),
        affected_services=rec.get("affected_services", []),
        requestor=rec.get("requestor", ""),
        approvers=rec.get("approvers", []),
        scheduled_window=rec["scheduled_window"],
        expected_duration_min=rec.get("expected_duration_min"),
        is_customer_facing=_derive_customer_facing(ingest),
        affected_tier=_derive_affected_tier(ingest),
        runbook=ingest.runbook,
        rollback=ingest.rollback,
        cmdb=ingest.cmdb,
        sla=ingest.sla,
        schedule=ingest.schedule,
        comms=ingest.comms,
        incidents=ingest.incidents,
        pr_flags=ingest.pr_flags,
    )
