# CAB Window 01 — Full-Bundle Synthetic Integration Fixture

**Secondary to BPI Challenge 2014** (primary real dataset). This is a **full-bundle synthetic** regression set: every CR includes runbook, rollback, CMDB, SLA, and related artifacts so stages 4–7 can be tested with injected failures. Real BPI 2014 records are typically partial bundles (often `itsm_record` only).

3 change requests in a single weekly CAB window (Saturday 2026-06-07, 02:00-05:30 UTC).

## Service Graph

Shared CMDB with 6 nodes: payment-api (T1), payment-db (T1), payment-db-v3 (T1, **deprecated**), order-service (T2), notification-queue (T3), auth-gateway (T2).

## Injection Map

| CR | ID | Injections | Expected Dimensions Hit | Expected Disposition |
|----|-----|-----------|------------------------|---------------------|
| cr-001 | CR-2026-0451 | None (clean) | — | **approve** (R4: no findings) |
| cr-002 | CR-2026-0452 | #1 stale runbook ref (payment-db-v3), #2 infeasible rollback (schema migration + restore backup) | runbook_validity (blocker), rollback_feasibility (blocker) | **reject** (R1: blocker present) |
| cr-003 | CR-2026-0453 | #3 scheduling overlap with cr-001 on order-service, missing comms for customer-facing auth-gateway | scheduling_conflicts (warning), communication_gaps (warning) | **conditional** (R2: ≥2 warnings across ≥2 dimensions) |

## Cross-CR Interactions

- **Scheduling overlap:** cr-001 (02:00-04:00) and cr-003 (02:30-04:30) overlap 90 minutes on `order-service`
- **SLA impact:** Tier-2 union downtime from cr-001+cr-003 overlap = ~120 min of the 120 min budget (85 remaining) — no breach but tight
- **Dependencies:** order-service depends-on payment-api (cr-002); if cr-002 deploys during cr-001's window, payment-api availability could affect order-service

## File Structure

Each `cr-NNN/` contains 9 files (full bundle for schema regression; only `itsm_record` is required by `cr-bundle.schema.json`):
- `itsm_record.json` — Change request metadata
- `runbook.md` — Deployment procedure
- `rollback_plan.md` — Revert procedure
- `cmdb_snapshot.json` — Service graph (identical across CRs)
- `sla_definitions.json` — Monthly downtime budgets (identical across CRs)
- `maintenance_schedule.json` — All CRs in window (identical across CRs)
- `incident_history.json` — Past incidents for affected services
- `pr_scope_flags.json` — CI/CD scope metadata
- `communication_plan.md` — Customer/stakeholder notification (absent in cr-003 = injection)
