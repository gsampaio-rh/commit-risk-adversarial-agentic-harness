# Agents Guide — Commit Risk Investigator

This project builds an agentic investigation harness that analyzes Apache open-source commits for defect risk, producing evidence-grounded reports evaluated across six dimensions. It is developed by Cursor agents using an adversarial harness workflow.

## Reading Order

1. **This file** — entry point, key invariants, task policy
2. [docs/architecture.md](docs/architecture.md) — system design, pipeline stages, trust boundary diagram
3. [docs/temporal-model.md](docs/temporal-model.md) — what the agent sees vs eval-only oracle (critical)
4. [docs/glossary.md](docs/glossary.md) — all project-specific terms and definitions
5. [docs/evaluation.md](docs/evaluation.md) — D1–D6 rubrics, thresholds, scoring methodology
6. [docs/datasets.md](docs/datasets.md) — ApacheJIT data, CSV features, ground truth chain
7. [docs/agent-loop.md](docs/agent-loop.md) — stage-by-stage investigation flow
8. [docs/harness.md](docs/harness.md) — harness vs agent-loop vs evaluation definitions

## Key Invariants

**Oracle isolation:** Ground truth data (buggy label, fix commits, JIRA metadata, chain linkage) must NEVER enter investigation context. The agent sees only commit-time information. Enforced by allowlist in `CommitContextBuilder` + 7 dedicated tests. Full rules in [docs/temporal-model.md](docs/temporal-model.md).

**Script-first pipeline:** Deterministic scripts own all classification decisions (archetype detection, evidence tagging, risk policy, quality gates, confidence scoring). The LLM generates hypotheses; scripts grade them. This architecture is frozen in V2.

**Six-dimension evaluation:** D1 (Prediction), D2 (Localization), D3 (Diagnosis), D4 (Severity), D5 (Recommendations), D6 (Evidence grounding). All must pass gate thresholds simultaneously. Rubrics in [docs/evaluation.md](docs/evaluation.md).

## Workflow

This project uses the **adversarial harness** workflow: `/planner` sets direction, builder implements, `/evaluator` tests adversarially. See `.cursor/skills/adversarial-harness/SKILL.md` for the full protocol.

State files:
- [.harness/state.json](.harness/state.json) — vision, tasks, baselines, trade-offs
- `.harness/contract.json` — active build contract (when building)
- `.harness/breadcrumbs.jsonl` — append-only session trace

## Task Policy

Pending tasks live in `.harness/state.json` under `tasks[]`. Pick the task with the **lowest `priority` number** among those with status `pending`, `building`, or `evaluating`. Lower number = higher priority.

Before starting a new task: check if a contract exists, read the last 5 breadcrumbs, and verify no uncommitted work from a prior session.

## Package Layout

```
src/commit_investigator/
├── analysis/       # risk_policy, evidence_tagger, confidence_model, archetype, quality_gate
├── context/        # context_builder, git_context, smart_diff
├── hypothesis/     # hypothesis_engine, historical_rag, prompts, response_parser
├── infra/          # ground_truth, jira_client, llm providers
├── pipeline/       # orchestrator, report_builder, tools
├── routing/        # XGBoost router
└── runners/        # run_eval, eval_harness, eval_judge
```
