# Bug Attribution Agent

> Given a JIRA bug report (title + description), identify the commit that introduced the bug in a git repository. Produces ranked suspect lists with evidence-grounded causal explanations. Evaluated against [ApacheJIT](docs/datasets.md) ground truth via Hit@k, MRR, and attribution quality metrics.

## Architecture (V4.2 — Current)

```
JIRA Bug Report (title + description)
    │
    ▼
Phase 0: Extraction → ProblemStatement
    │
    ▼
Phase 1a: Retrieval + Script Pre-Score → ScoredShortlist (15 candidates)
    │
    ▼
Phase 1b: Deterministic Triage → 3 must-examine + 4 watchlist (zero LLM)
    │
    ▼
Phase 2: Scoped Investigation (multi-turn ReAct, scoped tools)
    │
    ▼
Phase 2b: Watchlist Expansion (conditional)
    │
    ▼
Ranked suspects (commit_hash, confidence, mechanism, evidence)
```

The agent operates within **temporal bounds** — it can only see repository state up to the parent of the fix commit (`fix_hash~1`). Tools are scoped to a pre-retrieved CandidateSet.

## Quick Start

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Download ApacheJIT data and clone repos:

```bash
./scripts/download_apachejit.sh
./scripts/clone_apache_repos.sh
```

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/system-specification.md](docs/system-specification.md) | V4.2 pipeline, LLM boundary, data structures, temporal model |
| [docs/agent-loop.md](docs/agent-loop.md) | Agent loop: phases 1b-2-2b, scoped tools, nudge ladder |
| [docs/evaluation-framework.md](docs/evaluation-framework.md) | 5-stage funnel, metrics, baselines |
| [docs/datasets.md](docs/datasets.md) | ApacheJIT data, ground truth chain |
| [docs/glossary.md](docs/glossary.md) | Term definitions |

Architecture decisions: [.harness/docs/](.harness/docs/)

## Package Layout

```
├── src/commit_investigator/
│   ├── extraction/       # problem_extractor.py, jira_client.py
│   ├── retrieval/        # retriever.py, config.py, pipeline.py
│   ├── models/           # candidates.py (CandidateSet, CandidateCommit)
│   ├── agent/            # tools.py (build_scoped_tools, ToolRegistry)
│   ├── harness/          # scoped_runner.py, scoped_prompts.py, result.py, trace_writer.py, phase2b.py
│   ├── eval/             # ground_truth.py, coverage.py, metrics.py, helpers.py
│   └── infra/            # llm.py, git_context.py, smart_diff.py
├── data/apachejit/        # Train/test CSVs + replication zip (gitignored)
├── data/repos/            # Local git clones of Apache projects (gitignored)
├── scripts/               # Data download, eval prep, smoke tests
├── docs/                  # System spec, agent loop, evaluation, datasets, glossary
├── results/               # Eval run results and traces (JSON artifacts)
└── .harness/              # Adversarial verification harness state + ADRs
```

## Baselines

V3 (fully agentic, full repo tools): Hit@5=0.50, MRR=0.304 on n=20 (seed=42).
V4.2 (Cursor SDK, claude-sonnet-4-6): Hit@5=0.800, MRR=0.600 (n=5).
V4.2 (local gemma3:12b): Hit@5=0.250, MRR=0.225 (n=20).

See `.harness/state.json` for current tasks and status.
