# Commit Risk — Adversarial Agentic Harness

> **Experiment #19b** — a long-running investigative agent that examines real code commits and produces structured risk assessments with evidence. Validated against [ApacheJIT](docs/datasets.md) ground truth (~28K buggy commits, 15 Apache projects) through a six-dimension adversarial evaluation framework.

## Definitions

### What is an agent?

An agent is the LLM inside the harness: it reasons over the context bundle the orchestrator assembles and fills the structured report schema. It does not control routing, context selection, follow-up decisions, or budget — the orchestrator (harness) does. The agent's single job: reason over evidence and produce structured output with risk level, localization, and recommendations.

### What is a long-running agent?

A long-running agent operates at **batch scale over extended periods** — not a single prompt-response interaction. This agent processes hundreds of commits sequentially, each taking 30–70 seconds. A 100-commit evaluation run takes ~90 minutes and costs ~$0.30. Long-running agents need infrastructure that single-shot agents don't: cost governance, checkpoint persistence, error resilience, progress tracking, and reproducible artifacts.

### What is a harness?

A harness is the **deterministic infrastructure that produces the best possible investigation** — everything that isn't the LLM reasoning itself. It owns routing, context construction, schema validation, quality gates, follow-up triggers, budget enforcement, and artifact persistence. The harness never sees ground truth.

### What is adversarial evaluation?

Adversarial evaluation means the evaluation framework **actively tries to catch the agent being bad** — comparing output against ground truth the agent never saw. Six dimensions probe different failure modes: is the prediction correct (D1)? does it point to the right files (D2)? does the reasoning match the actual root cause (D3)? An LLM-as-judge scores reasoning against JIRA tickets. Automated grounding checks (D6) catch agents that classify correctly but cite no real evidence.

## Why agents?

Most commit-risk tooling stops at **classification**: a model outputs "buggy" or "clean" and a probability. That's useful but insufficient for a reviewer who needs to know *where* the risk is and *why*.

An agent adds value in the **gray zone** — commits where a classifier has medium confidence (0.3–0.7). For those, the agent reads the diff, examines file history, checks author patterns, and produces a structured investigation report. For clear-cut commits, an XGBoost router handles them at zero LLM cost.

### How the agent works

```
Commit arrives
    │
    ▼
XGBoost Router ──► P < 0.3? → SAFE (skip)
    │                P > 0.7? → HIGH (flag)
    ▼
AgentOrchestrator (0.3 ≤ P ≤ 0.7)
    │
    ├── Turn 1: Assemble context (diff + message + features + file history + author stats)
    │           → LLM reasons → structured JSON output
    │
    ├── Turn 2-3 (optional): Only if confidence < threshold or uncertainty flagged
    │           → targeted follow-up (deeper file history, blame)
    │
    ▼
CommitInvestigationReport
    ├── risk_assessment (level + confidence)
    ├── evidence[] (diff hunks, file history, metrics)
    ├── localization[] (file + lines + rationale)
    ├── findings[], recommendations[]
    └── metadata (cost, tokens, model, turn count)
```

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
python -m commit_investigator.run_eval --max-evals 20
```

Mock evaluation (no API key, methodology testing only):

```bash
python -m commit_investigator.run_eval --max-evals 20 --mock
```

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/architecture.md](docs/architecture.md) | System identity, design philosophy, investigation method, trust boundaries |
| [docs/harness.md](docs/harness.md) | Deterministic infrastructure: routing, budget, schema, control plane, improvement cycle |
| [docs/agent-loop.md](docs/agent-loop.md) | Investigation process: flow, validation, quality gates, model strategy |
| [docs/evaluation.md](docs/evaluation.md) | Six-dimension framework, acceptance thresholds, results |
| [docs/experiment-context.md](docs/experiment-context.md) | Research thesis, oracle isolation rationale |
| [docs/datasets.md](docs/datasets.md) | ApacheJIT splits, ground truth chain, download instructions |

Research spikes (archived): [.harness/archive/docs/](.harness/archive/docs/)

## Acceptance Thresholds

All six GATE thresholds must pass on a stratified eval (n >= 50, 50/50 buggy/clean) for V1 delivery.

| Dimension | GATE | TARGET | STRETCH |
|-----------|------|--------|---------|
| D1 Prediction | >= 0.70 | >= 0.80 | >= 0.90 |
| D2 Localization | >= 0.15 | >= 0.25 | >= 0.40 |
| D3 Diagnosis | >= 0.20 | >= 0.35 | >= 0.50 |
| D4 Severity | >= 0.60 | >= 0.75 | >= 0.85 |
| D5 Recommendations | >= 0.25 | >= 0.40 | >= 0.55 |
| D6 Evidence grounding | >= 0.60 | >= 0.70 | >= 0.80 |

## Package Layout

```
├── src/commit_investigator/   # Orchestrator (harness), agent, evaluation framework, router
├── data/apachejit/            # Train/test CSVs + replication zip (gitignored)
├── data/repos/                # Local git clones of Camel + Hadoop (gitignored)
├── scripts/                   # Data download and repo clone scripts
├── docs/                      # Architecture, harness, agent loop, evaluation, datasets
├── output/runs/               # Timestamped eval run artifacts (gitignored)
└── .harness/                  # Adversarial verification harness state
```

## Status

**Phase:** investigation-quality. iter-1 verified with Claude Sonnet 4.6 production data.

**iter-1 results (Claude n=20):** D1=0.60 (+0.20 from baseline), D3=0.20 (+0.07), D6=0.85 (stable). 5/6 gates pass at n=20. D1 at 0.60 vs 0.70 full gate — needs iter-2 validation at n=50.

**Next:** EXP-FORENSICS-TAG (classify D3 failure modes), then iter-2. 86 tests passing.
