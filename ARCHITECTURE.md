# Architecture — Commit Risk Investigator

System design for an investigative commit-risk agent validated against ApacheJIT ground truth. This document describes planned components; implementation is staged across feat-2 through feat-7.

## Overview

The system has four layers:

1. **Data & oracle** — ApacheJIT CSVs, replication-package linkage files, JIRA metadata (eval-only)
2. **Context** — git diffs, messages, numeric features, file history, author stats
3. **Investigation** — bounded agent loop with tool dispatch and schema-validated output
4. **Evaluation** — five-dimension harness comparing agent output to oracle

See also: [docs/experiment-context.md](docs/experiment-context.md), [docs/datasets.md](docs/datasets.md), [docs/evaluation.md](docs/evaluation.md)

## Agent Loop and Orchestrator

V1 uses a **framework-agnostic `AgentOrchestrator`** with a hard cap of **max 3 turns**:

| Turn | Action |
|------|--------|
| 1 | Assemble context bundle (diff, message, CSV features, initial file scan) |
| 2–3 | Targeted follow-up only when confidence is below threshold or explicit uncertainty is flagged |

The orchestrator — not the LLM — owns turn limits, tool dispatch, budget tracking, checkpoint persistence, and report assembly. The LLM performs reasoning over assembled context inside each turn.

Follow-up triggers are deterministic: low confidence, missing localization evidence, or explicit uncertainty in the prior turn output.

## XGBoost Routing

An **XGBoost router** scores every commit on numeric features (LA, LD, NF, ND, NS, ENT, NDEV, AGE, etc.) at zero LLM cost:

| Probability | Route | LLM usage |
|-------------|-------|-----------|
| P < **0.3** | SAFE | Skip investigation |
| **0.3** ≤ P ≤ **0.7** | INVESTIGATE | Full agent loop |
| P > **0.7** | HIGH | Flag; optional light confirm |

Router is trained on the train split only. Agent eval focuses on the gray zone where investigation adds value.

## Ground Truth Graph

**`GroundTruthGraph`** indexes the ApacheJIT replication package linkage:

```
bug_hash → fix_hash → issue_key → JIRA metadata
```

- `commit_links_{PROJECT}.csv` — maps fixing commit to bug-inducing commit
- `{PROJECT}.csv` — maps commits to JIRA issue keys

The graph is first-class infrastructure for all five eval dimensions. JIRA API fetches (summary, description, priority, components) are cached on disk and used **only at eval time** — never during agent investigation.

Coverage reports (chain completeness %, broken links) gate eval claims (feat-2).

## CommitInvestigationReport Schema

Primary output is a **`CommitInvestigationReport`** (Pydantic, feat-4):

- `commit_id`, `project`
- `risk_assessment` — level (LOW | MEDIUM | HIGH | CRITICAL), confidence 0–1
- `evidence[]` — typed items citing diff hunks, file paths, or git history
- `findings[]`, `localization[]`, `recommendations[]`
- `reasoning_summary`, `tools_used[]`, `turn_count`, `metadata`

Schema validation rejects reports with zero evidence items. JSON schema export supports downstream eval and artifact storage.

## Git Context Layer

**`GitContextProvider`** (feat-3) reads local clones via git CLI:

- `get_diff(commit_id)`, `get_commit_message`, `get_touched_files`
- `get_file_history(path, n=3)` for targeted follow-up turns

**`CommitContextBuilder`** assembles the investigation bundle from git output, CSV row features, and precomputed author stats (train split, no leakage).

## V1 Scope and Deferred Work

**In V1:**

- Two Apache projects: **Camel** and **Hadoop** (largest + second-project generalization)
- Local full clones under `data/repos/` (~2–5 GB disk budget)
- Default eval budget tier: **$50** (~300 LLM investigations)

**Deferred:**

- Agent framework selection (LangGraph vs minimal custom loop vs CrewAI) — **deferred** to post-V1 spike
- All 15 project clones
- Line-level localization via GumTree mappings
- Live JIRA during investigation
- Production API or deployment

## Component Map (planned)

```
ApacheJIT CSVs ──► GroundTruthGraph ──► EvalHarness (D1–D5)
                         │
Local git clones ──► GitContextProvider ──► CommitContextBuilder
                                                    │
XGBoost router ──► AgentOrchestrator (≤3 turns) ──► CommitInvestigationReport
```

## Harness Integration

This repo uses an adversarial verification harness (`.harness/`): contract-first acceptance criteria, evaluator negotiation, and breadcrumb tracing. Each feature task ships against a binary contract before eval runs.

## Related Documents

- [Experiment context](docs/experiment-context.md) — thesis, dimensions, cost governance
- [Datasets](docs/datasets.md) — ApacheJIT ground truth chain
- [Evaluation](docs/evaluation.md) — D1–D5 framework and results (pending feat-9)
