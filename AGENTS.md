# Agents Guide — Bug Attribution Agent

This project builds an agentic attribution harness that, given a JIRA bug report (title + description), autonomously searches a git repository to identify the commit that introduced the bug, producing evidence-grounded attribution reports. It is developed by Cursor agents using an adversarial harness workflow.

## Reading Order

1. **This file** — entry point, key invariants, task policy
2. [docs/system-specification.md](docs/system-specification.md) — pipeline stages, LLM boundary, agentic loop (7 stages), tools, data structures, temporal model
3. [docs/evaluation-framework.md](docs/evaluation-framework.md) — system-level vs output-quality metrics, D3 rubric, baselines, thresholds
4. [docs/glossary.md](docs/glossary.md) — all project-specific terms and definitions
5. [docs/datasets.md](docs/datasets.md) — ApacheJIT data, ground truth chain, bug→commit mappings

## Key Invariants

**Temporal bound enforcement:** The agent may only access repository state up to `COMMIT_B~1` (the parent of the earliest fix commit). The JIRA ticket is input and is temporally valid. No commits at or after the fix window may enter investigation context. Full rules in [docs/system-specification.md](docs/system-specification.md#temporal-model).

**Oracle isolation:** Ground truth data (buggy commit labels, fix commits, chain linkage, eval-only metadata) must NEVER enter investigation context. The agent sees the JIRA report and commit-time repository information only. Enforced by temporal bounds, context allowlists, and dedicated tests.

**LLM reasons, scripts verify:** The LLM drives multi-turn search, hypothesis formation, and commit selection via tool use. Deterministic scripts verify evidence grounding, score attribution quality, and enforce report schema. Scripts do not own search strategy or final attribution decisions.

**Attribution evaluation:** Hit@k, MRR, attribution quality (D3), evidence grounding (D6), and retrieval recall. Rubrics and gate thresholds in [docs/evaluation-framework.md](docs/evaluation-framework.md).

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
| Eval setup | `runners/run_eval.py`, `context/problem_extractor.py` | Harness |
| Commit search & attribution | `pipeline/orchestrator.py` | LLM + tools |
| Evidence scoring | `analysis/evidence_tagger.py` (inside `investigate()`) | Script |
| Evaluation | `runners/eval_metrics.py` | Oracle |

Full three-stage pipeline specification in [docs/system-specification.md](docs/system-specification.md).
