# The Harness

The harness is the deterministic infrastructure around the [agent loop](agent-loop.md) — routing, budget, schema validation, error recovery, artifact persistence. It does not reason; it ensures the agent loop runs reliably at scale.

## Definitions

Five concepts operate in this system. The industry often conflates "harness" with "agent loop." In this project, they are distinct.

| Concept | What it is | When it runs | Sees ground truth? |
|---------|-----------|--------------|-------------------|
| **Harness** | Deterministic infrastructure: routing, budget caps, schema enforcement, error recovery, artifact persistence, cost tracking. Does not change between iterations. | Always — wraps every investigation | **Never.** |
| **Agent loop** | The investigation process itself: context assembled → LLM reasons → output validated → follow-up or finalize → report assembled. Runs inside the harness. | At investigation time — once per commit | **Never.** |
| **Investigation method** | The *configuration* of the agent loop: which prompt, which model, how many turns, what triggers follow-up, what context to inject. This is what we iterate on between eval runs. | Defined before a run; fixed during the run | **Never.** |
| **Evaluation** | Post-hoc measurement of agent loop output against ground truth across six dimensions. | After a run completes — batch scoring | **Yes.** Full oracle access. |
| **Improvement cycle** | Uses evaluation results to change the investigation method. Human-driven. | Between runs | **Indirectly** — reads eval reports. |

### Why the separation matters

The agent loop never knows whether its output will be evaluated. It never sees ground truth. The harness enforces structural constraints (budget, schema, error recovery) while the investigation method (inside the agent loop) determines reasoning quality through prompt design and context strategy.

Evaluation is a *diagnostic tool for us*. If D3 is low, the method needs to force more mechanistic reasoning. If D1 is low, the method needs stricter classification rules. The eval doesn't fix the agent loop — we fix the investigation method based on what eval reveals.

### Other terms

| Term | Definition |
|------|-----------|
| **Agent** | The LLM call inside the agent loop. It reasons over assembled context and fills the structured schema. |
| **Orchestrator** | The deterministic code that *runs* the agent loop: assembles context, calls the LLM, validates output, decides follow-up, assembles the report. Part of the harness. |
| **Ground truth** | Oracle data (ApacheJIT buggy labels, fix-commit diffs, JIRA tickets). Used exclusively by evaluation. |
| **Router** | XGBoost model that decides which commits enter the agent loop. Pre-harness, zero LLM cost. |

### Industry context

"Agent loop" (also: "agentic loop", "ReAct loop") is the 2026 industry-standard term for the Perceive → Reason → Act → Observe cycle. Our agent loop is **constrained** — the orchestrator controls turn count, context, and follow-up decisions. See [agent-loop.md](agent-loop.md) for details.

---

## System Loop

The overall system operates as a closed loop with three legs:

```
┌──────────────────────────────────────────────────────────┐
│                                                          │
│   ┌───────────┐     ┌──────────┐     ┌──────────────┐   │
│   │Agent Loop ├────►│   Eval   ├────►│   Improve    │   │
│   └─────▲─────┘     └──────────┘     └──────┬───────┘   │
│         │                                     │           │
│         └─────────────────────────────────────┘           │
│                                                          │
│   Investigates        Measures quality    Changes method │
│   (inside harness)    against GT          to produce     │
│                                           better output  │
└──────────────────────────────────────────────────────────┘
```

- **Agent loop (inside harness):** Investigates a commit. Produces a `CommitInvestigationReport`. No ground truth involved. See [agent-loop.md](agent-loop.md).
- **Eval:** Measures agent output against ground truth (D1–D6). See [evaluation.md](evaluation.md).
- **Improve:** Uses eval diagnosis to change the investigation method (prompt, context, turns). Then re-runs to verify improvement.

---

## Why a Harness?

An LLM call is non-deterministic, expensive, slow, and has no memory. An agent that runs on 100 commits needs to survive all of those properties without human intervention.

| Problem | What happens without a harness |
|---------|-------------------------------|
| Cost blowup | $15+ for 100 commits, most of which are obviously safe |
| Generic reasoning | LLM says "this is a large change, therefore risky" on every commit |
| No evidence trail | Can't verify *why* the agent flagged something |
| Silent failures | API timeout on commit #47 crashes the entire run |
| Unreproducible | Same commit, different day, different answer |
| No quality signal | D1=0.85 looks good until you realize always-predict-clean scores 0.98 |

---

## Control Plane

The control plane is the deterministic infrastructure that implements the design above.

### 1. Routing — who gets investigated

Not every commit needs an LLM. An XGBoost router trained on numeric features classifies every commit at zero cost:

- **P < 0.3** → SAFE. Skip the agent entirely.
- **0.3 ≤ P ≤ 0.7** → INVESTIGATE. Full agent loop.
- **P > 0.7** → HIGH. Flag directly; optional light LLM confirmation.

The router handles ~60% of commits without spending a single token.

### 2. Context construction — what the agent sees

The `CommitContextBuilder` assembles a fixed context bundle:

| Context piece | Source | Why |
|--------------|--------|-----|
| Unified diff | Local git clone | What changed |
| Commit message | Local git clone | What the author intended |
| Touched files | Local git clone | Scope of the change |
| Numeric features | ApacheJIT CSV (allowlist only) | Quantitative change metrics |
| File history (last 3 commits) | Local git clone | Recent activity in changed files |
| Author stats | Precomputed from train split | Author experience |
| Router probability | XGBoost output | ML prior |

This bundle is deterministic — same commit, same context, every time.

### 3. Turn governance — how much the agent can do

The `AgentOrchestrator` enforces hard limits:

- **Max 3 turns** per commit (V1 default: 1 turn for cost efficiency)
- **50K token budget** per investigation
- **$0.50 cost cap** per commit
- **Follow-up triggers are deterministic**: low confidence, missing localization, explicit uncertainty

### 4. Schema validation — what the agent must output

A `CommitInvestigationReport` (Pydantic-validated) with:

- Risk level (LOW/MEDIUM/HIGH/CRITICAL) + confidence (0–1)
- At least one evidence item (enforced — empty reports rejected)
- Localization claims (file + lines + rationale)
- Findings, recommendations, tools used, turn count

### 5. Error resilience — what happens when things break

| Failure | Harness behavior |
|---------|-----------------|
| LLM API timeout | Return LOW risk with confidence=0 and error in metadata |
| Malformed JSON | Extract partial JSON, fill defaults, log warning |
| Commit not in clone | Skip with reason, don't crash the run |
| JIRA API error (eval) | Score D3/D4/D5 as 0 with error note, continue |
| Budget exceeded | Stop investigation, assemble report from available data |

### 6. Cost governance — budget tiers

| Tier | Budget | Commits | Use case |
|------|--------|---------|----------|
| Smoke | $10 | ~50 | CI/quick validation |
| Standard | $50 | ~300 | Default eval run |
| Deep | $100 | ~1000 | Manual deep analysis |

---

## Investigation Method (Operational)

This section describes **how the harness enforces** the investigation method defined in [architecture.md §3](architecture.md#3-the-investigation-method).

### Four-stage enforcement

| Stage | Purpose | Quality signal |
|-------|---------|---------------|
| 1. Change summary | Scope the commit: files, intent, magnitude | Accurate file list, correct intent |
| 2. Defect hypotheses | Generate 2–3 mechanistic candidates: "If ⟨X⟩ then ⟨Y⟩ in ⟨Z⟩" | Specificity — not "could have bugs" |
| 3. Evidence triage | Mark each hypothesis SUPPORTED / REFUTED / UNVERIFIABLE with diff cites | Cites specific lines/hunks |
| 4. Verdict | Risk level tied to supported evidence, not hedge language | Consistent with findings |

### Classification rule enforcement

- **Mechanism floor:** SUPPORTED hypothesis → risk ≥ HIGH.
- **No hedge downgrade:** "Purely additive", "limited blast radius" must not lower risk when a mechanism is identified.
- **Localization = defect site:** `localization[]` means "where the bug probably lives", not "all files I read."
- **ML prior ≠ oracle:** Router probability is a calibrated prior, not a label.

### Known failure modes

| Failure | D-impact | Root cause | Status |
|---------|----------|-----------|--------|
| MEDIUM on buggy commits | D1 | Agent defaults to MEDIUM without rubric pressure | Fixed in iter-1 (rubric + CoT) |
| "Could contain bugs" reasoning | D3 | Describes structure, not failure mechanism | Partially fixed (iter-1 D3=0.20) |
| Localization = all touched files | D2 | No distinction between "analyzed" and "defective" | Open (iter-4) |
| Hedging language overrides findings | D1, D3 | "Limited blast radius" downgrades despite evidence | Fixed in iter-1 (hedge-ban rules) |
| False positives on clean commits | D1 | Agent generates plausible SUPPORTED hypotheses on clean code | Open (6/10 clean → HIGH in Claude n=20) |

---

## Improvement Cycle

The improvement cycle is the system's evolution mechanism — disciplined, measured, reversible.

### Steps

| # | Action | Artifact | Typical cost/time |
|---|--------|----------|-------------------|
| 1 | **Hypothesize:** which dimension, which method change | Breadcrumb note | — |
| 2 | **Implement:** prompt/context/turn change (method only) | Code diff in `orchestrator.py` | — |
| 3 | **Smoke:** n=5 stratified, catch regressions early | `output/runs/..._real_n5/` | ~5 min, ~$0.02 |
| 4 | **Validate:** n=20 stratified, measure all six dimensions | `eval-report.json` | ~24 min, ~$0.10 |
| 5 | **Compare:** per-commit JSONs vs baseline, check gate trajectory | Updated baseline | — |
| 6 | **Decide:** trending toward gate → n=50 for confidence; flat → pivot | State update | ~1 hr, ~$0.35 |

### Hard constraints (every iteration)

- Oracle isolation holds: agent never sees `buggy`, `fix`, `year`, `author_date`, JIRA
- 86+ tests pass after every change
- D6 ≥ 0.70 — grounding regression = immediate revert
- Each iteration tracked as a breadcrumb with before/after scores

### Current phase roadmap

| Task | Focus | Status |
|------|-------|--------|
| spike-0 | Investigation harness design | **Complete** |
| iter-1 | A+B hybrid: risk rubric + staged CoT + router probability | **Verified** (Claude n=20: D1=0.60, D3=0.20, D6=0.85) |
| EXP-FORENSICS-TAG | Classify D3 failure modes from iter-1 data | Next |
| iter-2 | Validate at n=50 | Pending |
| iter-3 | Multi-turn for low-confidence commits | Pending |
| iter-4 | D2 localization focus | Pending |

---

## Industry References

### Agent loop architecture
- **ReAct** (Yao et al. 2022) — the foundational perceive-reason-act-observe pattern.
- **Anthropic "Building Effective Agents"** — agents (open-ended loops) vs workflows (predetermined sequences). We are a workflow with agent reasoning inside.
- **Claude Code Agent SDK** — canonical implementation of the agentic loop with turn-based tool execution.

### Prompt/method management
- **DSPy** — programmatic prompt optimization. Relevant pattern: treat prompts as versioned, diffable artifacts.
- **Braintrust / Humanloop** — prompt versioning, A/B testing, experiment tracking.

### LLM-as-judge / rubric management
- **Braintrust scorers** — rubric scoring, pairwise ranking. Pattern: deterministic scorers for format/fact, LLM-as-judge for subjective quality.
- **Multi-judge voting** — aggregates scores from multiple models. Relevant to EXP-JUDGE-SWAP.

### Context engineering
- **Dynamic context assembly** (Zylos Research 2026) — context window as a projection. Our `CommitContextBuilder` IS a context assembler.
- **Pinned + Retrieved architecture** — system prompt (stable, cacheable) + per-commit context (dynamic).

---

## Related

- [agent-loop.md](agent-loop.md) — investigation process: flow, validation, quality gates, model strategy
- [architecture.md](architecture.md) — system identity, design philosophy, trust boundaries
- [evaluation.md](evaluation.md) — D1–D6 rubrics, acceptance thresholds, run results
- [datasets.md](datasets.md) — ApacheJIT ground truth chain
- [experiment-context.md](experiment-context.md) — research thesis, oracle isolation rationale
