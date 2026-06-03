# Spike: BPI Challenge 2014 Dataset Exploration

**Date:** 2026-06-03
**Source:** [4TU.ResearchData](https://data.4tu.nl/collections/BPI_Challenge_2014/5065469) — Rabobank Group ICT, HP Service Manager
**Files:** `data/bpi2014/Detail_Change.csv` (6.4 MB), `data/bpi2014/Detail_Incident.csv` (14 MB)

---

## Dataset Overview

| File | Rows | Unique IDs | Delimiter | Meaningful Columns |
|------|------|-----------|-----------|-------------------|
| Detail_Change.csv | 30,275 | 18,000 changes | `;` | 21 |
| Detail_Incident.csv | 46,809 | 46,607 incidents | `;` | 28 (50 trailing empty cols) |

**Key structural property:** Change CSV has multiple rows per change (one per affected CI). 77% of changes affect 1 CI, 11% affect 2, 12% affect 3+. Must GROUP BY Change ID and aggregate CIs into `affected_services[]`.

**Temporal range:** Changes opened 2011-09 to 2014-03. Planned Start spans 2011-06 to 2021-02 (outliers).

---

## Change Data — Field Mapping

| BPI 2014 Column | Our Schema Field | Mapping | Coverage |
|-----------------|-----------------|---------|----------|
| Change ID | `change_id` | Direct | 100% |
| Risk Assessment | `risk_category` | Minor→low, Business→medium, Major Business→high | 100% (3 values) |
| Emergency Change | `type` (partial) | Y→emergency | 100% (0.3% emergency) |
| Change Type | `type` + category | 240 types: "Standard *"→standard, "Release *"→normal | 100% |
| CAB-approval needed | metadata | Y/N — filters CAB-relevant changes | 100% (2% = Y) |
| CI Name (aff) | `affected_services[]` | Group by Change ID, collect unique CIs | 100% |
| CI Type (aff) | CMDB node type | 13 types: computer, application, database, etc. | 100% |
| CI Subtype (aff) | CMDB node subtype | 74 subtypes | 100% |
| Service Component WBS | service group | 286 groups — organizational unit | 100% |
| Planned Start | `scheduled_window.start` | Parse "dd-mm-yyyy HH:MM" | 100% |
| Planned End | `scheduled_window.end` | Parse "dd-mm-yyyy HH:MM" | 99.9% |
| Scheduled Downtime Start/End | SLA downtime | **97% EMPTY** | 3% |
| Actual Start/End | actual execution | 90% coverage | 90% |
| # Related Incidents | incident count | Sparse but real | 7% |
| Originated from | metadata | Problem 64%, Incident 35% | 100% |

### Fields NOT Available

| Our Schema Field | Impact |
|-----------------|--------|
| `title`, `description` | Anonymized. Completeness flags as missing. |
| `requestor`, `approvers` | Not in CSV. Completeness flags. |
| `runbook` | Stage 4 skips |
| `rollback_plan` | Stage 5 skips |
| `communication_plan` | Stage 3 flags |
| `cmdb_snapshot.edges` | Stage 7 degrades to co-occurrence |
| `sla_definitions` | Stage 6 degrades (no SLA math) |

---

## CAB Windows

**374 unique changes required CAB approval** across 51 weeks.

| Week Size | Count |
|-----------|-------|
| 1-3 changes | 22 weeks |
| 4-10 changes | 11 weeks |
| 11-21 changes | 18 weeks |

**Derivation:** Filter `CAB-approval needed = Y`, group by ISO week of Planned Start → 51 windows, 1-21 changes each. Top weeks: 21, 20, 20, 18, 16 changes.

---

## Schedule Overlap Analysis

**25,529 overlapping change pairs** on shared CIs within the same week.

Real examples:
- Exact window overlap: two changes on same CI, identical start/end
- Partial overlap: 2-hour overlap on datacenter equipment
- Multi-CI overlap: changes sharing infra through different CI paths

Schedule overlap detection has abundant real data.

---

## Historical Pattern — Change→Incident Linkage

| Metric | Value |
|--------|-------|
| Incidents linked to changes | 560 (1.2% of all incidents) |
| Unique changes with incidents | 231 |
| High-severity (Impact 1-2) linked | 29 incidents → 25 changes |
| (CI, ChangeType) tuples with ≥2 incidents | **56** |
| Of those, with ≥1 high-severity | **12** |

**Top recurring patterns:**

| CI | Change Type | Incidents | Highest Impact |
|----|------------|-----------|---------------|
| SUB000479 | Standard Change Type 08 | 7 | Impact 2 |
| WBA000082 | Standard Change Type 06 | 6 | Impact 2 |
| SBA000088 | Standard Change Type 73 | 6 | Impact 2 |
| WBA000011 | Standard Activity Type 32 | 5 | 2x Impact 2 |
| COM000003 | Standard Activity Type 32 | 4 | Impact 2 |

The 56 recurring tuples are **natural ground truth** for Historical Pattern.

---

## Pseudo-CMDB from CI Data

Nodes buildable from:

| Field | Unique Values |
|-------|---------------|
| CI Name | 10,193 |
| CI Type | 13 (application, database, computer, etc.) |
| CI Subtype | 74 |
| Service Component WBS | 286 |

No edges in data. Co-occurrence (CIs in same change) = implicit relationship, but not architectural dependency.

---

## Stage Coverage Assessment

| Stage | Coverage | Quality | Notes |
|-------|----------|---------|-------|
| 1. Ingest | Full | Strong | CSV parsing, multi-CI grouping |
| 2. Normalize | Full | Strong | Risk mapping, type derivation, CI aggregation |
| 3. Completeness | Full | **Primary value** | Flags missing title, desc, runbook, rollback, comms |
| 4. Runbook | Skipped | N/A | No runbooks |
| 5. Rollback | Skipped | N/A | No rollback plans |
| 6. Schedule & SLA | Partial | Good overlap, no SLA | 25K+ overlaps; 97% missing downtime |
| 7. Dependency Chain | Degraded | Weak | Co-occurrence only |
| 8. Historical Pattern | Full | **Strong real GT** | 56 recurring patterns, 12 high-severity |
| 9. Risk Synthesis | Full | Good | Aggregates available + skipped |

---

## Open Questions Resolved

| Question | Answer |
|----------|--------|
| BPI 2014 structure? | 21 cols, `;` delimited, multi-CI per change, anonymized IDs |
| CAB window derivation? | `CAB-approval needed = Y`, group by ISO week → 51 windows, 1-21 changes |
| Historical Pattern GT? | 56 (CI, ChangeType) tuples with ≥2 incidents → natural GT |
| SLA viable? | No — 97% empty Scheduled Downtime. Degrade to overlap-only. |
| CMDB graph? | Nodes yes (10K CIs). Edges: co-occurrence only. |

## New Open Questions

| Question | Impact |
|----------|--------|
| Map 240 Change Types to standard/normal/emergency? | Pattern: "Standard *"→standard, "Release *"→normal, Emergency flag→emergency |
| CI Name vs Service Component WBS as service ID? | WBS groups into 286 components. Could use both levels. |
| Co-occurrence graph worth it for Stage 7? | 25K overlaps but co-occurrence ≠ dependency. May produce noise. |
