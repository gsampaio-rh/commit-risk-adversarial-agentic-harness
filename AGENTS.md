# Agents Guide — Bug Attribution Agent

This project builds a bug attribution system: given a JIRA bug report (title + description), identify the commit that introduced the bug in a git repository, producing evidence-grounded attribution reports. It is developed by Cursor agents using an adversarial harness workflow.

## Reading Order

1. **This file** — entry point, key invariants, current code layout, task policy
2. [docs/system-specification.md](docs/system-specification.md) — V4.2 architecture, data structures, LLM boundary
3. [docs/agent-loop.md](docs/agent-loop.md) — V4.2 agent loop (phases 1b-2-2b)
4. [docs/evaluation-framework.md](docs/evaluation-framework.md) — metrics, stage-to-metric mapping, baselines, thresholds
5. [docs/glossary.md](docs/glossary.md) — all project-specific terms and definitions
6. [docs/datasets.md](docs/datasets.md) — ApacheJIT data, ground truth chain, bug→commit mappings

## Key Invariants

**Temporal bound enforcement:** The temporal bound (COMMIT_B~1) constrains the entire system — both the input pipeline (retrieval) and agent tools. In eval mode, bound comes from `fix_hash`. Full rules in [docs/system-specification.md](docs/system-specification.md#temporal-model).

**LLM reasons, scripts retrieve:** Scripts own retrieval (candidate set assembly) and evidence verification. The LLM owns examination reasoning and attribution. The LLM never searches from scratch — it receives a curated CandidateSet and uses scoped tools restricted to that set.

**Scoped tools:** V4.1 examination tools validate SHAs against the CandidateSet before execution. The LLM can diff, blame, and read files — but only for pre-retrieved candidates. Search tools are not registered.

**Observable by design:** Every investigation produces a structured InvestigationTrace. No investigation is a black box. Traces are the substrate for skill emergence.

**Oracle isolation:** Ground truth data (buggy commit labels, fix commits, chain linkage, eval-only metadata) must NEVER enter investigation context. The agent sees the JIRA report and repository information only.

**Attribution evaluation:** Hit@k, MRR, D3 attribution quality, D6 evidence grounding, and retrieval recall. Rubrics and thresholds in [docs/evaluation-framework.md](docs/evaluation-framework.md).

## Architecture Status

The **target architecture is V4.2** (Revised Hierarchical Pipeline): separates narrowing from deep investigation via a 4-phase pipeline — script pre-score → deterministic triage → scoped investigation → conditional watchlist expansion. The **current implementation is V4.1** (Scoped Tools), which passes 20 candidates into a single loop.

V4.2 was decided via research-grounded builder/evaluator debates. Key change: Phase 1a (script pre-score) narrows 100→15, Phase 1b (deterministic triage) narrows 15→7 (3 must-examine + 4 watchlist) using pre-score rank (zero LLM), Phase 2 investigates deeply with scoped tools.

V3 (fully agentic) achieved Hit@5=0.50, MRR=0.304. V4 metadata-only achieved Hit@5=0.062. V4.2 targets Hit@5 ≥ 0.40.

See [.harness/docs/v42-architecture-adr.md](.harness/docs/v42-architecture-adr.md) for V4.2 decision, [.harness/docs/architecture-constraints.md](.harness/docs/architecture-constraints.md) for NFRs, [.harness/docs/scoped-tools-adr.md](.harness/docs/scoped-tools-adr.md) for V4→V4.1 pivot.

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
├── retrieval/         # retriever, config, pipeline (input pipeline)
├── models/            # candidates (CandidateSet, CandidateCommit)
├── agent/             # tools (build_scoped_tools, ToolRegistry)
├── harness/           # scoped_runner, scoped_prompts, result, trace_writer
├── eval/              # ground_truth, coverage
└── infra/             # llm, git_context, smart_diff (shared)
```

## Pipeline (V4.2 — Target)

```
Input: Extraction → Retrieval → CandidateSet@100
Phase 1a: Script pre-score → ScoredShortlist@15 (zero LLM)
Phase 1b: Deterministic triage → 3 must-examine + 4 watchlist (zero LLM)
Phase 2: Scoped investigation (multi-turn ReAct) → Ranked Suspects
Phase 2b: Watchlist expansion (conditional) → Merged final
Evaluation: 5-stage funnel (Recall@100→@15→@7→Exam→Hit@5)
```

| Phase | Module | Owner |
|-------|--------|-------|
| Extraction | `extraction/problem_extractor.py` | Script |
| Retrieval + pre-score | `retrieval/pipeline.py` → `retrieval/retriever.py` | Script |
| Deterministic triage | TBD (V4.2 implementation) | Script |
| Scoped investigation | `harness/scoped_runner.py` + `agent/tools.py` | LLM + scoped tools |
| Watchlist expansion | TBD (V4.2 implementation) | Harness + LLM |
| Evaluation | TBD (V4.2 implementation) | Oracle |

The agent receives `TriageResult` + `ProblemStatement`, not raw repo access. Tools are scoped to CandidateSet SHAs.

## Results Directory (gitignored)

```
results/
├── traces/              # Per-investigation JSON traces (written by TraceWriter)
├── retrieval-spike/     # Retrieval strategy analysis data
└── v4-checkpoints/      # Eval checkpoint results (retrieval recall, scoped eval)
```

All `results/` content is local, gitignored, and reproducible from scripts. New eval tasks should write under `results/`.

## Baselines

V3 (fully agentic, full repo tools): Hit@5=0.50, MRR=0.304. Code deleted during cleanup; results preserved in [exp19b retrospective](.harness/docs/exp19b-retrospective.md).
