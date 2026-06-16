# Bug Attribution Agent

> Given a JIRA bug report (title + description), an agentic investigator autonomously searches a git repository to identify the commit that introduced the bug. Produces ranked suspect lists with evidence-grounded causal explanations. Evaluated against [ApacheJIT](docs/datasets.md) ground truth via Hit@k, MRR, and attribution quality metrics.

## How the Agent Works

```
JIRA Bug Report (title + description)
    │
    ▼
ProblemExtractor → ProblemStatement
    │
    ▼
AgentOrchestrator (multi-turn, tool-use)
    │
    ├── Search: git log --grep, git log --all -- <file>
    ├── Examine: get_commit_diff, get_commit_message, get_file_at_commit
    ├── Refine: narrow suspects, build causal chains
    │
    ▼
BugAttributionReport
    ├── suspects[] (commit_hash, confidence, mechanism, evidence)
    ├── tool_trace[] (all tool calls + results)
    └── metadata (cost, tokens, tool_calls, elapsed_s)
```

The agent operates within **temporal bounds** — it can only see repository state up to the parent of the fix commit (`fix_hash~1`). It never sees the fix itself.

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

Run a real evaluation (requires `CURSOR_API_KEY` or `OPENAI_API_KEY`):

```bash
export CURSOR_API_KEY=your_key_here
python -m commit_investigator.eval.run_eval --max-evals 20
```

Mock evaluation (no API key, methodology testing only):

```bash
python -m commit_investigator.eval.run_eval --max-evals 20 --mock
```

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/system-specification.md](docs/system-specification.md) | Pipeline, LLM boundary, agentic loop (7 stages), tools, data structures, temporal model |
| [docs/evaluation-framework.md](docs/evaluation-framework.md) | Metrics (Hit@k, MRR, D3, D6), stage-to-metric mapping, baselines, thresholds |
| [docs/agent-loop.md](docs/agent-loop.md) | Agentic loop summary: 7-stage pipeline, tools, resource limits |
| [docs/datasets.md](docs/datasets.md) | ApacheJIT data, ground truth chain, git clones |
| [docs/glossary.md](docs/glossary.md) | Project-specific term definitions |

Research spikes: [.harness/docs/](.harness/docs/)

## Acceptance Thresholds

V3 metrics evaluated on n=20 stratified sample (seed=42).

| Metric | GATE | TARGET | Current |
|--------|------|--------|---------|
| Hit@5 | >= 0.20 | >= 0.40 | **0.50** |
| MRR | >= 0.10 | >= 0.25 | **0.304** |
| D6 Evidence Grounding | >= 0.40 | >= 0.60 | **0.610** |
| D3 Attribution Quality | — | >= 0.40 | pending n=50 |
| Retrieval Recall | >= 0.15 | >= 0.40 | pending |

## Package Layout

```
├── src/commit_investigator/
│   ├── extraction/       # problem_extractor.py, jira_client.py
│   ├── agent/            # orchestrator.py, tools.py, evidence_tagger.py, investigators
│   ├── eval/             # eval_metrics.py, d3_judge.py, baselines.py, run_eval.py, ground_truth.py
│   └── infra/            # llm.py, git_context.py, smart_diff.py (shared)
├── data/apachejit/        # Train/test CSVs + replication zip (gitignored)
├── data/repos/            # Local git clones of Apache projects (gitignored)
├── scripts/               # Data download, eval prep, smoke tests
├── docs/                  # System spec, evaluation framework, agent loop, datasets, glossary
├── results/               # Eval run results (JSON artifacts)
└── .harness/              # Adversarial verification harness state
```

## Status

**Phase:** V3 — Agentic attribution via tool-use (Cursor Task Subagent + Prompt V2).

**Latest n=20 (V3, Prompt V2):**

| Metric | Score | Target | Status |
|--------|-------|--------|--------|
| Hit@1 | 0.200 | — | — |
| Hit@5 | 0.500 | ≥ 0.40 | **PASS** |
| MRR | 0.304 | ≥ 0.25 | **PASS** |
| D6 Evidence | 0.610 | ≥ 0.60 | **PASS** |

**Adjusted for GT noise:** Hit@5 = 0.70 on plausible-only cases (7/10).
