# Experiment Context

This repo is **experiment #19b** in a research program on long-running AI agents for enterprise operations.

## Thesis

**Harness engineering** — designing the full execution system around a model, not just the prompt or context — is required for reliable multi-hour autonomous work. The harness is a control plane for cognition: it manages which context is loaded, what "done" means, what checks must pass, what persists across sessions, when to reset before drift becomes rot, and when to escalate to humans.

The industry progression: prompt engineering (2022-2024) → context engineering (2025) → harness engineering (2026). Each era subsumes the previous. The unit of design moved from "one instruction" to "the whole factory."

## Why This Use Case

ITSM change request analysis scored **23/25** on agent-fit dimensions — among the strongest enterprise candidates for long-running agent validation.

| Dimension | Score | Justification |
|-----------|-------|---------------|
| Heterogeneous reasoning | 5/5 | 8 artifact types (ticket, runbook, rollback, CMDB, SLA, schedule, comms, incidents) requiring cross-source reasoning |
| Open-ended analysis | 5/5 | Each CR is a unique bundle; "is this change safe?" requires reasoning about the specific combination |
| Explanation needs | 5/5 | "Reject because rollback assumes pre-migration state" IS the deliverable — CAB needs the reasoning chain |
| Novelty | 4/5 | Change types recur but each artifact combination is unique; cross-CR interactions per window add novelty |
| Context evolution | 4/5 | CMDB topology, SLA definitions, team practices, and runbook quality evolve continuously |

**Overnight value:** analyze ALL pending CRs before CAB meets. Transform human-quality judgment from a sampled, daytime activity into an exhaustive, continuous one.

## Harness Instantiation

This experiment instantiates harness engineering as a concrete 9-stage pipeline:

| Harness Component | Implementation in This Repo |
|-------------------|-----------------------------|
| Context construction | Adapter layer: vendor-specific data → `CRBundle` (pipeline's input port) |
| Memory | Disk checkpoints per stage (JSON); `.harness/breadcrumbs.jsonl` trace |
| Skill routing | Evolution levels per stage: script → encoder → LLM, cheapest first |
| Verification | 147 pytest tests; adversarial evaluator (`.harness/` workflow); Pydantic schema validation |
| Governance | Budget gates ($2/batch ceiling); P0/P1/P2 claim separation |
| Tool orchestration | Sequential runner with skip logic for missing artifacts |

**Minimum capable component:** 7 of 9 stages have script or encoder as SOTA. LLM enters only where prose reasoning is unavoidable (runbook validation L3, risk synthesis L2 narrative).

## Validation Phases

| Phase | What It Proves | Status |
|-------|---------------|--------|
| **P0 — Architecture** | Pipeline processes real data correctly, stages produce valid output | **Done** — BPI 2014 (373 CRs, 100% completion, 100% schema compliance) |
| **P1 — Predictive** | Risk scores predict actual failures at statistical scale | **In progress** — ApacheJIT (28K positives, temporal splits) |
| **P2 — Full pipeline** | All 9 stages on real CRs with prose artifacts; expert CAB alignment | **Blocked** — requires enterprise partner |

P0 metrics are architectural validation, not production performance claims. P1 accepts the semantic gap between bug-inducing commits and ITSM incident-causing changes. P2 is required before any commercial claims.

For P0 success gates, injected failure taxonomy, and aspirational evolution targets, see [archived aspirational content](../.harness/archive/docs/ARCHITECTURE-aspirational.md).

---

**Related:** [Architecture](../ARCHITECTURE.md) | [Pipeline Flow](pipeline-flow.md) | [Datasets](datasets.md) | [Evaluation](evaluation.md)
