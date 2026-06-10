# Commit Risk Investigator

> **Experiment #19b** — an investigative AI agent that examines real code commits and produces structured risk assessments with evidence and reasoning. [ApacheJIT](docs/datasets.md) provides the validation test bed: a rare six-dimensional ground-truth chain covering ~28K buggy commits across 15 Apache projects.

## Thesis

Most commit-risk tooling stops at classification. This project builds an **investigative agent** that gathers diff, message, and repository evidence through a bounded multi-turn loop, then emits a structured investigation report. Harness engineering — context construction, routing, budget gates, schema validation — matters more than raw model quality.

Full research framing: [docs/experiment-context.md](docs/experiment-context.md)

## Quick Start

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Download ApacheJIT data (optional, for later tasks):

```bash
./scripts/download_apachejit.sh
```

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/experiment-context.md](docs/experiment-context.md) | Thesis, eval dimensions, cost governance, oracle isolation |
| [docs/datasets.md](docs/datasets.md) | ApacheJIT splits, ground truth chain, replication package |
| [docs/evaluation.md](docs/evaluation.md) | Six-dimension evaluation framework (D1–D6) |

System design: [ARCHITECTURE.md](ARCHITECTURE.md)

## V1 Scope

- Local git clones of **Camel** and **Hadoop** (two projects)
- Bounded agent loop (max 3 turns) with deterministic orchestrator
- XGBoost router on numeric features; LLM investigation only on gray-zone commits
- Six-dimension eval against ApacheJIT oracle (D3/D5 LLM-as-judge, D6 automated grounding, JIRA reserved for eval only)

Agent framework choice (LangGraph vs minimal custom loop) is **deferred** until after V1.

## Package Layout

```
├── src/commit_investigator/   # Python package (scaffold)
├── data/apachejit/            # Train/test CSVs + replication zip (gitignored)
├── scripts/download_apachejit.sh
├── docs/                      # Experiment and eval documentation
└── .harness/                  # Adversarial verification harness
```

## Status

All 11 V1 tasks complete. Agent investigates commits via Cursor SDK, scores against ground truth on 6 dimensions. First real eval (20 commits): D1=0.85, D6=0.75 (grounded evidence), D3=0.20 (judge catches generic reasoning). See [docs/evaluation.md](docs/evaluation.md).
