# Agent Loop — Bug Attribution (V4.2 Revised Hierarchical Pipeline)

The agent loop is the reasoning core. It receives a `ScoredShortlist` + `ProblemStatement` from the input pipeline and produces ranked suspects via a multi-phase pipeline that separates narrowing from deep investigation.

> **Architecture status:** The **target architecture is V4.2** (Revised Hierarchical Pipeline). V4.1 (single scoped loop with 20 candidates) is the current implementation. See [V4.2 ADR](../.harness/docs/v42-architecture-adr.md) for the decision, [architecture-constraints.md](../.harness/docs/architecture-constraints.md) for codified NFRs.

---

## V4.2 Pipeline Overview

```
┌──────────────────────────────────────────────────────┐
│  Phase 1a: SCRIPT PRE-SCORE (zero LLM)               │
│  CandidateSet@100 → ScoredShortlist@15                │
│  Formula: file_overlap + signal_count + retrieval_rank│
└───────────────────────┬──────────────────────────────┘
                        │ ScoredShortlist (15 candidates)
                        ▼
┌──────────────────────────────────────────────────────┐
│  Phase 1b: DETERMINISTIC TRIAGE (zero LLM)            │
│  must_examine = top 3, watchlist = next 4 by pre-score│
│  Fixed tier sizes: 3 + 4 = 7                          │
└───────────────────────┬──────────────────────────────┘
                        │ TriageResult
                        ▼
┌──────────────────────────────────────────────────────┐
│  Phase 2: SCOPED INVESTIGATION (multi-turn ReAct)    │
│  must-examine candidates + scoped tools               │
│  Budget: 15 calls (soft), 8 turns                     │
│  Must: ≥1 get_commit_diff per must-examine SHA        │
└───────────────────────┬──────────────────────────────┘
                        │
              ┌─────────┴─────────┐
              │ Phase 2b trigger?  │
              │ • no suspects      │
              │ • confidence < 0.6 │
              │ • no evidence      │
              └─────────┬─────────┘
               yes      │      no
         ┌──────────────┘      └──────┐
         ▼                            ▼
┌────────────────────┐    ┌──────────────────┐
│ Phase 2b: WATCHLIST │    │  FINAL OUTPUT    │
│ EXPANSION           │    │  Ranked suspects │
│ Fresh context        │    └──────────────────┘
│ Budget: 8 calls, 4t │
└─────────┬───────────┘
          │ Merge: dedup + max confidence + union quotes
          ▼
┌──────────────────────┐
│  FINAL OUTPUT         │
│  Merged ranked suspects│
└──────────────────────┘
```

---

## Phase 1a: Script Pre-Score (zero LLM)

**Harness controls everything.** No agentic loop.

| Aspect | Detail |
|--------|--------|
| **Input** | CandidateSet (50-100 commits) + ProblemStatement |
| **Output** | ScoredShortlist (top 15 by composite score) |
| **Formula** | `0.5·file_overlap + 0.3·norm(signal_count) + 0.2·(1 - norm(best_rank))` |
| **Pre-implementation gate** | Recall@15 on n=20 oracle must validate weights |

Scoring signals:
- **file_overlap:** Overlap between commit's `files_changed` and ProblemStatement's `extracted_files`
- **signal_count:** How many retrieval strategies found this commit (blame, file_log, keyword_grep, pickaxe)
- **best_rank:** Original retrieval rank (normalized, inverted — lower rank = higher score)

---

## Phase 1b: Deterministic Triage (zero LLM)

**Script controls everything.** No LLM call — tier assignment is purely by pre-score rank.

| Aspect | Detail |
|--------|--------|
| **LLM cost** | Zero |
| **Input** | ScoredShortlist (15 candidates from Phase 1a) |
| **Output** | TriageResult: 3 must-examine + 4 watchlist |
| **Rule** | must_examine = shortlist[:3], watchlist = shortlist[3:7] |
| **Rationale field** | Template: `"Rank {n} by pre-score ({score:.3f})"` |

> **Why no LLM?** The triage smoke test (2026-06-17) showed deterministic top-7 achieves TriageRecall@7 = 1.00 on all retrievable cases. Even llama3.1:8b added zero value over the deterministic baseline. See [V4.2 ADR](../.harness/docs/v42-architecture-adr.md) post-gate revision for reintroduction trigger.

### Handoff to Phase 2

Phase 2 receives:
- SHA + pre-score rank per must-examine candidate
- Retrieval signal type and file overlap
- No LLM reasoning (triage is deterministic)

---

## Phase 2: Scoped Investigation (multi-turn ReAct)

**Harness controls:** tool dispatch, budget enforcement, turn counting, context management, exit conditions, temporal bound.
**LLM controls:** which tools to call, reasoning about diffs/blame, suspect ranking.

### Entry conditions
- Valid `TriageResult` with 3 must-examine + 4 watchlist candidates
- Valid `ProblemStatement` (title + description non-empty)
- `GitContextProvider` with temporal bound set

### System prompt assembly

The system prompt contains:
1. Agent role and strategy instructions
2. Tool descriptions (scoped examination tools only)
3. Output format (`` ```suspects `` block with JSON)
4. Bug report (title + description, truncated to 3000 chars)
5. Must-examine candidates (3 SHAs + pre-score rank each)

### Turn-by-turn execution

Each turn, the LLM responds with one of:

| Response type | Action |
|---------------|--------|
| `` ```tool `` block | Harness parses JSON, dispatches via `ToolRegistry.execute()`, returns compressed result |
| `` ```suspects `` block | Harness parses suspects. Accepted only if diff_examined on ≥1 must-examine SHA. |
| Neither | Nudge ladder activates |

### 4-Tier Nudge Ladder

| State | Nudge |
|-------|-------|
| Idle turn 1 | `Call get_commit_diff on {must_examine[0]}. Output tool block only.` |
| Idle turn 2 | `You have {N}/{budget} calls. Examine remaining must-examine SHAs or output suspects.` |
| Idle turn 3 | Harness force-conclude: parse best-effort from tool cache + pre-score fallback |
| Suspects without diff examined | `Suspects rejected: no diff examined. Call get_commit_diff before suspects.` |

### Context management

**Harness-managed context** (not unbounded message accumulation):
- System prompt (stable across turns)
- Rolling working summary (harness-maintained, ≤2K tokens)
- Last-turn tool results only (compressed)

### Context compression (AgentSZZ-inspired, 3 layers)

1. **Cache deduplication:** Same (tool, args) → "Already examined" instead of re-executing
2. **Formatted output:** Strip git metadata trailers, normalize whitespace
3. **Structured extraction:** 8000-char baseline truncation, smart_diff for extracted_files relevance

### Tools available

| Tool | Purpose | SHA validation |
|------|---------|---------------|
| `get_commit_diff` | Inspect what a candidate changed | CandidateSet only |
| `get_commit_message` | Read the author's stated intent | CandidateSet only |
| `get_file_at_commit` | See file state at a specific point | CandidateSet only |
| `get_blame` | Trace line-level authorship | None (temporal bound enforced) |

### Exit conditions

| Condition | Exit reason | Behavior |
|-----------|-------------|----------|
| Suspects parsed + diff_examined on ≥1 must-examine | `normal` | Suspects returned |
| Budget exhausted (15 tool calls) | `budget_exhausted` | Parse last response for suspects |
| Max turns reached (8) | `max_turns` | Parse last response for suspects |
| 3 consecutive idle turns | `forced_conclude` | Best-effort from tool cache |
| Empty CandidateSet | `empty_candidates` | Return empty immediately |
| LLM/provider failure | `provider_error` | Abort with structured error |

### Resource limits

| Resource | Limit | Enforced by |
|----------|-------|-------------|
| Tool calls | 15 (soft) | Investigation harness |
| Turns | 8 | Investigation harness |
| Min tool calls before suspects | 1 get_commit_diff per must-examine | Investigation harness |
| Global examination cap | ~23 (shared with Phase 2b) | Investigation harness |

---

## Phase 2b: Watchlist Expansion (conditional)

**Trigger:** ANY of:
- (a) Phase 2 produced no suspects
- (b) max(confidence) < 0.6
- (c) no evidence_quotes on top suspect

**If not triggered:** Exit reason = `watchlist_skipped`, final result = Phase 2 output.

| Aspect | Detail |
|--------|--------|
| **Input** | Fresh context: bug report + watchlist (4 candidates) + Phase 2 best suspect as reference |
| **Budget** | 8 tool calls, 4 turns (separate from Phase 2) |
| **Context** | Fresh — no Phase 2 message history carried over |
| **Exit** | Same conditions as Phase 2 |
| **Exit reason** | `watchlist_expansion_exhausted` if budget/turns hit |

### Merge logic

1. Deduplicate by full SHA
2. Duplicates: confidence = max(Phase 2, Phase 2b); evidence_quotes = union; mechanism = longer
3. New 2b-only suspects: insert below Phase 2 suspects unless confidence > top + 0.15
4. Final rank: stable sort by (grounded_quote_count DESC, confidence DESC)
5. Cap at 5 suspects

---

## V4.1 Scoped Investigation (Current Implementation)

The V4.1 implementation runs a single multi-turn loop with 20 candidates in the system prompt. It is the predecessor to V4.2.

| Aspect | V4.1 | V4.2 |
|--------|------|------|
| Candidates in prompt | 20 | 3 must-examine (+ 4 watchlist in 2b) |
| Triage | None (LLM triages implicitly) | Deterministic Phase 1a + 1b (zero LLM) |
| Budget | 15 calls, 8 turns | 15 + 8 overflow, 8 + 4 turns |
| Context | Full message accumulation | Harness-managed rolling summary |
| Exit conditions | suspects + 1 tool call | suspects + diff on must-examine |
| Nudges | Single generic nudge | 4-tier state-based ladder |

V4.1 code: `harness/scoped_runner.py`. Preserved for baseline comparison.

---

## Related

- [system-specification.md](system-specification.md) — full system (all three pipelines), data structures, LLM boundary
- [evaluation-framework.md](evaluation-framework.md) — metrics, 5-stage funnel
- [glossary.md](glossary.md) — term definitions
- [.harness/docs/v42-architecture-adr.md](../.harness/docs/v42-architecture-adr.md) — V4.2 architecture decision
- [.harness/docs/architecture-constraints.md](../.harness/docs/architecture-constraints.md) — NFRs and invariants
- [.harness/docs/scoped-tools-adr.md](../.harness/docs/scoped-tools-adr.md) — V4→V4.1 pivot
