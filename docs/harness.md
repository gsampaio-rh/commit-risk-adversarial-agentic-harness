# The Harness

The harness is everything around the LLM that makes the agent work reliably at scale. Without it, you have a prompt and a prayer. With it, you have a system.

## Why a harness?

An LLM call is non-deterministic, expensive, slow, and has no memory of what happened before. An agent that runs on 100 commits needs to survive all of those properties without human intervention.

The naive approach — "send the diff to the LLM and ask if it's buggy" — fails in predictable ways:

| Problem | What happens without a harness |
|---------|-------------------------------|
| Cost blowup | $15+ for 100 commits, most of which are obviously safe |
| Generic reasoning | LLM says "this is a large change, therefore risky" on every commit |
| No evidence trail | Can't verify *why* the agent flagged something |
| Silent failures | API timeout on commit #47 crashes the entire run |
| Unreproducible | Same commit, different day, different answer |
| No quality signal | D1=0.85 looks good until you realize always-predict-clean scores 0.98 on an imbalanced dataset |

The harness solves each of these with deterministic infrastructure.

## What the harness controls

### 1. Routing — who gets investigated

Not every commit needs an LLM. An XGBoost router trained on numeric features (lines added, files touched, author experience, entropy) classifies every commit at zero cost:

- **P < 0.3** → SAFE. Skip the agent entirely.
- **0.3 ≤ P ≤ 0.7** → INVESTIGATE. Full agent loop.
- **P > 0.7** → HIGH. Flag directly; optional light LLM confirmation.

The router handles ~60% of commits without spending a single token. The agent focuses on the gray zone where classification alone is insufficient and investigation adds value.

### 2. Context construction — what the agent sees

The agent doesn't choose what to read. The `CommitContextBuilder` assembles a fixed context bundle for every commit:

| Context piece | Source | Why |
|--------------|--------|-----|
| Unified diff | Local git clone | What changed in the code |
| Commit message | Local git clone | What the author intended |
| Touched files | Local git clone | Scope of the change |
| Numeric features | ApacheJIT CSV | Quantitative change metrics |
| File history (last 3 commits) | Local git clone | Recent activity in changed files |
| Author stats | Precomputed from train split | Author experience in this project |

This bundle is deterministic — same commit, same context, every time. The agent receives exactly what a human reviewer would see at commit time. No future information (fix commit, JIRA ticket) leaks into the investigation.

### 3. Turn governance — how much the agent can do

The `AgentOrchestrator` enforces hard limits:

- **Max 3 turns** per commit (V1 runs with 1 turn for cost efficiency)
- **50K token budget** per investigation
- **$0.50 cost cap** per commit
- **Follow-up triggers are deterministic**: low confidence, missing localization, explicit uncertainty

The orchestrator — not the LLM — decides when to stop. The LLM can request follow-up (`"follow_up_needed": true`), but the orchestrator enforces the cap.

### 4. Schema validation — what the agent must output

The agent cannot return freeform text. It must produce a `CommitInvestigationReport` (Pydantic-validated) with:

- Risk level (LOW/MEDIUM/HIGH/CRITICAL) + confidence (0–1)
- At least one evidence item (enforced — empty reports are rejected)
- Localization claims (file + lines + rationale)
- Findings, recommendations, tools used, turn count

If the LLM returns malformed JSON, the orchestrator extracts what it can (tolerates markdown fences, partial JSON) and fills defaults for the rest.

### 5. Error resilience — what happens when things break

In a 100-commit run (~90 minutes), things will break: API timeouts, rate limits, malformed responses, git commits that don't exist in the local clone.

| Failure | Harness behavior |
|---------|-----------------|
| LLM API timeout | Return LOW risk with confidence=0 and error in metadata |
| Malformed JSON from LLM | Extract partial JSON, fill defaults, log warning |
| Commit not in git clone | Skip with reason, don't crash the run |
| JIRA API error during eval | Score D3/D4/D5 as 0 with error note, continue |
| Budget exceeded mid-turn | Stop investigation, assemble report from what's available |

No single failure stops the run. Every failure is logged, and the eval report shows exactly which commits had errors and why.

### 6. Cost governance — how much the run can spend

Three budget tiers:

| Tier | Budget | Commits | Use case |
|------|--------|---------|----------|
| Smoke | $10 | ~50 | CI/quick validation |
| Standard | $50 | ~300 | Default eval run |
| Deep | $100 | ~1000 | Manual deep analysis |

Cost tracking happens at three levels: per-turn (token count + estimated cost), per-commit (total across turns), per-run (sum of all commits). The run log shows running totals.

### 7. Evaluation — how we know the agent is good

The eval harness compares agent output against ground truth across six dimensions. This is the adversarial part — it's designed to catch the agent being bad, not to confirm it's good.

| Dimension | What it catches | Method |
|-----------|----------------|--------|
| D1 Prediction | Wrong risk classification | Deterministic: risk level vs buggy label |
| D2 Localization | Pointing to wrong files | Deterministic: Jaccard(agent files, fix files) |
| D3 Diagnosis | Generic reasoning that sounds good but says nothing | LLM-as-judge with rubric (0–4) |
| D4 Severity | Miscalibrated risk levels | Deterministic: risk vs JIRA priority |
| D5 Recommendations | Useless or disconnected suggestions | LLM-as-judge with rubric (0–3) |
| D6 Evidence grounding | Boilerplate that cites no real artifacts | Automated: agent claims vs actual diff/files |

The key insight is D3 and D6 working together. An agent can score D1=0.90 (great prediction) and D6=0.80 (cites real files) but D3=0.15 (reasoning is generic). That means it's classifying based on surface features ("big diff = risky") without understanding the actual bug mechanism. The harness catches this; a simple accuracy metric would miss it.

### 8. Artifact persistence — where results go

Every eval run creates a timestamped folder:

```
output/runs/2026-06-10_11-39-59_real_n100/
├── run-config.json          # All CLI args, git rev, python version, stratification
├── run.log                  # Full timestamped log (every commit, every error)
├── investigations/          # Per-commit agent reports (what the agent produced)
│   ├── f897d46870ba_camel.json
│   └── ...
├── evaluations/             # Per-commit eval scores (how it scored vs ground truth)
│   ├── f897d46870ba_camel.json
│   └── ...
├── eval-report.json         # Aggregate D1–D6 scores, baselines, strata (unified)
└── eval-report.md           # Human-readable report
```

Run configs capture everything needed to reproduce: exact git revision, Python version, stratification counts, provider, CLI arguments. Investigation reports capture per-commit risk, reasoning, evidence, cost, and timing.

## Harness vs agent — what lives where

| Responsibility | Harness (deterministic) | Agent (LLM) |
|---------------|------------------------|-------------|
| Which commits to investigate | Routing | — |
| What context to provide | Context builder | — |
| When to stop investigating | Orchestrator (turn cap) | Can request follow-up |
| What format to output | Schema validation | Fills the schema |
| How much to spend | Budget enforcement | — |
| Whether the output is good | Eval harness + judge | — |
| **Reasoning over evidence** | — | Core LLM value |

The LLM does exactly one thing: reason over assembled context and produce structured output. Everything else is deterministic.

## Related

- [ARCHITECTURE.md](../ARCHITECTURE.md) — component map and design decisions
- [evaluation.md](evaluation.md) — D1–D6 framework, acceptance thresholds, results
- [experiment-context.md](experiment-context.md) — research thesis and oracle isolation
