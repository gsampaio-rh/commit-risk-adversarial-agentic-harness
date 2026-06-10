# Agent Loop

The agent loop is the investigation process running inside the [harness](harness.md). Industry-standard term: Perceive → Reason → Act → Observe. The harness controls infrastructure; the agent loop produces the investigation.

## How a Commit Gets Investigated

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

## Validation and Quality Gates

After the LLM responds, the harness validates the investigation quality and forces improvement when it's insufficient — *before* the report is finalized.

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
│                  │  │ • Targeted question              │
│                  │  │ • Deeper context (blame, history)│
│                  │  │ • Pressure: "Is this closer to   │
│                  │  │   LOW or HIGH? Commit."          │
│                  │  │                                  │
│                  │  │ → Back to quality gates          │
│                  │  │   (max 3 turns total)            │
└────────┬─────────┘  └──────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│ CLASSIFICATION RULE CHECK — Is the verdict consistent?  │
│                                                         │
│  • SUPPORTED hypothesis but said MEDIUM?                │
│    → Architectural violation (mechanism floor)           │
│  • Hedging language overrides evidence?                 │
│    → Prompt should have prevented this                  │
│  • All touched files listed as localization?            │
│    → Investigation quality is weak (D2 signal)          │
│                                                         │
│  These checks inform the improvement cycle —            │
│  they tell us where the method needs to change.         │
└─────────────────────────────────────────────────────────┘
```

## Constrained vs Open Agent Loop

In a standard agent loop (LangGraph, Claude Code, Codex), the **LLM decides what to do next**. Our agent loop is **constrained by design**:

| Aspect | Open agent loop (industry default) | Our constrained loop |
|--------|-----------------------------------|---------------------|
| Who steers | LLM decides next action | Orchestrator decides |
| Tool selection | LLM picks from available tools | Pre-assembled context bundle (no runtime tool choice) |
| Turn count | LLM decides when done | Hard cap (max 3, default 1) |
| Follow-up triggers | LLM requests more info | Orchestrator checks signals (confidence, localization, uncertainty) |
| Stopping condition | LLM says "I'm done" | Budget, turn cap, or all quality gates pass |

**Why constrained:** Cost governance ($0.005/commit vs unbounded), reproducibility (same commit = same context = comparable results across method iterations), and the harness philosophy (LLM reasons, infrastructure controls).

**Trade-off acknowledged:** A pure agent loop might achieve higher D2/D3 by letting the agent request specific files or blame output. If D2 remains stuck after iter-4, reopening tool use is the escalation path.

## What the Agent Sees vs What It Never Sees

| Agent receives (investigation) | Agent never sees (eval-only) |
|-------------------------------|------------------------------|
| Unified diff | `buggy` label |
| Commit message | Fix commit / fix diff |
| Numeric features (allowlist) | JIRA issue details |
| File history (3 prior commits) | `fix`, `year`, `author_date` |
| Author stats (from train split) | Ground truth linkage |
| Router probability (ML prior) | Other commits' results |

## Model Strategy (V1)

| Phase | Model | Why | Cost/commit |
|-------|-------|-----|-------------|
| Investigation (turn 1) | `claude-sonnet-4-6` via Cursor SDK | Best reasoning quality; single-turn means one shot matters | ~$0.005 |
| Follow-up (turns 2–3) | Same model | Not yet exercised (max_turns=1 in eval) | ~$0.002/turn |
| D3/D5 judging | Same model | Shares provider — simpler, but potential self-evaluation bias | ~$0.001/judgment |
| Routing | XGBoost (no LLM) | Zero cost, handles ~60% of commits | $0 |

### Open design questions

1. **Should the judge use a different model?** Same model judging its own reasoning may be biased. EXP-JUDGE-SWAP will test this.
2. **Should follow-up turns use a different model?** Turn 2–3 ask "commit to HIGH or LOW" — a simpler task that might work with a cheaper model.
3. **Should router-confirmed HIGH commits get a lighter touch?** The agent currently runs full investigation on HIGH-zone commits.
4. **Is Cursor SDK mode=plan limiting?** Read-only mode with no native tool calling. D6=0.85 without tools suggests context assembly is sufficient.

## Multi-Turn Strategy (V1)

| Config | Value | Rationale |
|--------|-------|-----------|
| `max_turns` (orchestrator default) | 3 | Designed capacity |
| `max_turns` (run_eval override) | 1 | Cost control during method iteration |
| Follow-up trigger: confidence | < 0.6 | Agent signals it isn't sure |
| Follow-up trigger: localization | empty | Agent couldn't identify defect site |
| Follow-up trigger: uncertainty | explicit in reasoning | Agent says "insufficient context" |
| Follow-up injection | Targeted question + deeper context | "What specific failure mode?" + blame/extended history |

**V1 default is single-turn.** Multi-turn is designed but untested in eval. The improvement cycle prioritizes prompt/method quality at turn 1 (iter-1, iter-2) before adding turns (iter-3).

## Related

- [harness.md](harness.md) — deterministic infrastructure: routing, budget, schema, error recovery
- [architecture.md](architecture.md) — system identity, design philosophy, trust boundaries
- [evaluation.md](evaluation.md) — D1–D6 scoring, acceptance thresholds
