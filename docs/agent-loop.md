# Agent Loop — Bug Attribution (V4.2 Revised Hierarchical Pipeline)

The agent loop is the reasoning core. It receives a `TriageResult` + `ProblemStatement` from the input pipeline and produces ranked suspects via a multi-phase pipeline that separates narrowing from deep investigation.

> **Architecture status:** V4.2 is the **current proven architecture**. Hit@5=0.800, MRR=0.600 (Cursor SDK, claude-sonnet-4-6, n=5). Local gemma3:12b achieves Hit@5=0.250, MRR=0.225 (n=20). Exceeds V3 baseline (Hit@5=0.50) by 60%.
>
> ADRs: [V4.2 ADR](../.harness/docs/v42-architecture-adr.md), [architecture-constraints.md](../.harness/docs/architecture-constraints.md), [scoped-tools ADR](../.harness/docs/scoped-tools-adr.md) (historical, V4→V4.1 pivot).

---

## Pipeline Overview

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

## Implementation Map

| Phase | Module | Entry Point |
|-------|--------|-------------|
| 1a Pre-score | `narrowing/scoring.py` | `compute_pre_scores()` |
| 1b Triage | `narrowing/triage.py` | `deterministic_triage()` |
| 1a+1b Combined | `narrowing/pipeline.py` | `narrow_candidates()` |
| 2 Investigation | `investigation/investigator.py` | `RevisedScopedInvestigator.investigate()` |
| 2 Prompts | `investigation/prompts.py` | `build_phase2_system_prompt()` |
| 2b Expansion | `investigation/watchlist_expansion.py` | `run_phase2b()` |
| 2b Prompts | `investigation/prompts.py` | `build_phase2b_system_prompt()` |
| Tools | `investigation/tools.py` | `build_scoped_tools()` |
| Eval | `eval/metrics.py` | `compute_funnel()` |
| Full Pipeline | `scripts/run_scoped_eval.py` | CLI entry point |

---

## Phase 1a: Script Pre-Score (zero LLM)

**Harness controls everything.** No agentic loop.

| Aspect | Detail |
|--------|--------|
| **Input** | CandidateSet (50-100 commits) + ProblemStatement |
| **Output** | ScoredShortlist (top 15 by composite score) |
| **Formula** | `0.5·file_overlap + 0.3·norm(signal_count) + 0.2·(1 - norm(best_rank))` |
| **Module** | `narrowing/scoring.py` → `compute_pre_scores()` |
| **Gate result** | Recall@15 = 0.846 (11/13 retrievable, n=20) |

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
| **Module** | `narrowing/triage.py` → `deterministic_triage()` |
| **Gate result** | TriageRecall@7 = 1.00 (11/11 conditioned on R@15) |

> **Why no LLM?** The triage smoke test (2026-06-17) showed deterministic top-7 achieves TriageRecall@7 = 1.00 on all retrievable cases. Even llama3.1:8b added zero value over the deterministic baseline. Reintroduction trigger: TriageRecall@7 < 0.80 on dataset n >= 50.

### Handoff to Phase 2

Phase 2 receives:
- SHA + pre-score rank per must-examine candidate
- Retrieval signal type and file overlap
- No LLM reasoning (triage is deterministic)

---

## Phase 2: Scoped Investigation (multi-turn ReAct)

**Harness controls:** tool dispatch, budget enforcement, turn counting, context management, exit conditions, temporal bound.
**LLM controls:** which tools to call, reasoning about diffs/blame, suspect ranking.

**Implementation:** `RevisedScopedInvestigator` in `investigation/investigator.py`.

### Entry conditions
- Valid `TriageResult` with 3 must-examine + 4 watchlist candidates
- Valid `ProblemStatement` (title + description non-empty)
- `GitContextProvider` with temporal bound set

### System Prompt Template

The Phase 2 system prompt is assembled by `build_phase2_system_prompt()` in `investigation/prompts.py`. Here is the full template structure:

```
You are a bug attribution agent. Your task: examine candidate commits
to find which one INTRODUCED the bug described below.

## Tools
To invoke a tool, output a fenced block:
```tool
{"tool": "<name>", "args": {<arguments>}}
```

- **get_commit_diff**: Get the unified diff (patch) for a candidate commit.
- **get_commit_message**: Get the full commit message for a candidate commit.
- **get_blame**: Get git blame for a file, showing which commit last modified each line.
- **get_file_at_commit**: Get file contents at a specific candidate commit.

## Strategy
You MUST examine each must-examine commit ({sha1}, {sha2}, {sha3})
with `get_commit_diff` before concluding.
1. Call `get_commit_diff` on each must-examine SHA
2. For each diff, assess whether the change could CAUSE the symptoms
3. Use `get_blame` or `get_file_at_commit` for deeper analysis if needed
4. Conclude with a `suspects` block when you have evidence

## Output Format
When ready to conclude:
```suspects
[{
  "commit_id": "<full 40-char SHA>",
  "confidence": 0.85,
  "mechanism": "Explain HOW this commit caused the bug",
  "evidence_quotes": ["relevant diff lines or blame output"]
}]
```
Rank 3-5 suspects by confidence. Use ONLY full SHAs from the candidate list.

## Bug Report
**Title:** {problem.title}

**Description:**
{problem.description[:3000]}

## Must-Examine Candidates ({N} commits)
These are the highest-priority candidates — examine ALL of them:
  1. `{sha}` — "{summary}" [pre_score=0.XXX, file_overlap=0.XX]
     [{retrieval_signal}] [{date}]
     files: {file1}, {file2}, ...
  2. ...
  3. ...
```

### Tool Invocation Format

The LLM produces tool calls as fenced code blocks parsed by `parse_tool_calls()`:

````
```tool
{"tool": "get_commit_diff", "args": {"commit_id": "abc123..."}}
```
````

The LLM produces final output as a suspects block parsed by `parse_suspects()`:

````
```suspects
[{
  "commit_id": "abc123def456...",
  "confidence": 0.85,
  "mechanism": "This commit changed the buffer allocation...",
  "evidence_quotes": ["- int size = DEFAULT_SIZE;", "+ int size = 0;"]
}]
```
````

### Turn-by-turn execution

Each turn, the LLM responds with one of:

| Response type | Action |
|---------------|--------|
| `tool` block | Harness parses JSON, dispatches via `ToolRegistry.execute()`, returns compressed result |
| `suspects` block | Harness parses suspects. Accepted only if `MustExamineGate.is_satisfied()`. |
| Neither | `NudgeLadder` activates |

### Turn 0 (initial message)

```
Begin investigation. Examine each must-examine commit with get_commit_diff.
```

### Subsequent turns (compressed context)

```
## Investigation Progress
- get_commit_diff(abc123def456): Added new buffer allocation logic, changed 3 files...
- get_commit_diff(789xyz012345): Refactored error handling in Parser.java...
- [cache] Already examined — see previous result for get_commit_diff(...)

Continue examining candidates or conclude with ```suspects.
```

### 4-Tier Nudge Ladder

**Implementation:** `NudgeLadder` class in `investigation/loop_support.py`.

The nudge ladder tracks consecutive idle turns (no tool calls and no suspects parsed) and produces escalating interventions:

| Tier | Trigger | Action | Message |
|------|---------|--------|---------|
| 1 (Directive) | 1st idle turn | Append nudge to context | `Call get_commit_diff on {first_unexamined_sha}. Output tool block only.` |
| 2 (Warning) | 2nd idle turn | Append nudge to context | `You have {N}/{budget} calls. Examine remaining must-examine SHAs or output suspects.` |
| 3 (Force conclude) | 3rd idle turn | Harness exits loop | Returns `Phase2Result(exit_reason=FORCED_CONCLUDE)` with empty suspects |
| 4 (Reject suspects) | Suspects without any diff | Append rejection message | `Suspects rejected: no diff examined. Call get_commit_diff before suspects.` |

The ladder resets (`consecutive_idle = 0`) whenever the LLM produces tool calls or valid suspects.

### Must-Examine Gate

**Implementation:** `MustExamineGate` class in `investigation/loop_support.py`.

Tracks which must-examine SHAs have been successfully diffed:

- `record_diff(sha, success)` — records after each `get_commit_diff` call (SHA prefix matching, 12 chars)
- `is_satisfied()` — returns `True` when ≥1 must-examine SHA has been diffed
- Suspects submitted before the gate is satisfied trigger `NudgeAction.REJECT_SUSPECTS`

### Context Compression (AgentSZZ-inspired, 3 layers)

**Implementation:** `ToolCallCache` and `RollingSummary` in `investigation/loop_support.py`.

| Layer | Mechanism | Effect |
|-------|-----------|--------|
| Cache deduplication | `ToolCallCache` — keyed by `(tool_name, sorted(args))` | Duplicate call → "Already examined" message, no re-execution, no budget cost |
| Rolling summary | `RollingSummary(max_chars=2000)` — 1-line per tool result | Replaces unbounded message accumulation. FIFO eviction at 2K chars |
| Output truncation | `_truncate(text, max_chars=8000)` in `investigation/tools.py` | Long git output capped at 8000 chars |

Context per turn is assembled by `_build_turn_messages()`:
1. System prompt (stable across all turns)
2. User message with rolling summary + continuation instruction

No message history accumulation — each turn gets fresh system prompt + compressed context.

### Tools Available

Registered by `build_scoped_tools()` in `investigation/tools.py`. SHA-taking tools validate against the CandidateSet (12-char prefix match) before execution:

| Tool | Description | SHA validation | Truncation |
|------|-------------|---------------|------------|
| `get_commit_diff` | Get the unified diff (patch) for a candidate commit | CandidateSet only | 8000 chars |
| `get_commit_message` | Get the full commit message for a candidate commit | CandidateSet only | None |
| `get_blame` | Get git blame for a file (line-level authorship) | None (temporal bound enforced) | 8000 chars |
| `get_file_at_commit` | Get file contents at a specific candidate commit | CandidateSet only | 8000 chars |

Search tools are **not registered** — retrieval is done by the input pipeline.

### Exit Conditions

**Implementation:** exit paths in `RevisedScopedInvestigator.investigate()`:

| Condition | Exit reason | Behavior |
|-----------|-------------|----------|
| Suspects parsed + gate satisfied | `NORMAL` | Suspects returned |
| Budget exhausted (15 tool calls) | `BUDGET_EXHAUSTED` | Parse last response |
| Max turns reached (8) | `MAX_TURNS` | Return with empty suspects |
| 3 consecutive idle turns | `FORCED_CONCLUDE` | Return empty |
| Empty CandidateSet | `EMPTY_CANDIDATES` | Return empty immediately |
| LLM/provider failure | `PROVIDER_ERROR` | Abort with structured error |

### Resource Limits

| Resource | Limit | Default | Enforced by |
|----------|-------|---------|-------------|
| Tool calls | Soft cap | 15 | `RevisedScopedInvestigator(max_tool_calls=15)` |
| Turns | Hard cap | 8 | `RevisedScopedInvestigator(max_turns=8)` |
| Min diff before suspects | ≥1 must-examine SHA | — | `MustExamineGate` |
| Max idle before force-conclude | 3 | — | `NudgeLadder` |
| Rolling summary size | — | 2000 chars | `RollingSummary(max_chars=2000)` |
| Tool output truncation | — | 8000 chars | `_truncate()` in `investigation/tools.py` |

---

## Phase 2b: Watchlist Expansion (conditional)

**Implementation:** `investigation/watchlist_expansion.py`

### Trigger logic

`should_trigger_phase2b(phase2_result)` returns `(triggered: bool, reason: str)`.

**Trigger:** ANY of:
- (a) Phase 2 produced no suspects → reason `"no_suspects"`
- (b) `max(confidence) < 0.6` → reason `"low_confidence"`
- (c) top suspect by confidence has empty `evidence_quotes` → reason `"no_evidence"`

**If not triggered:** exit reason = `WATCHLIST_SKIPPED`, final result = Phase 2 output.

| Aspect | Detail |
|--------|--------|
| **Input** | Fresh context: bug report + watchlist (4 candidates) + Phase 2 best suspect as reference |
| **Budget** | 8 tool calls, 4 turns (separate from Phase 2) |
| **Context** | Fresh — no Phase 2 message history carried over |
| **Exit** | Same conditions as Phase 2 |
| **Exit reason** | `WATCHLIST_EXPANSION_EXHAUSTED` if budget/turns hit |

### Phase 2b System Prompt Template

Assembled by `build_phase2b_system_prompt()` in `investigation/prompts.py`:

```
You are a bug attribution agent. A prior investigation examined
high-priority candidates but produced weak results. Your task:
examine these WATCHLIST candidates to find which one INTRODUCED
the bug described below.

## Tools
[Same tool invocation format and descriptions as Phase 2]

## Strategy
Examine each watchlist commit ({sha1}, {sha2}, {sha3}, {sha4})
with `get_commit_diff`.
1. Call `get_commit_diff` on each watchlist SHA
2. For each diff, assess whether the change could CAUSE the symptoms
3. Use `get_blame` or `get_file_at_commit` for deeper analysis if needed
4. Conclude with a `suspects` block when you have evidence

## Output Format
[Same as Phase 2]

## Bug Report
**Title:** {problem.title}
**Description:** {problem.description[:3000]}

## Watchlist Candidates ({N} commits)
  1. `{sha}` — "{summary}" [pre_score=X.XXX, file_overlap=X.XX]
     [{signal}] [{date}]
     files: ...
  ...

## Phase 2 Best Suspect (reference)         ← conditional section
Previous investigation found: `{sha}` (confidence=X.XX)
Mechanism: {mechanism}
Compare your findings against this suspect.
```

The "Phase 2 Best Suspect" section is included only when Phase 2 produced a suspect. It provides context without carrying over the full Phase 2 message history.

### Watchlist investigation execution

Phase 2b reuses `RevisedScopedInvestigator` with a **synthetic TriageResult** — the 4 watchlist candidates are promoted to must-examine, and the watchlist is set to empty:

```python
synthetic_triage = TriageResult(
    must_examine=[promoted watchlist candidates],
    watchlist=[],
    ...
)
inv = RevisedScopedInvestigator(
    ..., triage=synthetic_triage,
    max_tool_calls=8, max_turns=4,  # reduced budget
)
```

This ensures the same nudge ladder, must-examine gate, and context compression apply to Phase 2b without code duplication.

### Merge Logic

**Implementation:** `merge_suspects()` in `investigation/watchlist_expansion.py`.

1. **Deduplicate by full SHA** — identify suspects appearing in both Phase 2 and Phase 2b
2. **Duplicates:** `confidence = max(P2, P2b)`, `evidence_quotes = union` (deduplicated), `mechanism = longer` string, `phase = "both"`
3. **New 2b-only suspects:** insert below Phase 2 suspects UNLESS `confidence > top_p2_confidence + 0.15` (then promoted)
4. **Final rank:** stable sort by `(len(evidence_quotes) DESC, confidence DESC)`
5. **Cap at 5** suspects, assign rank 1..N

Constants: `CONFIDENCE_THRESHOLD = 0.6`, `PROMOTION_MARGIN = 0.15`, `MAX_FINAL_SUSPECTS = 5`.

---

## Skills

No investigation-specific skills are currently loaded. The V4.2 pipeline uses direct prompts (templates above) without skill injection. The governance/skills system from the mechanism-design ADR was deferred — deterministic triage + scoped tools proved sufficient.

Skill injection is a future enhancement path if trace analysis reveals systematic reasoning gaps.

---

## Provider Routing

**Implementation:** `get_provider()` in `infra/llm.py`.

| Env var | Effect |
|---------|--------|
| `INVESTIGATION_MODEL` | Overrides model for Phase 2/2b (e.g. `gemma3:12b-it-q8_0` routes to Ollama) |
| `EVAL_STRICT=1` | Fail-fast: raises `ProviderUnavailableError` if no real provider available |
| `CURSOR_SDK_ENABLED=1` | Enables `CursorSDKProvider` (Agent.prompt() API) |

Provider priority: Cursor SDK (if enabled) → Ollama (if `INVESTIGATION_MODEL` set) → Mock (test only, blocked by `EVAL_STRICT`).

---

## V4.1 Scoped Investigation (Historical Baseline)

The V4.1 implementation ran a single multi-turn loop with 20 candidates in the system prompt. Code was deleted during the V4.2 repo audit; results are preserved in the [exp19b retrospective](../.harness/docs/exp19b-retrospective.md).

| Aspect | V4.1 (historical) | V4.2 (current) |
|--------|------|------|
| Candidates in prompt | 20 | 3 must-examine (+ 4 watchlist in 2b) |
| Triage | None (LLM triages implicitly) | Deterministic Phase 1a + 1b (zero LLM) |
| Budget | 15 calls, 8 turns | 15 + 8 overflow, 8 + 4 turns |
| Context | Full message accumulation | Harness-managed rolling summary (≤2K) |
| Exit conditions | suspects + 1 tool call | suspects + diff on must-examine (gate) |
| Nudges | Single generic nudge | 4-tier state-based ladder |
| Best result | Not evaluated at scale | Hit@5=0.800 (Cursor SDK), 0.250 (local) |

V4.1 code (`ScopedInvestigator`, `build_scoped_system_prompt()`, `run_scoped_investigation()`) was deleted from `investigation/investigator.py` and `investigation/prompts.py` during the V4.2 repo audit. Historical results in [exp19b retrospective](../.harness/docs/exp19b-retrospective.md).

---

## Related

- [system-specification.md](system-specification.md) — full system (all three pipelines), data structures, LLM boundary
- [evaluation-framework.md](evaluation-framework.md) — metrics, 5-stage funnel
- [glossary.md](glossary.md) — term definitions
- [.harness/docs/v42-architecture-adr.md](../.harness/docs/v42-architecture-adr.md) — V4.2 architecture decision
- [.harness/docs/architecture-constraints.md](../.harness/docs/architecture-constraints.md) — NFRs and invariants
- [.harness/docs/scoped-tools-adr.md](../.harness/docs/scoped-tools-adr.md) — V4→V4.1 pivot (historical)
