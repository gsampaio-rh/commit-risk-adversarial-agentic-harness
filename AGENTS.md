# Agents Guide — Bug Attribution Agent

This project builds a bug attribution system: given a JIRA bug report (title + description), identify the commit that introduced the bug in a git repository, producing evidence-grounded attribution reports. It is developed by Cursor agents using an adversarial harness workflow.

## Reading Order

1. **This file** — entry point, key invariants, current code layout, task policy
2. [docs/system-specification.md](docs/system-specification.md) — V4 target architecture (three pipelines, agent framework, data structures, LLM boundary)
3. [docs/agent-loop.md](docs/agent-loop.md) — agent loop (stages 2-3-4), completion criteria, tracing
4. [docs/evaluation-framework.md](docs/evaluation-framework.md) — metrics, stage-to-metric mapping, baselines, thresholds
5. [docs/glossary.md](docs/glossary.md) — all project-specific terms and definitions
6. [docs/datasets.md](docs/datasets.md) — ApacheJIT data, ground truth chain, bug→commit mappings

## Key Invariants

**Temporal bound enforcement:** The temporal bound (COMMIT_B~1) constrains the entire system — both the input pipeline (retrieval) and agent tools. In eval mode, bound comes from `fix_hash`. Full rules in [docs/system-specification.md](docs/system-specification.md#temporal-model).

**LLM reasons, scripts retrieve:** Scripts own retrieval (candidate set assembly) and evidence verification. The LLM owns planning, examination reasoning, and attribution. The LLM never searches from scratch — it receives a curated CandidateSet. (V3 code still has LLM-driven search; V4 target moves search to scripts.)

**Agent is governed:** The investigation harness governs the LLM. The LLM does not self-govern. The harness manages state, transitions, and completion criteria. Budget is a hard stop, not the primary exit signal.

**Observable by design:** Every investigation produces a structured InvestigationTrace. No investigation is a black box. Traces are the substrate for skill emergence.

**Oracle isolation:** Ground truth data (buggy commit labels, fix commits, chain linkage, eval-only metadata) must NEVER enter investigation context. The agent sees the JIRA report and repository information only.

**Attribution evaluation:** Hit@k, MRR, D3 attribution quality, D6 evidence grounding, and retrieval recall. Rubrics and thresholds in [docs/evaluation-framework.md](docs/evaluation-framework.md).

## Architecture Status

The **target architecture (V4)** is documented in [docs/system-specification.md](docs/system-specification.md). It introduces three pipelines (Input / Agent / Evaluation), an investigation harness, InvestigationBrief-driven completion, and structured traces.

The **current implementation (V3)** uses a single-agent loop where the LLM performs both search and reasoning within a budget-limited multi-turn conversation. V3 achieved Hit@5=0.50, MRR=0.304.

See [.harness/docs/topology-debate.md](.harness/docs/topology-debate.md) for the ADR that defines V4, and [.harness/docs/exp19b-retrospective.md](.harness/docs/exp19b-retrospective.md) for V2/V3 lessons learned.

## Workflow

This project uses the **adversarial harness** workflow: `/planner` sets direction, builder implements, `/evaluator` tests adversarially. See `.cursor/skills/adversarial-harness/SKILL.md` for the full protocol.

State files:
- [.harness/state.json](.harness/state.json) — vision, tasks, baselines, trade-offs
- `.harness/contract.json` — active build contract (when building)
- `.harness/breadcrumbs.jsonl` — append-only session trace

## Task Policy

Pending tasks live in `.harness/state.json` under `tasks[]`. Pick the task with the **lowest `priority` number** among those with status `pending`, `building`, or `evaluating`. Lower number = higher priority.

Before starting a new task: check if a contract exists, read the last 5 breadcrumbs, and verify no uncommitted work from a prior session.

## Package Layout (Current Code)

```
src/commit_investigator/
├── extraction/        # problem_extractor, jira_client
├── agent/             # orchestrator, tools, investigators, evidence_tagger
├── eval/              # eval_metrics, d3_judge, baselines, run_eval, ground_truth
└── infra/             # llm, git_context, smart_diff (shared)
```

This is the V3 layout. V4 will add packages for retrieval, harness governance, and trace storage — but those do not exist yet. Only describe what's on disk.

## Pipeline (Current V3 Implementation)

```
ProblemStatement (input) → Attribution Agent (multi-turn, tool-use) → Evidence Scorer → BugAttributionReport
```

| Stage | Module | Owner |
|-------|--------|-------|
| Eval setup | `eval/run_eval.py`, `extraction/problem_extractor.py` | Harness |
| Commit search & attribution | `agent/orchestrator.py` | LLM + tools |
| Evidence scoring | `agent/evidence_tagger.py` (inside `investigate()`) | Script |
| Evaluation | `eval/eval_metrics.py` | Oracle |

## Pipeline (V4 Target)

See [docs/system-specification.md](docs/system-specification.md) for the full target:

```
Input Pipeline: Extraction → Retrieval → CandidateSet
Agent Pipeline: Planning → Examination → Attribution → BugAttributionReport
Evaluation Pipeline: Scoring (Hit@k, MRR, D3, D6)
```

The agent receives `CandidateSet` + `ProblemStatement`, not raw repo access. The investigation harness governs the LLM through Stages 2-3-4 with an InvestigationBrief defining completion criteria.
