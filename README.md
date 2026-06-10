# Commit Risk — Adversarial Agentic Harness

> **Experiment #19b** — a long-running investigative agent that examines real code commits and produces structured risk assessments with evidence. Validated against [ApacheJIT](docs/datasets.md) ground truth (~28K buggy commits, 15 Apache projects) through a six-dimension adversarial evaluation harness.

## Definitions

### What is an agent?

An agent is an LLM-powered system that **takes actions** — not just answers questions. It reads diffs, calls tools (git log, file history, blame), reasons over what it finds, and decides whether to dig deeper or finalize its assessment. The agent here is not a chatbot: it receives a commit, investigates it autonomously, and outputs a structured report with risk level, evidence, localization, and recommendations.

### What is a long-running agent?

A long-running agent operates at **batch scale over extended periods** — not a single prompt-response interaction. This agent processes hundreds of commits sequentially, each taking 30–70 seconds. A 100-commit evaluation run takes ~90 minutes and costs ~$0.30. Long-running agents need infrastructure that single-shot agents don't: cost governance (per-commit and per-run budgets), checkpoint persistence, error resilience (one failure can't crash a 2-hour run), progress tracking, and reproducible artifacts per run.

### What is a harness?

A harness is the **deterministic infrastructure around the agent** — everything that isn't the LLM call itself. The harness owns routing (which commits need investigation), context construction (what the agent sees), schema validation (what the agent must output), budget enforcement (how much it can spend), evaluation (how we measure quality), and artifact persistence (where results go). The thesis: harness engineering matters more than model quality. A better prompt on a worse harness produces worse results than a basic prompt on a well-engineered harness.

### What is adversarial evaluation?

Adversarial evaluation means the harness **actively tries to catch the agent being bad**, not just checks that it runs. Six dimensions probe different failure modes: is the prediction correct (D1)? does it point to the right files (D2)? does the reasoning match the actual bug root cause, or is it generic boilerplate (D3)? An LLM-as-judge scores the agent's reasoning against ground truth from JIRA tickets that the agent never saw. Automated grounding checks (D6) catch agents that classify correctly but cite no real evidence.

See [docs/harness.md](docs/harness.md) for the full harness architecture.

## Why agents?

Most commit-risk tooling stops at **classification**: a model outputs "buggy" or "clean" and a probability. That's useful but insufficient for a reviewer who needs to know *where* the risk is and *why*.

An agent adds value in the **gray zone** — commits where a classifier has medium confidence (0.3–0.7). For those, the agent reads the diff, examines file history, checks author patterns, and produces a structured investigation report. For clear-cut commits (very low or very high risk), an XGBoost router handles them at zero LLM cost.

### Where agents are used in this system

| Component | Agent? | What it does |
|-----------|--------|-------------|
| XGBoost router | No | Scores all commits on numeric features. Zero LLM cost. |
| **AgentOrchestrator** | **Yes** | Bounded multi-turn investigation loop (max 3 turns). Reads diff, calls tools, reasons, outputs structured report. |
| ReasoningJudge | Yes (eval-only) | LLM-as-judge that scores agent reasoning against JIRA ground truth. Never runs in production — only during evaluation. |

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
| [docs/harness.md](docs/harness.md) | Harness architecture: why, how, what it controls |
| [docs/experiment-context.md](docs/experiment-context.md) | Thesis, eval dimensions, cost governance, oracle isolation |
| [docs/datasets.md](docs/datasets.md) | ApacheJIT splits, ground truth chain, replication package |
| [docs/evaluation.md](docs/evaluation.md) | Six-dimension framework, acceptance thresholds, results |

System design: [ARCHITECTURE.md](ARCHITECTURE.md)

## Acceptance Thresholds

Defined before the n=100 eval run. All six GATE thresholds must pass on a stratified eval (n >= 50, 50/50 buggy/clean) for V1 delivery.

| Dimension | GATE | TARGET | STRETCH |
|-----------|------|--------|---------|
| D1 Prediction | >= 0.70 | >= 0.80 | >= 0.90 |
| D2 Localization | >= 0.15 | >= 0.25 | >= 0.40 |
| D3 Diagnosis | >= 0.20 | >= 0.35 | >= 0.50 |
| D4 Severity | >= 0.60 | >= 0.75 | >= 0.85 |
| D5 Recommendations | >= 0.25 | >= 0.40 | >= 0.55 |
| D6 Evidence grounding | >= 0.60 | >= 0.70 | >= 0.80 |

Full methodology and results: [docs/evaluation.md](docs/evaluation.md)

## Package Layout

```
├── src/commit_investigator/   # Agent, orchestrator, eval harness, router
├── data/apachejit/            # Train/test CSVs + replication zip (gitignored)
├── data/repos/                # Local git clones of Camel + Hadoop (gitignored)
├── scripts/                   # Data download and repo clone scripts
├── docs/                      # Experiment, eval, harness, and dataset docs
├── output/runs/               # Timestamped eval run artifacts (gitignored)
└── .harness/                  # Adversarial verification harness state
```

## Status

All 11 V1 tasks complete. Data leakage fix applied (buggy/fix labels removed from agent context). Clean n=5 eval: D1=0.40 (FAIL gate), D6=0.85 (PASS), D3=0.13 (FAIL). 3/6 gates fail — prompt engineering needed for D1/D3. See [docs/evaluation.md](docs/evaluation.md).
