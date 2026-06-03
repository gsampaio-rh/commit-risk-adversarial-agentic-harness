# ITSM Change Request Analyzer

A 9-stage pipeline that pre-analyzes IT change requests for the Change Advisory Board (CAB). Cross-references ITSM records, runbooks, rollback plans, CMDB state, SLA definitions, scheduling data, and incident history to produce risk assessments with approve/conditional/reject recommendations.

## Problem

CABs review 20–50 changes per week. Members have 2 minutes per change. Nobody reads the runbook. Nobody cross-references the rollback plan against current system state. Nobody checks whether two changes in the same window conflict on shared infrastructure. Result: rubber-stamping, missed conflicts, stale artifacts, SLA surprises.

An overnight agent analyzes ALL pending CRs before the CAB meets — transforming it from rubber-stamp to risk-informed decision point.

## Why an Agent

A CR is a bundle of heterogeneous artifacts (ticket + runbook + rollback plan + CMDB state + schedule + SLA + incidents). Assessing "is this change safe?" requires cross-artifact reasoning. Scripts check individual fields ("has rollback plan? yes/no"). The agent assesses "is this rollback plan ACTUALLY FEASIBLE given current system state and the other changes in this window?"

**Agent Score:** 23/25 | **Eval Readiness:** ★★★½ | **Total:** 26/35

## Data Strategy

| Source | Role | What it covers |
|--------|------|----------------|
| [BPI Challenge 2014](https://data.4tu.nl/datasets/e9c00fe9-c87a-450e-8bd6-d5e06a6b309a) (Rabobank) | **Primary** | 18K real ITIL change records. Partial bundles (no runbooks/rollback). Validates Ingest, Normalize, Completeness, Schedule overlap, Historical Pattern. See `.harness/spike-bpi2014.md` |
| `fixtures/cab-window-01/` | **Regression** | 1 full-bundle synthetic CR for smoke testing prose-artifact stages |

## Pipeline (9 stages, all L1 script-only)

| # | Stage | Conditional? |
|---|-------|-------------|
| 1 | Ingest | always |
| 2 | Normalize | always |
| 3 | Completeness Check | always |
| 4 | Runbook Validation | **skips** when `runbook` is null |
| 5 | Rollback Feasibility | **skips** when `rollback_plan` is null |
| 6 | Schedule & SLA | **degrades** to overlap-only when `sla_definitions` absent |
| 7 | Dependency Chain | **skips** when `cmdb_snapshot` is null |
| 8 | Historical Pattern | always (uses incident linkage) |
| 9 | Risk Synthesis & CAB Report | always |

## Tech Stack

- **Python 3.12+**, Pydantic v2 (models are the source of truth for schemas)
- **pytest** for stage-level and E2E tests
- Sequential runner with disk checkpoints per stage

## Success Metrics

| Metric | Target |
|--------|--------|
| Per-dimension recall | >= 80% on injected failures |
| False positive rate | <= 15% on clean CRs |
| Cost per 50-CR batch | < $2 (L1 = ~$0) |
| Wall-clock per batch | < 30 min |
| Cross-run consistency | >= 95% (L1 is deterministic) |

## Directory Structure

```
├── README.md              # This file
├── ARCHITECTURE.md        # 9-stage design, evolution levels, evaluation
├── fixtures/
│   └── cab-window-01/
│       └── cr-001/        # Full-bundle smoke test CR
├── data/
│   └── bpi2014/           # BPI Challenge 2014 CSVs (primary dataset)
├── src/                   # Pipeline code (Pydantic models, stages, runner)
└── tests/                 # Stage-level and E2E tests
```

Design history, decisions, and skeptic reviews are archived in `.harness/archive/docs/`.
