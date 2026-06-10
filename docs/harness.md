# The Harness

The harness is the deterministic infrastructure around the [agent loop](agent-loop.md) — routing, budget, schema validation, error recovery, artifact persistence. It does not reason; it ensures the agent loop runs reliably at scale.

## Definitions

Five concepts operate in this system. The industry often conflates "harness" with "agent loop." In this project, they are distinct.

| Concept | What it is | When it runs | Sees ground truth? |
|---------|-----------|--------------|-------------------|
| **Harness** | Deterministic infrastructure: routing, budget caps, schema enforcement, error recovery, artifact persistence, cost tracking. Does not change between iterations. | Always — wraps every investigation | **Never.** |
| **Agent loop** | The investigation pipeline: context assembled → archetype detected → LLM generates hypotheses → Script tiers evidence → Script computes risk → gate checks quality → report assembled. Runs inside the harness. | At investigation time — once per commit | **Never.** |
| **Investigation method** | The *configuration* of the agent loop: which hypothesis prompt, which model, turn policy, context strategy. This is what we iterate between eval runs. | Defined before a run; fixed during the run | **Never.** |
| **Evaluation** | Post-hoc measurement of agent loop output against ground truth across six dimensions. | After a run completes — batch scoring | **Yes.** Full oracle access. |
| **Improvement cycle** | Uses evaluation results to change the investigation method. Human-driven. | Between runs | **Indirectly** — reads eval reports. |

### Why the separation matters

The agent loop never knows whether its output will be evaluated. It never sees ground truth. The harness enforces structural constraints (budget, schema, error recovery) while the investigation method (inside the agent loop) determines reasoning quality through prompt design, context strategy, and Script policy.

Evaluation is a *diagnostic tool for us*. If D3 is low, the pipeline's hypothesis stage needs to force more mechanistic output. If D1 is low, risk_policy.py needs a tighter archetype cap. The eval doesn't fix the agent loop — we fix the investigation method based on what eval reveals.

### Other terms

| Term | Definition |
|------|-----------|
| **Agent** | The LLM call inside Stage 1 (Hypothesis Generation). Generates mechanism text and evidence quotes over assembled context. |
| **Orchestrator** | The deterministic coordinator that runs the pipeline: assembles context, calls Stage 1 LLM, passes artifacts through Script stages, enforces budget, assembles the report. Part of the harness. |
| **Ground truth** | Oracle data (ApacheJIT buggy labels, fix-commit diffs, JIRA tickets). Used exclusively by evaluation. |
| **Router** | XGBoost model that decides which commits enter the agent loop. Pre-harness, zero LLM cost. |

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

- **Agent loop (inside harness):** Runs the investigation pipeline. Produces a `CommitInvestigationReport`. No ground truth involved. See [agent-loop.md](agent-loop.md).
- **Eval:** Measures agent output against ground truth (D1–D6). See [evaluation.md](evaluation.md).
- **Improve:** Uses eval diagnosis to change the investigation method (hypothesis prompt, context, policy rules). Then re-runs to verify improvement.

---

## Why a Harness?

An LLM call is non-deterministic, expensive, slow, and has no memory. An agent that runs on 100 commits needs to survive all of those properties without human intervention.

| Problem | What happens without a harness |
|---------|-------------------------------|
| Cost blowup | $15+ for 100 commits, most obviously safe |
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
- **0.3 ≤ P ≤ 0.7** → INVESTIGATE. Full pipeline.
- **P > 0.7** → HIGH. Flag directly; optional light LLM confirmation.

The router handles ~60% of commits without spending a single token.

### 2. Context construction — what the agent sees

The `CommitContextBuilder` assembles a deterministic context bundle. **Today (iter-2):** linear diff with 16K char cap; `file_histories` and `author_stats` are collected in `InvestigationContext` but not injected into the LLM message. **Target (iter-3c/3d):** smart per-file diff prioritization + bundle injection or `missing_reasons[]` logging.

| Context piece | Source | Status |
|--------------|--------|--------|
| Unified diff (16K linear cap) | Local git clone | **Active** |
| Unified diff (smart-prioritized) | Local git clone | **Pending (iter-3d)** |
| Commit message | Local git clone | **Active** |
| Touched files | Local git clone | **Active** |
| Numeric features (allowlist only) | ApacheJIT CSV | **Active** |
| File history (last 3 commits) | Local git clone | Collected; **injection pending (iter-3c)** |
| Author stats | Precomputed from train split | Collected; **injection pending (iter-3c)** |
| Router probability | XGBoost output | **Active** |
| truncation_metadata | Context builder | **Pending (iter-3d)** |

When iter-3c ships: if file_histories or author_stats are unavailable, `missing_reasons[]` is populated with the gap and its impact. No silent omissions.

### 3. Turn governance — how much the agent can do

The `AgentOrchestrator` enforces hard limits:

- **Max turns: 1 (frozen in eval)** — `run_eval.py` hard-codes `max_turns=1`; CLI/orchestrator default remains 3 until iter-3b enforces globally. Multi-turn execution frozen until iter-3f A/B (see [agent-loop.md Multi-Turn Policy](agent-loop.md#multi-turn-policy))
- **50K token budget** per investigation
- **$0.50 cost cap** per commit
- **Follow-up triggers are deterministic:** `InvestigationQualityGate` — not LLM self-report

See [agent-loop.md](agent-loop.md) Multi-Turn Policy for reactivation criteria.

### 4. Schema validation — what the pipeline must output

Each pipeline stage has its own Pydantic schema:

| Schema | Stage | Enforced fields |
|--------|-------|-----------------|
| `HypothesisArtifact` | Stage 1 (LLM) | summary, hypotheses[{mechanism, evidence_quote}]; ≥1 hypothesis required |
| `TaggedHypothesis` | Stage 2 (Script) | tier (SUPPORTED/SPECULATIVE/REFUTED/UNVERIFIABLE), evidence_quote |
| `PolicyVerdict` | Stage 3 (Script) | risk_level, cap_applied, cap_reason, applied_rules[], supported_count |
| `GateResult` | Gate (Script) | follow_up_needed, signals[], reason |
| `CommitInvestigationReport` | Assembly | All above merged + per_stage metadata + localization + findings |

`HypothesisArtifact` schema failure → retry LLM with error message (max 2). `CommitInvestigationReport` assembly failure → `InvalidInvestigationResponseError`, logged, commit skipped.

### 5. Error resilience — what happens when things break

| Failure | Harness behavior |
|---------|-----------------|
| LLM API timeout | Return LOW risk with confidence=0 and error in metadata |
| HypothesisArtifact schema invalid | Retry with validation error feedback (max 2); if still invalid → degraded report |
| Commit not in clone | Skip with reason, don't crash the run |
| JIRA API error (eval) | D3/D4/D5 scored with `judge_oracle=unavailable` note; see FIX-JUDGE-INFRA |
| Budget exceeded | Stop investigation, assemble report from available data |
| Empty findings masked | `InvestigationQualityGate` fires; default "Investigation completed" placeholder → `InvalidInvestigationResponseError` |

### 6. Cost governance — budget tiers

| Tier | Budget | Commits | Use case |
|------|--------|---------|----------|
| Smoke | $10 | ~50 | CI/quick validation |
| Standard | $50 | ~300 | Default eval run |
| Deep | $100 | ~1000 | Manual deep analysis |

---

## Investigation Method

The investigation method is the **iter-3 design target**: five Script/LLM pipeline components that replace the iter-2 monolithic prompt. Implementation ships iter-3a–3e; iter-2 code still uses `INVESTIGATION_SYSTEM_PROMPT` with 4 in-prompt stages.

| Component | Role | Module | Ships in |
|-----------|------|--------|----------|
| Archetype detection | Clean-commit patterns + defect signals | `archetype.py` | iter-3a-extract |
| Hypothesis generation | Mechanism + evidence_quote pairs | `HypothesisEngine` (LLM) | iter-3e-decompose-prompt |
| Evidence tiering | SUPPORTED/SPECULATIVE/REFUTED/UNVERIFIABLE | `evidence_tagger.py` (Script-first + LLM escalation) | iter-3b-wire-gates (after feasibility spike) |
| Risk policy | Single source of `risk_level` | `risk_policy.py` | iter-3a-extract (wired iter-3b) |
| Quality gate | Deterministic follow-up signals | `quality_gate.py` | iter-3a-extract stub → iter-3b wire |

Context assembly (Stage 0) and report assembly are harness responsibilities, not investigation-method components.

## Investigation Pipeline (Design Target)

This section describes **how the harness will enforce** the investigation method once iter-3 ships. See [agent-loop.md](agent-loop.md) for the stage diagram and [architecture.md §7](architecture.md#7-implementation-map) for current vs pending modules.

### Pipeline stages and enforcement

| Stage | Component | Purpose | Quality signal |
|-------|-----------|---------|---------------|
| 0 — Context Assembly | `CommitContextBuilder` | Smart-diff bundle + file_histories + author_stats | truncation_metadata accurate |
| 0b — Archetype Detection | `archetype.py` | Identify clean-commit archetype + defect signals | Signal present/absent |
| 1 — Hypothesis Generation | `HypothesisEngine` (LLM) | Mechanism + evidence_quote per hypothesis | ≥1 hypothesis; evidence_quote non-empty |
| 2 — Evidence Tiering | `evidence_tagger.py` (Script-first) | Tag hypotheses SUPPORTED/SPECULATIVE/REFUTED/UNVERIFIABLE | Tiers assigned; no blanket UNVERIFIABLE |
| 3 — Risk Policy | `risk_policy.py` (Script) | Single source of risk_level + cap + rules | PolicyVerdict audit trail in report |
| Gate | `quality_gate.py` (Script) | Deterministic follow-up trigger | follow_up_needed based on signals, not confidence |
| Assembly | Orchestrator | Merge artifacts → CommitInvestigationReport | Schema valid; cap_reason present when cap_applied |

### Risk policy — single source of truth

`risk_policy.evaluate_risk()` is the **only place** risk_level is computed. It is a pure function with no side effects. Input/output is fully deterministic.

```
archetype (from Stage 0b)
  + TaggedHypothesis[] (from Stage 2)
  + router_prior (from context)
  + production_defect_signals (from Stage 0b)
  + RiskPolicyContext{touched_files, localization, diff_metadata, ...}
  →
PolicyVerdict {
  risk_level: LOW | MEDIUM | HIGH | CRITICAL
  cap_applied: bool
  cap_reason: str | None         ← "clean_archetype_version_bump" etc.
  applied_rules: list[str]       ← audit trail
  supported_count: int
}
```

**Prohibited:** risk logic in the LLM prompt; post-hoc regex overrides; any code outside `risk_policy.py` setting `risk_level`.

### Classification rule enforcement

All classification rules live exclusively in `risk_policy.py`:

- **Mechanism floor:** SUPPORTED hypothesis + not capped → risk ≥ HIGH
- **Clean archetype cap:** clean archetype + no production defect signals → risk ≤ MEDIUM (regardless of router prior)
- **Speculative cap:** all-SPECULATIVE + no production defect signals → risk ≤ MEDIUM
- **Router prior:** `router_prior ≥ 0.70` contributes to HIGH **only when not capped**
- **Localization semantics:** `localization[]` = defect locus, not all touched files (D2 Jaccard penalizes extras)

### Known failure modes

| Failure | D-impact | Root cause | Status |
|---------|----------|-----------|--------|
| False positives on clean commits | D1 | Agent generates SUPPORTED hypotheses on speculative impact | Mitigated in iter-2b via prompt rubric + `_apply_clean_commit_risk_cap()`; migrates to `risk_policy.py` in iter-3a/3b. 3 stubborn FPs remain |
| Truncation hides defect file | D3 | 572f3cee35fe: linear diff hides XmppGroupChatProducer.java | Open — iter-3d smart-diff |
| Wrong-mechanism diagnosis | D3 | Agent identifies bug A, not bug B (409664582f53) | Open — iter-2d wrong-mechanism prompt |
| Empty JIRA → D3=0 | D3 | Judge infra, not agent failure (f897d46870ba) | Open — FIX-JUDGE-INFRA |
| Localization = all touched files | D2 | No distinction between "analyzed" and "defective" | Open — post-iter-3 |

---

## Improvement Cycle

The improvement cycle is the system's evolution mechanism — disciplined, measured, reversible.

### Steps

| # | Action | Artifact | Typical cost/time |
|---|--------|----------|-------------------|
| 1 | **Hypothesize:** which dimension, which pipeline change | Breadcrumb note | — |
| 2 | **Implement:** method change (prompt, Script policy, context) | Code diff | — |
| 3 | **Smoke:** n=5 stratified, catch regressions early | `output/runs/..._real_n5/` | ~5 min, ~$0.02 |
| 4 | **Validate:** n=20 stratified, measure all six dimensions | `eval-report.json` | ~24 min, ~$0.10 |
| 5 | **Compare:** per-commit JSONs vs baseline, check gate trajectory | Updated baseline | — |
| 6 | **Decide:** trending toward gate → n=50 for confidence; flat → pivot | State update | ~1 hr, ~$0.35 |

### Hard constraints (every iteration)

- Oracle isolation holds: agent never sees `buggy`, `fix`, `year`, `author_date`, JIRA
- 89+ tests pass after every change
- D6 ≥ 0.70 — grounding regression = immediate revert
- Each iteration tracked as a breadcrumb with before/after scores
- EXP-JUDGE-SWAP decision applied before any n=20 D3/D5 claims

### Phase roadmap

| Task | Focus | Status |
|------|-------|--------|
| spike-0 | Investigation harness design | **Complete** |
| iter-1 | A+B hybrid: risk rubric + staged CoT + router probability | **Committed** (D1=0.60, D3=0.20) |
| EXP-FORENSICS-TAG | Classify D3 failure modes from iter-1 | **Complete** |
| iter-2a+b | 16K diff + dual-path clean-commit rubric | **Committed** (D1=0.75, panel n=12) |
| iter-2-n20 | n=20 iteration gate on iter-2 codebase | **Pending** |
| FIX-JUDGE-INFRA | D3 JIRA fallback for empty descriptions | **Pending (parallel)** |
| EXP-JUDGE-SWAP | Cross-model judge validation | **Pending (parallel)** |
| iter-3a-extract | Extract archetype.py + risk_policy.py + quality_gate stub (no evidence_tagger) | **Pending** |
| iter-3a-feasibility-evidence-tagger | Spike: Script tier tagging ≥80% panel agreement | **Pending** |
| iter-3b-wire-gates | Wire gates + evidence_tagger.py + cap_reason schema | **Pending** |
| iter-3c-bundle-inject | File_histories + author_stats injected | **Pending** |
| iter-3d-smart-diff | Per-file diff prioritization | **Pending** |
| iter-3e-decompose-prompt | HypothesisEngine stage + remove monolith | **Pending** |
| iter-3-validate | Regression panel + n=5 smoke after redesign | **Pending** |
| iter-3-n20 | n=20 gate on redesigned pipeline | **Pending** |

---

## Related

- [agent-loop.md](agent-loop.md) — investigation pipeline: stages, validation, quality gates, model strategy
- [architecture.md](architecture.md) — system identity, design philosophy, trust boundaries
- [evaluation.md](evaluation.md) — D1–D6 rubrics, acceptance thresholds, run results
- [datasets.md](datasets.md) — ApacheJIT ground truth chain
- [experiment-context.md](experiment-context.md) — research thesis, oracle isolation rationale
