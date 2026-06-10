# Experiment Context — Commit Risk Investigator

Experiment **#19b** in a long-running agent research program. This document frames the investigative-agent thesis, evaluation dimensions, cost governance, and oracle isolation rules.

## Investigative Agent vs Classifier

**Classifier-only baseline:** train XGBoost (or similar) on numeric commit features; emit a probability or binary label. Cheap at scale, but output is a score — no evidence, no localization, no actionable reasoning.

**Investigative agent (this experiment):** a bounded multi-turn loop gathers diff, message, file history, and author context; an LLM reasons over assembled evidence; output is a **`CommitInvestigationReport`** with cited evidence, localization claims, and recommendations.

ApacheJIT is the test bed not because we need another classifier, but because its replication package provides a **six-dimensional validation chain** (bug-inducing commit → fixing commit → JIRA issue) covering 100% of ~28K positives. That richness supports eval dimensions no classifier output can satisfy.

The deliverable is a **verifiable investigation harness** proving that harness engineering (context construction, routing, verification, cost governance) matters more than raw model quality.

## Five Evaluation Dimensions (D1–D5)

| ID | Name | Criterion (automated vs judged) |
|----|------|--------------------------------|
| **D1** | Prediction | Agent risk level vs buggy/clean label — deterministic |
| **D2** | Localization | Agent cited files vs fix-commit diff files (Jaccard) — deterministic |
| **D3** | Diagnosis | Agent reasoning vs JIRA issue description — LLM-as-judge + human audit sample |
| **D4** | Severity | Agent risk level vs JIRA priority (normalized mapping) — LLM-as-judge + audit |
| **D5** | Recommendations | Agent recommendations vs actual fix pattern — LLM-as-judge + audit |

D1 and D2 are fully automatable. D3–D5 use documented rubrics with a 20-commit human audit to catch judge drift.

## Harness Cost Governance

Cost discipline is a first-class harness concern, not an afterthought:

1. **XGBoost scores all commits for free** — full train/test splits get routing probabilities at zero LLM cost.
2. **LLM investigates only routed gray-zone samples** — commits with router probability between 0.3 and 0.7 (plus optional HIGH confirm).
3. **Budget tiers** gate eval runs:
   - **$10** — smoke (~50 LLM investigations)
   - **$50** — default eval (~300 investigations)
   - **$100** — deep run (~1000 investigations)

The orchestrator enforces per-commit token budgets, a hard 3-turn cap, and per-run dollar limits. Router-only baseline (no LLM) is always reported alongside agent results for comparison.

## Oracle Isolation

**Critical rule:** JIRA metadata, fixing-commit diffs, and issue keys are **reserved for eval only** — they must never appear in agent investigation context.

During investigation the agent sees what a reviewer would see at commit time: the suspect commit's diff, message, numeric features, limited file history, and author stats. After the agent run completes, the eval harness compares output against the oracle:

- buggy label (CSV)
- fix-commit files (ground truth graph)
- JIRA description, priority, resolution (API + cache)

This prevents oracle leakage that would trivialize the investigation task and invalidate claims about investigative capability.

## Validation Strategy

1. **feat-2** — load ground truth graph; emit coverage report before any eval claims
2. **feat-5** — standalone agent (no routing) demonstrable on single commits
3. **feat-6** — XGBoost router integrated; gray-zone routing live
4. **feat-7** — six-dimension eval harness with tiered automation
5. **feat-9** — first $50 eval run; document ≥3 provable and ≥3 non-provable claims

Negative results (agent ≈ router-only) are valid if documented honestly.

## Research Lineage

Prior experiment phases (ITSM batch pipeline, BPI validation) are archived under `.harness/archive/phases/`. This rewrite abandons the pipeline-adapter framing in favor of a purpose-built investigative agent aligned with ApacheJIT's ground truth richness.

## Related Documents

- [Architecture](../ARCHITECTURE.md) — component design and V1 scope
- [Datasets](datasets.md) — ApacheJIT splits and ground truth chain
- [Evaluation](evaluation.md) — dimension rubrics and results (pending feat-9)
