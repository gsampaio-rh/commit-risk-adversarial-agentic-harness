# Design Context — Commit Risk Investigator

This document frames the investigative-agent thesis, evaluation dimensions, cost governance, and oracle isolation rules.

## Investigative Agent vs Classifier

**Classifier-only baseline:** train XGBoost on numeric commit features; emit a probability or binary label. Cheap at scale, but output is a score — no evidence, no localization, no actionable reasoning.

**Investigative agent:** a bounded multi-turn loop gathers diff, message, file history, and author context; an LLM reasons over assembled evidence; output is a **`CommitInvestigationReport`** with cited evidence, localization claims, and recommendations.

ApacheJIT is the test bed because its replication package provides a **six-dimensional validation chain** (bug-inducing commit → fixing commit → JIRA issue) covering 100% of ~28K positives. That richness supports eval dimensions no classifier output can satisfy.

The deliverable is a **verifiable investigation harness** proving that harness engineering (context construction, routing, verification, cost governance) matters more than raw model quality.

## Six Evaluation Dimensions (D1–D6)

| ID | Name | Criterion | Method |
|----|------|--------|--------|
| **D1** | Prediction | Agent risk level vs buggy/clean label | Deterministic |
| **D2** | Localization | Agent cited files vs fix-commit diff files (Jaccard) | Deterministic |
| **D3** | Diagnosis | Agent reasoning vs JIRA issue description | LLM-as-judge (rubric 0–4) |
| **D4** | Severity | Agent risk level vs JIRA priority (normalized) | Deterministic |
| **D5** | Recommendations | Agent recommendations vs actual fix pattern | LLM-as-judge (rubric 0–3) |
| **D6** | Evidence grounding | Agent claims vs actual diff/files | Automated (zero LLM cost) |

D1, D2, D4, D6 are fully deterministic. D3, D5 use documented rubrics with LLM-as-judge scoring. D6 catches agents that classify correctly but cite no real evidence. Full rubrics: [evaluation.md](evaluation.md).

## Harness Cost Governance

Cost discipline is a first-class harness concern:

1. **XGBoost scores all commits for free** — full train/test splits get routing probabilities at zero LLM cost.
2. **LLM investigates only routed gray-zone samples** — commits with router probability between 0.3 and 0.7.
3. **Budget tiers** gate eval runs: $10 smoke (~50), $50 standard (~300), $100 deep (~1000).

The orchestrator enforces per-commit token budgets, a hard 3-turn cap, and per-run dollar limits.

## Oracle Isolation

**Critical rule:** JIRA metadata, fixing-commit diffs, and issue keys are **reserved for eval only** — they must never appear in agent investigation context.

During investigation the agent sees what a reviewer would see at commit time: the suspect commit's diff, message, numeric features (allowlist), limited file history, and author stats. After the agent run completes, the eval harness compares output against the oracle:

- buggy label (CSV)
- fix-commit files (ground truth graph)
- JIRA description, priority, resolution (API + cache)

Oracle isolation is enforced by an allowlist in `CommitContextBuilder` and verified by 7 dedicated tests. The data leakage incident (buggy label in CSV features, D1 dropped from 0.86 to 0.40 after fix) proved this is not theoretical.

## Related

- [architecture.md](architecture.md) — component design and trust boundaries
- [datasets.md](datasets.md) — ApacheJIT splits and ground truth chain
- [evaluation.md](evaluation.md) — dimension rubrics, thresholds, and results
