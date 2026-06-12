# Agent Loop

The agent loop is the investigation process running inside the [harness](harness.md). Industry-standard term: Perceive → Reason → Act → Observe. The harness controls infrastructure; the agent loop produces the investigation.

## Implementation status

All pipeline stages described in this document are **fully implemented** as of V1 delivery (2026-06-11). The five-stage Script/LLM pipeline replaced the iter-2 monolithic prompt. See [architecture.md §7](architecture.md#7-implementation-map) for the component map.

## How a Commit Gets Investigated

```
Commit enters INVESTIGATE zone (0.3 ≤ P ≤ 0.7)
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ STAGE 0 — CONTEXT ASSEMBLY (Script)                     │
│                                                         │
│  CommitContextBuilder assembles:                        │
│  • Unified diff — smart per-file priority budget:       │
│    defect-signal files first; ≥1 hunk/file before cap  │
│  • Commit message + touched files                       │
│  • Numeric features (allowlist: LA, LD, NF, ND, etc.)  │
│  • File history (last 3 commits, injected)              │
│  • Author stats (injected or gap logged)                │
│  • Bundle expansion (test-adjacency + blame snippets    │
│    for allowlisted missing-context commits)             │
│  • Router probability (ML prior)                        │
│  • truncation_metadata + missing_reasons[]              │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│ STAGE 0b — DETERMINISTIC PRE-ANALYSIS (Script)          │
│                                                         │
│  archetype.detect(diff, commit_msg) →                   │
│    "version_bump" | "label_rename" | "type_migration"   │
│    | "comment_only" | "pure_refactor" | "AMBIGUOUS"     │
│                                                         │
│  AMBIGUOUS = no clean-commit pattern matched AND        │
│    has_production_defect_signals() == False             │
│                                                         │
│  archetype.has_production_defect_signals(diff) → bool   │
│    True when: guard removal, lifecycle reordering,      │
│    inverted condition, removed null check detected      │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│ STAGE 1 — HYPOTHESIS GENERATION (LLM Call 1)            │
│                                                         │
│  HypothesisEngine receives:                             │
│    • Context bundle (diff, message, histories, stats)   │
│    • archetype_label + defect_signals from Stage 0b     │
│    • H3a RAG context (historical defect distribution    │
│      from ApacheJIT KNN — when --enable-h3a-rag)        │
│                                                         │
│  LLM produces: HypothesisArtifact (Pydantic-validated)  │
│    • summary — user-visible symptom + primary fix-file  │
│    • hypotheses[] — each with:                          │
│        mechanism: "If <X> then <Y> in <Z>"              │
│        evidence_quote: substring of diff (required)     │
│                                                         │
│  Composite selector (select_primary_by_evidence):       │
│    H1 anchor + citation score + production-file rank    │
│    → promotes best-grounded hypothesis to primary       │
│                                                         │
│  Schema failure → retry with error feedback (max 2)     │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│ STAGE 2 — EVIDENCE TIERING (Script-first)               │
│                                                         │
│  evidence_tagger.tag(hypothesis, diff) →                │
│    TaggedHypothesis{tier, evidence_quote}               │
│                                                         │
│  Tier assignment (Script patterns):                     │
│    SUPPORTED — diff shows guard removal, inverted       │
│      condition, wrong default, lifecycle change at      │
│      a call site (deterministic pattern match)          │
│    SPECULATIVE — cross-version/theoretical impact       │
│      not shown in diff (assumed external behavior)      │
│    REFUTED — diff shows the guard/check exists          │
│    UNVERIFIABLE — diff truncated or file not visible    │
│                                                         │
│  Ambiguous cases → LLM escalation with blind rubric     │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│ STAGE 3 — RISK POLICY (Script — single source)          │
│                                                         │
│  risk_policy.evaluate_risk(                             │
│    archetype, hypotheses, router_prior,                 │
│    production_defect_signals, context)                  │
│  → PolicyVerdict {                                      │
│      risk_level, cap_applied, cap_reason,               │
│      applied_rules[], supported_count }                 │
│                                                         │
│  Rules (in priority order):                             │
│    CRITICAL: credential/injection/data-loss             │
│    HIGH:   ≥1 SUPPORTED hypothesis (not capped)         │
│            API/binary incompatibility + defect signal   │
│            router_prior ≥ 0.70 + not capped             │
│            security change without validation           │
│            >200 new production lines, no tests          │
│    CAP→MEDIUM: clean archetype + no defect signals      │
│                all-SPECULATIVE + no defect signals      │
│    LOW:    no mechanism identifiable                    │
│                                                         │
│  Zero risk logic in LLM prompt. Zero post-hoc override. │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│ GATE — InvestigationQualityGate (Script — deterministic)│
│                                                         │
│  Signals follow-up required (GateResult) when ANY of:   │
│    • supported_count == 0 AND defect_signals == True    │
│    • localization == [] AND diff_truncated AND          │
│       archetype == AMBIGUOUS                            │
│    • HypothesisArtifact schema validation failed        │
│    • findings == ["Investigation completed"]            │
│      (masked empty output)                              │
│                                                         │
│  LLM output field follow_up_needed: REMOVED.            │
│  Confidence-based trigger deferred (post-V2 spike).     │
│                                                         │
│  Gate PASS → assemble report                            │
│  Gate FAIL + turns remaining → inject follow-up context │
└──────────────┬──────────────────────────┬───────────────┘
               │ PASS                     │ FAIL + turn ≤ max_turns
               ▼                          ▼
┌──────────────────────┐  ┌───────────────────────────────────────┐
│ ASSEMBLY (Script)    │  │ FOLLOW-UP TURN (FROZEN until iter-3f) │
│                      │  │                                       │
│  Merge into          │  │  max_turns=1 everywhere until A/B     │
│  CommitInvestigation │  │  evidence proves ΔD3 ≥ 0.25 on hard  │
│  Report:             │  │  subset. See Multi-Turn Policy below. │
│  • PolicyVerdict     │  │                                       │
│  • HypothesisArtifact│  │  When active (future iter-3f):        │
│  • cap_reason (if    │  │  • smart-diff of truncated files      │
│     cap_applied)     │  │  • file_history blame snippets        │
│  • applied_rules[]   │  │  • targeted question per gate signal  │
│  • per_stage metadata│  └───────────────────────────────────────┘
└──────────────────────┘
```

## Stage Responsibilities

| Stage | Tier | Responsibility | LLM? |
|-------|------|----------------|------|
| Stage | Tier | Responsibility | LLM? |
|-------|------|----------------|------|
| 0 — Context Assembly | Script | Build diff bundle; inject file_histories + author_stats + bundle_expand | No |
| 0b — Archetype | Script | Detect clean-commit patterns, production defect signals | No |
| 1 — Hypothesis Generation | **LLM** | Produce mechanism + evidence_quote pairs; composite selector | **Yes (Call 1)** |
| 2 — Evidence Tiering | Script-first | Tag each hypothesis SUPPORTED/SPECULATIVE/REFUTED/UNVERIFIABLE | No (LLM escalation for ambiguous) |
| 3 — Risk Policy | Script | Compute risk_level with single source of truth | No |
| Gate | Script | Deterministic follow-up trigger | No |
| Assembly | Script | Merge artifacts → CommitInvestigationReport; SUPPORTED-only localization | No |

**Default path: 1 LLM call.** Multi-turn deferred — iter-3f A/B showed ΔD3 < threshold.

## Quality Gate — Trigger Conditions

The `InvestigationQualityGate` fires deterministically based on `HypothesisArtifact` output, not on LLM self-report.

| Trigger | Condition | Why it fires |
|---------|-----------|-------------|
| `no_supported_with_defect_signals` | `supported_count == 0 AND defect_signals == True` | Agent failed to identify mechanism despite structural signals present |
| `empty_localization_truncated_ambiguous` | `localization == [] AND diff_truncated AND archetype == AMBIGUOUS` | Missing context prevents localization |
| `schema_failure` | `HypothesisArtifact` Pydantic validation failed | Output unusable |
| `empty_findings_masked` | `findings == ["Investigation completed"]` | Default placeholder — investigation didn't run |

**Planned (V2):** confidence-based trigger using 7-signal confidence equation (`spike-investigation-confidence-equation`). Not yet implemented.

**Error paths:** LLM API timeout, schema retry exhaustion, and budget exceeded are handled by the harness — see [harness.md Error resilience](harness.md#5-error-resilience--what-happens-when-things-break). The pipeline diagram above shows the happy path only.

## Constrained vs Open Agent Loop

In a standard agent loop (LangGraph, Claude Code, Codex), the **LLM decides what to do next**. Our agent loop is **constrained by design**:

| Aspect | Open agent loop (industry default) | Our constrained loop |
|--------|-----------------------------------|---------------------|
| Who steers | LLM decides next action | Orchestrator decides |
| Tool selection | LLM picks from available tools | Pre-assembled context bundle (no runtime tool choice) |
| Turn count | LLM decides when done | Hard cap (max 1, frozen; design supports 3) |
| Follow-up triggers | LLM requests more info | `InvestigationQualityGate` checks deterministic signals |
| Risk classification | LLM reasons to verdict | `risk_policy.evaluate_risk()` — Script function |
| Stopping condition | LLM says "I'm done" | Budget, turn cap, or gate passes |

**Why constrained:** Cost governance, reproducibility (same commit → same context → comparable results across iterations), and harness philosophy (LLM reasons about mechanisms; infrastructure controls decisions).

## What the Agent Sees vs What It Never Sees

| Agent receives (investigation) | Agent never sees (eval-only) |
|-------------------------------|------------------------------|
| Unified diff (smart per-file prioritized) | `buggy` label |
| Commit message | Fix commit / fix diff |
| Numeric features (allowlist) | JIRA issue details |
| File history (last 3 commits, injected) | `fix`, `year`, `author_date` |
| Author stats (injected or gap logged) | Ground truth linkage |
| Bundle expansion (test-adjacency + blame) | Other commits' results |
| Router probability (ML prior) | Risk rubric HIGH/MEDIUM/LOW |
| archetype_label (from Stage 0b) | PolicyVerdict rationale |
| truncation_metadata + missing_reasons[] | — |

*Note: archetype_label from Stage 0b is injected as context fact ("PRIMARY change: version_bump"), not as a risk verdict.*

## Model Strategy

| Phase | Model | Why | Cost/commit |
|-------|-------|-----|-------------|
| Stage 1 — Hypothesis Generation | `claude-sonnet-4-6` via Cursor SDK | Best mechanism extraction; single focused task | ~$0.004 |
| Stage 2 — Evidence Tiering LLM escalation | Same model (blind rubric) | Ambiguous cases only; rubric stripped so context-blind | ~$0.001 (rare) |
| D3/D5 judging (eval) | **Different model** (EXP-JUDGE-SWAP decision) | Same model judging own output = self-evaluation anti-pattern | ~$0.001/judgment |
| Routing | XGBoost (no LLM) | Zero cost, handles ~60% of commits | $0 |

## Multi-Turn Policy

**Status: DEFERRED.** Multi-turn A/B (iter-3f) ran and showed ΔD3 < threshold — single-turn maintained. `TurnCheckpoint` infrastructure preserved for future re-evaluation.

| Config | Value | Rationale |
|--------|-------|-----------|
| `max_turns` (eval path) | **1** | `run_eval.py` hard-codes 1. Primary iteration gate. |
| `max_turns` (CLI default) | 3 | `investigate.py` / orchestrator constructor default — frozen at 1 in eval only until iter-3b enforces globally. |
| `max_turns` (design capacity) | 3 | Infrastructure preserved; no-op beyond turn 1 while frozen. |
| Follow-up trigger | `InvestigationQualityGate` | Deterministic, not LLM self-report |
| Follow-up content (when active) | smart-diff of hidden files + file_history blame + targeted question per gate signal | Not generic "continue investigating" |

**Re-activation threshold (future):** ALL THREE conditions must hold:
1. `InvestigationQualityGate` in place (done)
2. A/B on ≥3 hard commits shows ΔD3 ≥ 0.25 OR gate confirms `missing_context` in ≥2 commits
3. Cost/commit documented ≤ 2× single-turn baseline

## Observability (Report Metadata)

Every investigation report includes per-stage metrics:

```json
"metadata": {
  "per_stage": [
    {"stage": "context_assembly", "tier": "script", "latency_ms": 12},
    {"stage": "archetype_detection", "tier": "script", "latency_ms": 3},
    {"stage": "hypothesis_generation", "tier": "llm", "tokens_in": 2100, "tokens_out": 380, "latency_ms": 4200, "cost_usd": 0.0038},
    {"stage": "evidence_tiering", "tier": "script", "latency_ms": 8},
    {"stage": "risk_policy", "tier": "script", "latency_ms": 1},
    {"stage": "quality_gate", "tier": "script", "latency_ms": 1}
  ],
  "cap_applied": false,
  "cap_reason": null,
  "applied_rules": ["supported_hypothesis"],
  "turn_count": 1,
  "total_cost_usd": 0.0038
}
```

## Related

- [harness.md](harness.md) — deterministic infrastructure: routing, budget, schema, error recovery
- [architecture.md](architecture.md) — system identity, design philosophy, trust boundaries
- [evaluation.md](evaluation.md) — D1–D6 scoring, acceptance thresholds
