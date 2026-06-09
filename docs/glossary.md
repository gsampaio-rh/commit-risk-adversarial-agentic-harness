# Glossary

## ITSM / Domain Terms

| Term | What it means |
|------|---------------|
| **ITSM** | IT Service Management — frameworks and processes for managing IT services (ITIL, COBIT, etc.) |
| **ITIL** | Information Technology Infrastructure Library — the dominant ITSM framework. Defines change types, risk categories, and approval workflows. |
| **CR** | Change Request — a formal request to modify an IT system (deploy code, rotate certs, migrate a database, etc.) |
| **CAB** | Change Advisory Board — a weekly committee that reviews pending CRs and decides: approve, approve with conditions, or reject. Typically 20-50 CRs per meeting, ~2 min per CR. |
| **CAB Window** | The set of CRs being reviewed in a single CAB meeting, usually grouped by ISO week. |
| **CMDB** | Configuration Management Database — a registry of IT assets (servers, services, databases) and their relationships (dependencies, tiers). |
| **SLA** | Service Level Agreement — contractual uptime guarantees per service tier (e.g., "tier-1 services: max 30 min downtime/month"). |
| **Runbook** | Step-by-step operational procedure for executing a change (Markdown). Lists commands, service references, verification steps. |
| **Rollback Plan** | Procedure to revert a change if it fails. Includes assumptions, duration estimate, and data-loss risk assessment. |
| **Tier** | Service criticality level. Tier 1-2 = customer-facing (payment gateway, auth service). Tier 3-4 = internal (monitoring, logging). |
| **P1/P2/P3/P4** | Incident severity levels. P1 = critical outage. P2 = major degradation. P3 = minor. P4 = cosmetic. |

## Pipeline Concepts

| Term | What it means |
|------|---------------|
| **CRBundle** | The complete input package for one change request. Contains the ITSM record (required) + up to 8 optional artifacts (runbook, rollback plan, CMDB snapshot, SLA definitions, maintenance schedule, communication plan, incident history, PR scope flags). Defined in `src/cr_analyzer/models/bundle.py`. |
| **ItsmRecord** | The core ticket data inside a CRBundle: change ID, type, risk category, title, description, affected services, requestor, approvers, scheduled window. This is the only required field in a CRBundle. |
| **Finding** | A single issue detected by a pipeline stage. Has a dimension (what kind of problem), severity (blocker/warning/info), description, evidence (what artifact and field triggered it), and remediation suggestion. |
| **Finding Dimension** | The category of a finding: completeness, runbook validity, rollback feasibility, scheduling conflicts, communication gaps, dependency chain, SLA impact, or historical pattern. Each maps to a pipeline stage. |
| **Severity** | How serious a finding is: **blocker** (must fix before deploying), **warning** (should address), **info** (noted but not blocking). |
| **Recommendation** | The CAB verdict for a CR: **approve** (proceed), **conditional** (fix warnings first), **reject** (has blockers). Determined by rules R1-R4. |
| **Stages Skipped** | Stages that couldn't run because their input artifact was absent (e.g., no runbook = Runbook Validation skipped). Recorded in the CAB report. |
| **NormalizeOutput** | The vendor-neutral, canonical representation of a CR after stage 2. All downstream stages consume this, never the raw input. Includes derived fields like `is_customer_facing` and `affected_tier`. |
| **CabReport** | Per-CR output: risk level, recommendation, all findings, stages skipped, analysis coverage. JSON + Markdown versions. |
| **CabSummary** | Per-window output: how many CRs approved/conditional/rejected, cross-CR conflicts, processing time. JSON + Markdown versions. |
| **Disk Checkpoint** | Each stage writes its output as JSON to disk before the next stage starts. Enables debugging and resume-from-stage. |

## Evolution Levels

| Term | What it means |
|------|---------------|
| **L1** | Script-only implementation. Deterministic, $0 cost, 100% reproducible. Uses rules, regex, interval math, exact string matching. |
| **L2** | Adds ML/LLM capability on top of L1. For Historical Pattern: sentence-transformer embedding for semantic similarity. For Risk Synthesis: LLM-generated cross-dimension narrative. |
| **Dual-path** | Runs both L1 and L2, merges results, deduplicates. Preserves L1 exact matches while adding L2 semantic coverage. |
| **Selective Routing** | L2 Risk Synthesis only calls the LLM for conditional/reject CRs. Approve CRs use L1 template. Saves cost. |
| **Graceful Fallback** | If an L2 dependency is unavailable (sentence-transformers not installed, LLM API down), the stage falls back to L1 without crashing. |

## Data Terms

| Term | What it means |
|------|---------------|
| **BPI 2014** | BPI Challenge 2014 dataset — 18K real ITIL change records from Rabobank (Dutch bank), exported from HP Service Manager. Free public dataset. Our primary validation data. |
| **Adapter** | Code that converts a specific data format into `CRBundle` objects. Adapters sit outside the pipeline (Ports & Adapters pattern). Currently only `bpi2014.py` exists. A new data source = a new adapter; the pipeline stays unchanged. |
| **Adapter Layer** | The architectural layer between raw data sources and the pipeline. Each adapter converts one vendor format (CSV, API, export) into `CRBundle`. The pipeline never sees raw data — only `CRBundle` objects. |
| **CAB-approval needed** | A field in BPI 2014 indicating whether a change requires CAB review. We filter on this to get the 373 CRs that went through CAB. |
| **Opaque IDs** | BPI 2014 `change_category` values are Change IDs like "C00012345", not descriptive text. This means L2 embedding similarity can't extract semantic meaning from them. |
| **GT (Ground Truth)** | Known correct answers used to measure pipeline accuracy. In BPI 2014: 56 (CI, ChangeType) tuples with >= 2 incidents serve as natural GT for Historical Pattern. |

## Rules

| Rule | Condition | Result |
|------|-----------|--------|
| **R1** | Any finding with severity = blocker | Reject |
| **R2** | >= 2 warnings across >= 2 different dimensions | Conditional |
| **R3** | 1 warning OR only info findings | Approve |
| **R4** | No findings | Approve (clean) |
