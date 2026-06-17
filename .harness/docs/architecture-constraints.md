# Architecture Constraints — Bug Attribution Agent

**Status:** Codified from V4.2 builder/evaluator debate (2026-06-17)
**Scope:** Non-functional requirements and architectural invariants that persist across sessions and implementations.

These constraints were debated and agreed between builder and evaluator. They are NOT aspirational — they are hard constraints that must be satisfied by any implementation.

---

## Invariants (Hard, Non-Negotiable)

### I1: Temporal Bound Enforcement

The temporal bound (`COMMIT_B~1`) constrains the **entire** system — input pipeline, agent tools, and any LLM context. In eval mode, bound comes from `fix_hash`. No component may access commits beyond the bound.

### I2: Oracle Isolation

Ground truth data (`bug_hash`, `fix_hash`, chain linkage, eval metrics, stage recall values) must **never** enter investigation context. The agent sees the JIRA report and repository information only.

### I3: LLM Reasons, Scripts Retrieve

Scripts own retrieval (candidate set assembly) and evidence verification. The LLM owns examination reasoning and attribution. The LLM never searches from scratch — it receives a curated CandidateSet.

### I4: Agent Is Governed

The investigation harness governs the LLM. The LLM does not self-govern. The harness manages state, transitions, completion criteria, tool dispatch, and budget enforcement. Budget is a hard stop, not the primary exit signal.

### I5: Observable By Design

Every investigation produces a structured `InvestigationTrace`. No investigation is a black box. Traces are the substrate for failure forensics and skill emergence.

### I6: Scoped Not Unbounded

The agent's tools are restricted to CandidateSet SHAs. No full-repo search. Search tools are not registered in the agent's tool registry.

---

## Architectural Constraints (Hard)

### C1: Separate Narrowing from Deep Investigation

No phase may simultaneously triage AND deeply investigate. Triage (metadata reasoning) and investigation (diff/blame reasoning) are separate phases with separate contexts.

**Evidence:** Every successful system in the literature separates these concerns (AgentSZZ, SWERANK, RGFL, Agentless, HiFL, MAS-SZZ). V4.1's combined approach inherits this failure mode.

### C2: Context Budget Per Phase

Each phase starts with a fresh, bounded context. Tool results are compressed before re-injection. No unbounded message accumulation.

- Phase 1b: One-shot (no accumulation)
- Phase 2: Harness-managed rolling summary (≤2K tokens) + last-turn tool results
- Phase 2b: Fresh context (no Phase 2 message history)

**Evidence:** AgentSZZ's 3-layer compression achieves 30% token reduction with zero accuracy loss. Accumulated context degrades quality (observed in V3 cursor-sdk-multi-turn eval).

### C3: Script-Anchored Triage

LLM triage cannot veto retrieval's top candidates. The top 3 candidates by deterministic pre-score MUST appear in must-examine regardless of LLM output. LLM triage adds value by reordering and selecting watchlist, not by overriding retrieval.

**Evidence:** V4 metadata-only achieved Hit@5=0.062. Retrieval signals (blame, file_log) are more reliable than LLM metadata reasoning for candidate selection. LLM adds value for investigation, not for initial filtering.

### C4: Fixed Tier Sizes

must_examine = 3, watchlist = 4. Total = 7. These are fixed, not variable ranges. Metrics (TriageRecall@7) are defined against these fixed sizes.

### C5: Fail-Fast Eval Policy

During gated eval runs, the provider chain must fail fast — no silent degradation to weaker models (Ollama, Mock). If the primary provider is unavailable, the case fails with a structured error, not a poisoned investigation.

### C6: Explicit Exit Reasons

Every investigation must record an `InvestigationExitReason` enum value. No ambiguous exits. Exit reasons must distinguish between normal, budget_exhausted, max_turns, forced_conclude, stall, provider_error, watchlist_expansion_exhausted, and watchlist_skipped.

---

## Non-Functional Requirements

### NFR1: Per-Investigation Cost

Target: ≤ $0.15 USD per case (revised from $0.06 based on evaluator's 2-3× adjustment for diff payloads and message history growth).

### NFR2: Per-Investigation Latency

Target: ≤ 300s per case (wall-clock). Phase 1b should complete in <10s. Phase 2 is the latency-dominant phase.

### NFR3: Retrieval Recall Floor

Gate: Retrieval Recall@100 ≥ 0.35. Current: 0.40 (8/20). This is the binding constraint — no investigation architecture can fix cases where GT isn't retrieved.

### NFR4: Reproducibility

Pre-score weights, model names, prompt versions, and scoring metadata must be recorded in traces and checkpoint files. Two runs with the same inputs and model should produce comparable (not necessarily identical) results.

### NFR5: Funnel Observability

The 5-stage funnel (Recall@100 → Recall@15 → TriageRecall@7 → ExamRecall → Hit@5) must be computed for every eval run. Failures must be localizable to a specific funnel stage.

### NFR6: Provider Independence

The investigation architecture must not depend on a specific LLM provider. Any OpenAI-compatible chat completions endpoint must work. Provider-specific quirks (Cursor SDK one-shot limitations, GPT vs Claude tool-calling format differences) are handled at the provider layer, not the architecture layer.

### NFR7: Backward Compatibility

New data structures must have adapters to existing eval infrastructure (`evaluate_attribution()`, D3 judge, D6 evidence scorer, `BugAttributionReport`). V3 baseline must remain runnable for comparison.

---

## Pre-Implementation Gates

These measurements must be completed BEFORE writing V4.2 implementation code:

| Gate | Pass condition | Method |
|------|---------------|--------|
| G1: Recall@15 | GT in top 15 by pre-score formula on ≥80% of retrievable cases | Run pre-score on n=20 oracle |
| G2: Provider compliance | ```tool block parse rate ≥ 90% on chosen model | n=3 case spike on OpenAI-compatible endpoint |
| G3: Triage smoke | LLM ranks GT in top 7 on ≥60% of retrievable cases | One-shot triage on n=3-5 cases |

---

## Design Decisions Deferred

| Decision | Reason | When to revisit |
|----------|--------|-----------------|
| Cheap triage model (haiku/mini) | No TriageRecall@7 data yet | After G3 gate with strong model |
| Level 2 LLM-assisted extraction | Retrieval recall@100 meets gate (0.40 ≥ 0.35) | When recall@100 blocks Hit@5 |
| Native Anthropic provider | OpenAI-compatible proxy works for now | If proxy shows tool-call compliance issues |
| Rolling context summary implementation | Harness-managed context spec agreed, impl details deferred | During Phase 2 implementation |
| Evidence-grounding-based Phase 2b trigger | Requires grounded quote detection at exit time | During Phase 2b implementation |
