# The Harness

The harness is the deterministic infrastructure around the agent loop — routing, budget, schema validation, error recovery, artifact persistence. It does not reason; it ensures the agent loop runs reliably at scale.

## Definitions

Five concepts operate in this system. The industry often conflates "harness" with "agent loop" (one source literally lists them as synonyms). In this project, they are distinct.

| Concept | What it is | When it runs | Sees ground truth? |
|---------|-----------|--------------|-------------------|
| **Harness** | Deterministic infrastructure around the agent loop: routing, budget caps, schema enforcement, error recovery, artifact persistence, cost tracking. Does not change between iterations. | Always running — wraps every investigation | **Never.** |
| **Agent loop** | The investigation process itself: context assembled → LLM reasons → output validated → follow-up or finalize → report assembled. Industry-standard term (Perceive → Reason → Act → Observe). Runs inside the harness. | At investigation time — once per commit | **Never.** Operates on commit-time context only. |
| **Investigation method** | The *configuration* of the agent loop: which prompt, which model, how many turns, what triggers follow-up, what context to inject. This is what we iterate on between eval runs. | Defined before a run; fixed during the run | **Never.** |
| **Evaluation** | Post-hoc measurement of agent loop output against ground truth. Compares reports to buggy labels, fix commits, JIRA issues across six dimensions. | After a run completes — batch scoring | **Yes.** Full oracle access. |
| **Improvement cycle** | Uses evaluation results to change the investigation method. Reads eval scores → diagnoses failure patterns → modifies method → re-runs. Human-driven. | Between runs — manual | **Indirectly** — reads eval reports. |

### Why the separation matters

The agent loop never knows whether its output will be evaluated. It never sees ground truth. It cannot "cheat" by reading the answer. The harness enforces structural constraints (budget, schema, error recovery) while the investigation method (inside the agent loop) determines reasoning quality through prompt design and context strategy.

Evaluation is a *diagnostic tool for us*. It tells us whether the investigation method is working. If D3 is low, the method needs to force more mechanistic reasoning. If D1 is low, the method needs stricter classification rules. The eval doesn't fix the agent loop — we fix the investigation method based on what eval reveals.

### Other terms

| Term | Definition |
|------|-----------|
| **Agent** | The LLM call inside the agent loop. It reasons over assembled context and fills the structured schema. It does NOT control routing, context, turns, budget, or output validation. |
| **Orchestrator** | The deterministic code that *runs* the agent loop: assembles context, calls the LLM, validates output, decides follow-up, assembles the final report. Part of the harness. |
| **Ground truth** | The oracle data (ApacheJIT buggy labels, fix-commit diffs, JIRA tickets). Used exclusively by evaluation. Never enters the agent loop. |
| **Router** | XGBoost model that decides which commits enter the agent loop. Pre-harness, zero LLM cost. |

### Industry context

"Agent loop" (also: "agentic loop", "ReAct loop") is the 2026 industry-standard term for the Perceive → Reason → Act → Observe cycle that every agent framework implements. Our agent loop is **constrained** — the orchestrator controls turn count, context, and follow-up decisions. The LLM signals uncertainty but does not steer. This is a deliberate architectural choice for cost governance and reproducibility.

---

## System Loop

The overall system operates as a closed loop with three legs:

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│   ┌───────────┐     ┌──────────┐     ┌──────────────┐      │
│   │Agent Loop ├────►│   Eval   ├────►│   Improve    │      │
│   └─────▲─────┘     └──────────┘     └──────┬───────┘      │
│         │                                     │              │
│         └─────────────────────────────────────┘              │
│                                                              │
│   Investigates        Measures quality    Changes method     │
│   (inside harness)    against GT          to produce better  │
└──────────────────────────────────────────────────────────────┘
```

- **Agent loop (inside harness):** Investigates a commit — context assembly, LLM reasoning, output validation, follow-up if insufficient. Produces a `CommitInvestigationReport`. No ground truth involved.
- **Eval:** Measures the agent loop output against ground truth (D1–D6). Diagnoses where the investigation method is failing.
- **Improve:** Uses eval diagnosis to change the investigation method (prompt, context, turns). Then re-runs to verify improvement.

The harness is stable infrastructure. The investigation method (configuring the agent loop) is the variable we iterate on. See [ARCHITECTURE.md](../ARCHITECTURE.md) for design philosophy.

---

## Agent Loop

The agent loop is what happens when a commit enters the investigation zone. It is the core Perceive → Reason → Act → Observe cycle, constrained by the harness infrastructure.

```
Commit enters INVESTIGATE zone (0.3 ≤ P ≤ 0.7)
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ CONTEXT ASSEMBLY (deterministic — no LLM)               │
│                                                         │
│  CommitContextBuilder assembles:                        │
│  • Unified diff (from local git clone)                  │
│  • Commit message                                       │
│  • Touched files list                                   │
│  • Numeric features (allowlist: LA, LD, NF, ND, etc.)  │
│  • File history (last 3 commits per touched file)       │
│  • Author stats (precomputed from train split)          │
│  • Router probability (ML prior)                        │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│ TURN 1 — Primary investigation                          │
│                                                         │
│  LLM receives: system prompt + context bundle           │
│  LLM produces: structured JSON with:                    │
│    • risk_assessment (level + confidence)               │
│    • evidence[] (diff hunks, metrics, patterns)         │
│    • localization[] (file + lines + rationale)          │
│    • findings[], recommendations[]                      │
│    • reasoning_summary                                  │
│                                                         │
│  Orchestrator checks:                                   │
│    ✓ Valid JSON? (tolerates markdown fences)             │
│    ✓ Schema validates? (≥1 evidence item required)      │
│    ✓ Budget OK? (tokens + cost within cap)              │
└────────────────────────────┬────────────────────────────┘
                             │
                 ┌───────────┴───────────┐
                 │ Follow-up needed?      │
                 │                        │
                 │ YES if ANY of:         │
                 │ • confidence < 0.6     │
                 │ • zero localization    │
                 │ • explicit uncertainty │
                 │   in reasoning         │
                 └───────┬───────┬────────┘
                    NO   │       │  YES
                         │       │
                         ▼       ▼
┌────────────────────┐  ┌────────────────────────────────┐
│ DONE — assemble    │  │ TURN 2–3 — Targeted follow-up  │
│ final report       │  │                                │
│                    │  │  Additional context injected:  │
│                    │  │  • Deeper file history / blame │
│                    │  │  • "Is this closer to LOW or   │
│                    │  │     HIGH? What specific        │
│                    │  │     failure mode?"             │
│                    │  │                                │
│                    │  │  Same schema enforcement.      │
│                    │  │  Max 3 turns total (hard cap). │
└────────┬───────────┘  └──────────────┬─────────────────┘
         │                              │
         └──────────────┬───────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│ REPORT ASSEMBLY (deterministic)                         │
│                                                         │
│  Orchestrator merges turn outputs into final            │
│  CommitInvestigationReport:                             │
│  • Best risk_assessment (highest confidence turn)       │
│  • All evidence items aggregated                        │
│  • Localization deduplicated                            │
│  • Metadata: cost, tokens, turn_count, model, timing   │
│                                                         │
│  Persisted to: investigations/<hash>_<project>.json     │
└─────────────────────────────────────────────────────────┘
```

### After the LLM responds: how the harness validates and improves the investigation

The LLM produced a response. But the harness doesn't just accept it — it **validates the quality of the investigation itself** and forces improvement when it's insufficient. This happens *before* the report is finalized, not after.

```
LLM raw response (JSON)
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ STRUCTURAL VALIDATION — Can we even use this?           │
│                                                         │
│  • Valid JSON? (tolerant: strips markdown fences,       │
│    extracts from partial responses)                     │
│  • Schema conformance? (Pydantic validates against      │
│    CommitInvestigationReport)                           │
│  • At least 1 evidence item? (hard reject if empty)    │
│                                                         │
│  FAIL → fill defaults, log warning, mark degraded      │
└────────────────────────────┬────────────────────────────┘
                             │ PASS
                             ▼
┌─────────────────────────────────────────────────────────┐
│ QUALITY GATES — Is the investigation good enough?       │
│                                                         │
│  The orchestrator checks signals that indicate the      │
│  agent hasn't done its job:                             │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │ Signal              │ Meaning                     │  │
│  ├─────────────────────┼─────────────────────────────┤  │
│  │ confidence < 0.6    │ Agent isn't sure — needs    │  │
│  │                     │ more investigation          │  │
│  │ localization = []   │ Agent can't point to where  │  │
│  │                     │ the problem lives           │  │
│  │ explicit uncertainty│ Agent says "I'm unsure" or  │  │
│  │ in reasoning        │ "insufficient context"      │  │
│  │ all findings are    │ Agent described structure,   │  │
│  │ generic             │ not mechanism (iter-1+)     │  │
│  └─────────────────────┴─────────────────────────────┘  │
│                                                         │
│  ANY signal fires → TRIGGER FOLLOW-UP (if turns left)  │
│  All clear → investigation is sufficient → finalize    │
└──────────┬─────────────────────────────┬────────────────┘
           │                             │
      SUFFICIENT                   INSUFFICIENT
           │                             │
           ▼                             ▼
┌──────────────────┐  ┌──────────────────────────────────┐
│ Finalize report  │  │ FOLLOW-UP TURN (turn 2 or 3)     │
│                  │  │                                  │
│                  │  │ Orchestrator injects:            │
│                  │  │ • Targeted question: "What       │
│                  │  │   specific failure mode could    │
│                  │  │   this introduce?"               │
│                  │  │ • Deeper context: file blame,    │
│                  │  │   extended history               │
│                  │  │ • Pressure: "Is this closer to   │
│                  │  │   LOW or HIGH? Commit."          │
│                  │  │                                  │
│                  │  │ Agent must re-reason with new    │
│                  │  │ context and produce updated      │
│                  │  │ assessment.                      │
│                  │  │                                  │
│                  │  │ → Back to quality gates          │
│                  │  │   (max 3 turns total)            │
└────────┬─────────┘  └──────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│ CLASSIFICATION RULE CHECK — Is the verdict consistent?  │
│                                                         │
│  Even after quality gates pass, the harness checks:     │
│                                                         │
│  • Agent identified a concrete failure mechanism        │
│    but said MEDIUM? → Architectural violation.          │
│    (Mechanism floor: SUPPORTED hypothesis = ≥ HIGH)     │
│                                                         │
│  • Agent used hedging language ("limited blast radius", │
│    "purely additive") to downgrade despite evidence?    │
│    → Prompt should have prevented this.                 │
│                                                         │
│  • Agent listed all touched files as localization       │
│    instead of narrowing to defect site?                 │
│    → Investigation quality is weak (D2 signal).         │
│                                                         │
│  These checks inform the IMPROVEMENT CYCLE —            │
│  they tell us where the method (prompt/context)         │
│  needs to change to produce better investigations.      │
└─────────────────────────────────────────────────────────┘
```

The harness is not a judge — it's a **quality control process**. It ensures the agent investigates thoroughly (not superficially), commits to a verdict (not hedges), and points to the actual defect site (not everything it read). When the investigation is insufficient, the harness forces the agent to go deeper. When it hits the turn cap and quality is still weak, that's a signal the *method itself* (prompt, context assembly, follow-up strategy) needs to change — which drives the improvement cycle.

### What the agent sees vs what it never sees

| Agent receives (investigation) | Agent never sees (eval-only) |
|-------------------------------|------------------------------|
| Unified diff | `buggy` label |
| Commit message | Fix commit / fix diff |
| Numeric features (allowlist) | JIRA issue details |
| File history (3 prior commits) | `fix`, `year`, `author_date` |
| Author stats (from train split) | Ground truth linkage |
| Router probability (ML prior) | Other commits' results |

### Constrained vs open agent loop

In a standard agent loop (LangGraph, Claude Code, Codex), the **LLM decides what to do next** — it chooses tools, decides when to stop, and steers the investigation autonomously. Our agent loop is **constrained by design**:

| Aspect | Open agent loop (industry default) | Our constrained loop |
|--------|-----------------------------------|---------------------|
| Who steers | LLM decides next action | Orchestrator decides |
| Tool selection | LLM picks from available tools | Pre-assembled context bundle (no runtime tool choice) |
| Turn count | LLM decides when done | Hard cap (max 3, default 1) |
| Follow-up triggers | LLM requests more info | Orchestrator checks signals (confidence, localization, uncertainty) |
| Stopping condition | LLM says "I'm done" | Budget, turn cap, or all quality gates pass |

**Why constrained:** Cost governance ($0.003/commit vs unbounded), reproducibility (same commit = same context = comparable results across method iterations), and the harness philosophy (LLM reasons, infrastructure controls).

**Trade-off acknowledged:** A pure agent loop might achieve higher D2/D3 by letting the agent request specific files or blame output. Currently deferred — D6=0.85 shows the pre-assembled bundle is sufficient for grounding. If D2 remains stuck after iter-4, reopening tool use is the escalation path.

### Model strategy (current V1)

| Phase | Model | Why | Cost/commit |
|-------|-------|-----|-------------|
| Investigation (turn 1) | `claude-sonnet-4-6` via Cursor SDK | Best reasoning quality available; single-turn means one shot matters | ~$0.003 |
| Follow-up (turns 2–3) | Same model | Not yet exercised in eval (max_turns=1 in run_eval) | ~$0.002/turn |
| D3/D5 judging | Same model | Shares provider instance — simpler, but potential self-evaluation bias | ~$0.001/judgment |
| Routing | XGBoost (no LLM) | Zero cost, handles ~60% of commits | $0 |

**Open design questions (for iter-1+):**

1. **Should the judge use a different model?** Same model judging its own reasoning may be biased ("everything looks reasonable to me"). A stronger or different model could be more adversarial. Counter: judge prompts are rubric-based with explicit scoring criteria — bias may be low.

2. **Should follow-up turns use a different model?** Turn 2–3 ask "commit to HIGH or LOW" — a simpler task that might work with a cheaper/faster model. Counter: reasoning quality matters most when the agent is uncertain.

3. **Should router-confirmed HIGH commits get a lighter touch?** The agent currently runs full investigation on HIGH-zone commits. A shorter confirmation prompt could save cost. Counter: not yet needed — HIGH zone is small.

4. **Is Cursor SDK mode=plan limiting?** It runs in read-only mode with no native tool calling. Full agent mode might enable richer investigation. Counter: D6=0.85 without tools suggests context assembly is sufficient.

### Multi-turn strategy (current V1)

| Config | Value | Rationale |
|--------|-------|-----------|
| `max_turns` (orchestrator default) | 3 | Designed capacity |
| `max_turns` (run_eval override) | 1 | Cost control during method iteration |
| Follow-up trigger: confidence | < 0.6 | Agent signals it isn't sure |
| Follow-up trigger: localization | empty | Agent couldn't identify defect site |
| Follow-up trigger: uncertainty | explicit in reasoning | Agent says "insufficient context" |
| Follow-up injection | Targeted question + deeper context | "What specific failure mode?" + blame/extended history |

**V1 default is single-turn.** Multi-turn is designed but untested in eval. The improvement cycle prioritizes prompt/method quality at turn 1 (iter-1, iter-2) before adding turns (iter-3).

**Escalation path:** If iter-2 (n=50) shows D1/D3 still below gate with best single-turn method, iter-3 enables turn 2 for low-confidence commits. Expected cost increase: ~40% (not all commits trigger follow-up).

---

## Investigation Method (Operational)

This section describes **how the harness enforces** the investigation method defined in [ARCHITECTURE.md §3](../ARCHITECTURE.md#3-the-investigation-method). Architectural identity (intent, four-stage model, design constraints) lives in ARCHITECTURE; here we cover enforcement signals, schema gates, and failure-mode diagnostics.

### Four-stage enforcement

The orchestrator enforces this arc during investigation (via prompt, schema, and quality gates). Evaluation scores how well each stage succeeded post-hoc. Quality signals show which dimensions catch a failed stage:

| Stage | Purpose | Quality signal |
|-------|---------|---------------|
| 1. Change summary | Scope the commit: files, intent, magnitude | Accurate file list, correct intent interpretation |
| 2. Defect hypotheses | Generate 2–3 mechanistic candidates: "If ⟨X⟩ then ⟨Y⟩ in ⟨Z⟩" | Specificity — not "could have bugs" but "missing null check on line 47 causes NPE when input is empty" |
| 3. Evidence triage | Mark each hypothesis SUPPORTED / REFUTED / UNVERIFIABLE with diff cites | Cites specific lines/hunks, not entire files |
| 4. Verdict | Risk level tied to supported evidence, not hedge language | Risk level is consistent with supported mechanism findings |

### Classification rule enforcement

The prompt and schema enforce four architectural rules (design rationale: [ARCHITECTURE §3.3](../ARCHITECTURE.md#33-classification-rules-design-level)):

- **Mechanism floor:** SUPPORTED hypothesis → risk ≥ HIGH. An agent that identifies a bug mechanism but says MEDIUM is architecturally wrong.
- **No hedge downgrade:** "Purely additive", "limited blast radius", "backward-compatible in intent" — these phrases must not lower risk when a mechanism is identified.
- **Localization = defect site:** `localization[]` means "where the bug probably lives", not "all files I read." Listing all touched files produces low D2 Jaccard scores.
- **ML prior ≠ oracle:** Router probability is a calibrated prior from change metrics. It tells the agent "the model thinks this commit is N% likely to be buggy" — a signal to investigate harder, not a label.

### Known failure modes (current baseline)

| Failure | D-impact | Root cause |
|---------|----------|-----------|
| MEDIUM on buggy commits | D1=0.40 | Agent defaults to MEDIUM without external rubric pressure |
| "Could contain bugs" reasoning | D3=0.13 | Describes structure, not failure mechanism |
| Localization = all touched files | D2=0.08 | No distinction between "analyzed" and "defective" |
| Hedging language overrides findings | D1, D3 | "Limited blast radius" downgrades even with mechanism evidence |

These failures drive the [improvement cycle](#improvement-cycle) — each has a corresponding method change in the iter-1 plan.

---

## Evaluation Framework

Evaluation exists to diagnose *how* the agent fails, not just *whether* it fails. A binary accuracy metric would miss the distinction between "correct prediction with no evidence" (D1 high, D6 low — guessing) and "wrong prediction despite perfect reasoning" (D1 low, D3 high — calibration failure).

### Six-dimension diagnostic panel

| ID | What it catches | Method | LLM cost |
|----|----------------|--------|----------|
| D1 Prediction | Wrong risk classification | Risk level vs buggy label | None |
| D2 Localization | Pointing to wrong files | Jaccard(agent files, fix files) | None |
| D3 Diagnosis | Generic reasoning that sounds good but says nothing | LLM-as-judge rubric 0–4 | Per commit |
| D4 Severity | Miscalibrated risk levels | Risk vs JIRA priority | None |
| D5 Recommendations | Disconnected or useless suggestions | LLM-as-judge rubric 0–3 | Per commit |
| D6 Evidence grounding | Boilerplate that cites no real artifacts | Claims vs actual diff/files | None |

Full rubrics: [docs/evaluation.md](evaluation.md).

### Dimension coupling (cross-reads)

No single dimension proves the system works. Cross-dimension patterns expose specific failure modes:

| Pattern | What it reveals |
|---------|----------------|
| D6 ↑ + D3 ↓ | Cites real files, describes structure — but misses the failure mechanism |
| D3 ↑ + D1 ↓ | Identifies the bug mechanism but won't commit to HIGH classification |
| D1 ↑ + D6 ↓ | Guessing — correct classification with no supporting evidence |
| D2 ↓ + D3 ↓ | Can't distinguish "files changed" from "files containing the defect" |

The current baseline (D6=0.85, D3=0.13) is the first pattern: *describes structure, not failure*. This tells us the method needs mechanism-forcing rules, not more context.

### Gate rules

- **All six must pass** simultaneously on n≥50, stratified 50/50 buggy/clean
- **Regression guard:** D6 drop below 0.70 → revert the method change
- **Baseline soft constraint:** Agent D1 < always-predict-clean → WARNING (not blocking)
- Thresholds: [docs/evaluation.md](evaluation.md) | [`.harness/state.json`](../.harness/state.json)

### What to watch per change type

| Method change | Watch dimensions |
|---------------|-----------------|
| Classification rubric | D1, D3, D6 (guard) |
| Localization prompt | D2, D6 |
| Router probability injection | D1, D4 |
| Few-shot examples | D3, D5, D6 (parroting check) |
| Multi-turn follow-up | D1, D3 (should improve), cost (should stay bounded) |

### Per-commit forensics

Every eval run produces per-commit artifacts for failure analysis:

```
output/runs/YYYY-MM-DD_HH-MM-SS_real_nNN/
├── investigations/         # What the agent produced
│   └── <hash>_<project>.json
├── evaluations/            # How it scored vs ground truth
│   └── <hash>_<project>.json
├── eval-report.json        # Aggregate D1–D6
└── eval-report.md          # Human-readable summary
```

When a dimension drops, the fix process starts by reading individual `evaluations/` JSONs to identify *which commits* regressed and *why*.

---

## Improvement Cycle

The improvement cycle is the system's evolution mechanism. It is not ad-hoc iteration — it is a disciplined protocol that ensures every change is measured and reversible.

### Steps

| # | Action | Artifact | Typical cost/time |
|---|--------|----------|-------------------|
| 1 | **Hypothesize:** which dimension, which method change | Breadcrumb note | — |
| 2 | **Implement:** prompt/context/turn change (method only) | Code diff in `orchestrator.py` | — |
| 3 | **Smoke:** n=5 stratified, catch regressions early | `output/runs/..._real_n5/` | ~4 min, ~$0.02 |
| 4 | **Validate:** n=20 stratified, measure all six dimensions | `eval-report.json` | ~20 min, ~$0.07 |
| 5 | **Compare:** per-commit JSONs vs baseline, check gate trajectory | Updated baseline | — |
| 6 | **Decide:** trending toward gate → n=50 for confidence; flat → pivot | State update | ~1 hr, ~$0.35 |

### Hard constraints (every iteration)

- Oracle isolation holds: agent never sees `buggy`, `fix`, `year`, `author_date`, JIRA
- 76+ tests pass after every change
- D6 ≥ 0.70 — grounding regression = immediate revert
- No infrastructure changes unless they directly unblock a metric
- Each iteration tracked as a breadcrumb with before/after scores

### When to pivot vs persist

| Signal | Action |
|--------|--------|
| n=20 shows ≥0.05 improvement on target dimension | Persist → n=50 validation |
| n=20 flat or D6 regresses | Revert → different hypothesis |
| n=50 passes all gates | Declare iteration complete |
| n=50 fails one gate despite improvement | Continue with next iter targeting that gate |

### Current phase roadmap

| Task | Focus | Depends on | Status |
|------|-------|-----------|--------|
| spike-0 | Investigation harness design (these docs) | — | Complete |
| iter-1 | A+B hybrid: risk rubric + staged CoT + router probability | spike-0 | Pending |
| iter-2 | Validate at n=50 | iter-1 | Pending |
| iter-3 | Multi-turn for low-confidence commits | iter-2 | Pending |
| iter-4 | D2 localization focus | iter-1 | Pending |

Research backing for the A+B approach: [docs/spike-investigation-harness.md](spike-investigation-harness.md).

---

## Why a Harness?

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

---

## Control Plane

The control plane is the deterministic infrastructure that implements the design above.

### 1. Routing — who gets investigated

Not every commit needs an LLM. An XGBoost router trained on numeric features (lines added, files touched, author experience, entropy) classifies every commit at zero cost:

- **P < 0.3** → SAFE. Skip the agent entirely.
- **0.3 ≤ P ≤ 0.7** → INVESTIGATE. Full agent loop.
- **P > 0.7** → HIGH. Flag directly; optional light LLM confirmation.

The router handles ~60% of commits without spending a single token. The agent focuses on the gray zone where classification alone is insufficient and investigation adds value.

### 2. Context construction — what the agent sees

The agent doesn't choose what to read. The `CommitContextBuilder` assembles a fixed context bundle:

| Context piece | Source | Why |
|--------------|--------|-----|
| Unified diff | Local git clone | What changed in the code |
| Commit message | Local git clone | What the author intended |
| Touched files | Local git clone | Scope of the change |
| Numeric features | ApacheJIT CSV (allowlist only) | Quantitative change metrics |
| File history (last 3 commits) | Local git clone | Recent activity in changed files |
| Author stats | Precomputed from train split | Author experience in this project |
| Router probability | XGBoost output | ML prior — "how risky does the model think this is?" |

This bundle is deterministic — same commit, same context, every time. No future information leaks into the investigation.

### 3. Turn governance — how much the agent can do

The `AgentOrchestrator` enforces hard limits:

- **Max 3 turns** per commit (V1 default: 1 turn for cost efficiency)
- **50K token budget** per investigation
- **$0.50 cost cap** per commit
- **Follow-up triggers are deterministic**: low confidence, missing localization, explicit uncertainty

The orchestrator — not the LLM — decides when to stop.

### 4. Schema validation — what the agent must output

The agent must produce a `CommitInvestigationReport` (Pydantic-validated) with:

- Risk level (LOW/MEDIUM/HIGH/CRITICAL) + confidence (0–1)
- At least one evidence item (enforced — empty reports rejected)
- Localization claims (file + lines + rationale)
- Findings, recommendations, tools used, turn count

If the LLM returns malformed JSON, the orchestrator extracts what it can and fills defaults.

### 5. Error resilience — what happens when things break

| Failure | Harness behavior |
|---------|-----------------|
| LLM API timeout | Return LOW risk with confidence=0 and error in metadata |
| Malformed JSON | Extract partial JSON, fill defaults, log warning |
| Commit not in clone | Skip with reason, don't crash the run |
| JIRA API error (eval) | Score D3/D4/D5 as 0 with error note, continue |
| Budget exceeded | Stop investigation, assemble report from available data |

No single failure stops a run.

### 6. Cost governance — budget tiers

| Tier | Budget | Commits | Use case |
|------|--------|---------|----------|
| Smoke | $10 | ~50 | CI/quick validation |
| Standard | $50 | ~300 | Default eval run |
| Deep | $100 | ~1000 | Manual deep analysis |

---

## Industry References

Patterns and tools relevant to our agent loop, evaluation, and context management:

### Agent loop architecture
- **ReAct** (Yao et al. 2022) — the foundational perceive-reason-act-observe pattern. Our loop is a constrained ReAct variant.
- **Anthropic "Building Effective Agents"** — agents (open-ended loops) vs workflows (predetermined sequences). We are a workflow with agent reasoning inside.
- **Claude Code Agent SDK** — canonical implementation of the agentic loop with turn-based tool execution.

### Prompt/method management
- **DSPy** — programmatic prompt optimization. Relevant pattern: treat optimized prompts as versioned, diffable artifacts (not hand-crafted strings). MIPROv2 for joint instruction + few-shot optimization against a metric.
- **Braintrust / Humanloop** — prompt versioning, A/B testing, experiment tracking. Our timestamped runs serve a similar purpose.
- **MLflow + DSPy** — optimization tracking with parent/child runs. Each iteration in our improvement cycle is analogous to a child run.

### LLM-as-judge / rubric management
- **Braintrust scorers** — rubric scoring, pairwise ranking, pass/fail. Pattern: deterministic scorers for format/fact, LLM-as-judge for subjective quality. We follow this: D1/D2/D4/D6 deterministic, D3/D5 LLM-as-judge.
- **Multi-judge voting** — aggregates scores from multiple models. Relevant to EXP-JUDGE-SWAP (self-evaluation bias mitigation).
- **Span-level scoring** — score individual steps, not just final output. Relevant to multi-turn attribution.
- **Calibration against human ground truth** — aiming for 75–90% alignment. Pending for our system (audit protocol in evaluation.md).

### Context engineering
- **Dynamic context assembly** (Zylos Research 2026) — context window as a projection, not storage. Our `CommitContextBuilder` IS a context assembler: budget-aware, deterministic, stable prefix for cache efficiency.
- **Pinned + Retrieved architecture** — system prompt (stable, cacheable) + per-commit context (dynamic). Our architecture already implements this pattern.
- **Context gap forensics** — when failures come from missing context vs bad reasoning. Relevant to EXP-FORENSICS-TAG and the tool-use debate.

### Not yet adopted (watch list)
- **DSPy MIPROv2 optimization** — automated few-shot selection. Could replace manual few-shot curation in iter-1.
- **Prefix caching** — our system prompt is stable across commits; Cursor SDK may already benefit from this but we don't measure it.
- **Mem0 / persistent memory** — cross-session learning. Not applicable to our batch eval loop (no runtime memory needed) but relevant if this becomes a live service.

---

## Related

- [ARCHITECTURE.md](../ARCHITECTURE.md) — system identity, design philosophy, trust boundaries
- [evaluation.md](evaluation.md) — D1–D6 rubrics, acceptance thresholds, run results
- [experiment-context.md](experiment-context.md) — research thesis and oracle isolation rationale
- [spike-investigation-harness.md](spike-investigation-harness.md) — research spike: failure analysis, external approaches, iter-1 recommendation
- [datasets.md](datasets.md) — ApacheJIT ground truth chain
