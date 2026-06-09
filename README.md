# ITSM Change Request Analyzer

> **Experiment #19b** in a [long-running agent research program](docs/experiment-context.md). This repo validates whether harness engineering — wrapping models in a control plane with checkpoints, stage gates, evolution levels, and adversarial verification — produces reliable multi-hour autonomous agents for enterprise operations. The pipeline below is the artifact under test; the methodology, agent scoring (23/25), and validation phases (P0/P1/P2) are documented in [experiment context](docs/experiment-context.md).

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
| [BPI Challenge 2014](https://data.4tu.nl/datasets/e9c00fe9-c87a-450e-8bd6-d5e06a6b309a) (Rabobank) | **Primary** | 18K real ITIL change records. See [docs/datasets.md](docs/datasets.md) |
| `fixtures/cab-window-01/` | **Regression** | 3 synthetic CRs for smoke testing and integration |

## Pipeline

| # | Stage | Status |
|---|-------|--------|
| 1 | Ingest | L1 implemented |
| 2 | Normalize | L1 implemented |
| 3 | Completeness Check | L1 implemented |
| 4 | Runbook Validation | Not implemented — skip recorded |
| 5 | Rollback Feasibility | Not implemented — skip recorded |
| 6 | Schedule & SLA | L1 implemented, **degrades** to overlap-only when SLA absent |
| 7 | Dependency Chain | Not implemented — skip recorded |
| 8 | Historical Pattern | L1 + L2 (embedding) implemented |
| 9 | Risk Synthesis | L1 + L2 (LLM narrative) implemented |

## Tech Stack

- **Python 3.12+**, Pydantic v2 (models are the source of truth for schemas)
- **pytest** for stage-level and E2E tests
- Sequential runner with disk checkpoints per stage

## Results (BPI 2014 Evaluation)

| Metric | Target | Result |
|--------|--------|--------|
| Task completion | 100% | 100% (373/373 CRs) |
| Schema compliance | >= 99% | 100% |
| Cost per 50-CR batch | < $2 | $0 (L1) |
| Wall-clock | < 30 min/window | 11.6s total (373 CRs, 50 windows) |
| Cross-run consistency | >= 95% | 100% (deterministic) |

Full evaluation details: [docs/evaluation.md](docs/evaluation.md)

## Directory Structure

```
├── README.md              # This file
├── ARCHITECTURE.md        # Pipeline design, stage details, design decisions
├── docs/
│   ├── experiment-context.md  # Research lineage, agent scoring, validation phases
│   ├── pipeline-flow.md       # End-to-end visual walkthrough of every stage
│   ├── glossary.md            # Glossary of ITSM, pipeline, and data terms
│   ├── datasets.md            # BPI 2014, ApacheJIT, fixtures, data strategy
│   └── evaluation.md          # Eval results, L1 vs L2, test suite
├── fixtures/
│   └── cab-window-01/
│       ├── cr-001/        # Full-bundle smoke test CR
│       ├── cr-002/        # Incomplete bundle
│       └── cr-003/        # Schedule overlap test
├── data/
│   └── bpi2014/           # BPI Challenge 2014 CSVs (gitignored)
├── src/cr_analyzer/       # Pipeline code
│   ├── models/            # Pydantic v2 models (source of truth)
│   ├── stages/            # Stage implementations
│   ├── pipeline/          # Runner + skip logic
│   ├── adapters/          # BPI 2014 CSV adapter
│   └── eval/              # Evaluation harness
└── tests/                 # 147 tests across 13 files
```

Design history, decisions, and aspirational content archived in `.harness/archive/docs/`.
