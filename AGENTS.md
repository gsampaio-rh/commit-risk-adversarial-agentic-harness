# Agents Guide — Bug Attribution Agent

This project builds an agentic attribution harness that, given a JIRA bug report (title + description), autonomously searches a git repository to identify the commit that introduced the bug, producing evidence-grounded attribution reports. It is developed by Cursor agents using an adversarial harness workflow.

## Reading Order

1. **This file** — entry point, key invariants, task policy
2. [docs/architecture.md](docs/architecture.md) — system design, pipeline stages, trust boundary diagram
3. [docs/temporal-model.md](docs/temporal-model.md) — what the agent sees vs eval-only oracle (critical)
4. [docs/glossary.md](docs/glossary.md) — all project-specific terms and definitions
5. [docs/evaluation.md](docs/evaluation.md) — Hit@k, MRR, D3/D6 rubrics, thresholds, scoring methodology
6. [docs/datasets.md](docs/datasets.md) — ApacheJIT data, ground truth chain, bug→commit mappings
7. [docs/agent-loop.md](docs/agent-loop.md) — stage-by-stage attribution flow

## Key Invariants

**Temporal bound enforcement:** The agent may only access repository state up to `COMMIT_B~1` (the parent of the earliest fix commit). The JIRA ticket is input and is temporally valid. No commits at or after the fix window may enter investigation context. Full rules in [docs/temporal-model.md](docs/temporal-model.md).

**Oracle isolation:** Ground truth data (buggy commit labels, fix commits, chain linkage, eval-only metadata) must NEVER enter investigation context. The agent sees the JIRA report and commit-time repository information only. Enforced by temporal bounds, context allowlists, and dedicated tests.

**LLM reasons, scripts verify:** The LLM drives multi-turn search, hypothesis formation, and commit selection via tool use. Deterministic scripts verify evidence grounding, score attribution quality, and enforce report schema. Scripts do not own search strategy or final attribution decisions.

**Attribution evaluation:** Hit@k, MRR, attribution quality (D3), evidence grounding (D6), and retrieval recall. Rubrics and gate thresholds in [docs/evaluation.md](docs/evaluation.md).

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
├── analysis/          # evidence_tagger, report schema
├── context/           # git_context, smart_diff, problem_extractor (new)
├── hypothesis/        # (will contain attribution prompts)
├── infra/             # ground_truth, jira_client, llm providers
├── pipeline/          # orchestrator (attribution agent), tools
└── runners/           # eval_harness, run_eval
```

V2 modules are preserved under `src/commit_investigator/_v2_archive/` and `tests/_v2_archive/` for reference. Do not import from `_v2_archive/` in V3 code paths.

## Pipeline

```
ProblemStatement (input) → Attribution Agent (multi-turn, tool-use) → Evidence Scorer → BugAttributionReport
```

| Stage | Module | Owner |
|-------|--------|-------|
| Commit search & attribution | `pipeline/orchestrator.py` | LLM + tools |
| Evidence verification | `analysis/evidence_tagger.py` | Script |
| Report assembly | `analysis/` (report schema) | Script |

`ProblemExtractor` (`context/problem_extractor.py`) is eval infrastructure — it builds a `ProblemStatement` from a JIRA ticket to connect the problem description with ground truth. It is not part of the agent's runtime pipeline.
