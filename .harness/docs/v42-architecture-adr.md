# V4.2 Architecture Decision Record — Revised Hierarchical Pipeline

**Status:** Decided (2026-06-17)
**Supersedes:** V4.1 Scoped Tools (single-phase investigation)
**ADR:** V4.1 scoped-tools-adr.md → this document

## Context

V4.1 passes 20 candidates + bug report + tool descriptions into a single system prompt. This causes:
- Context overload (~10-15K tokens before tool results)
- Attention dilution across 20 candidates
- No prioritization signal — LLM must simultaneously triage AND investigate
- Budget waste examining low-probability candidates

V3 (fully agentic, full repo access) achieved Hit@5=0.50. V4 metadata-only achieved Hit@5=0.062. V4.1 structurally has the same context overload problem as V4.

## Decision

**Option C' (Revised Hierarchical):** Separate narrowing from deep investigation via a 4-phase pipeline.

```
Phase 0:  Retrieval (existing, zero LLM) → CandidateSet@100
Phase 1a: Script pre-score (zero LLM) → ScoredShortlist@15
Phase 1b: LLM triage (1 call, one-shot) → 3 must-examine + 4 watchlist
Phase 2:  Scoped investigation (multi-turn ReAct, scoped tools) → Ranked suspects
Phase 2b: Watchlist expansion (conditional, fresh context) → Merged final result
```

### Why not Option A (Fan-out)?

- No cheap per-leaf verification signal for "is this the bug-introducing commit?"
- Workers can't cross-reference candidates
- 5× token cost without proven accuracy lift
- RAH literature: fan-out only when subtasks genuinely independent + verification exists

### Why not Option B (Pre-fetched diffs)?

- Loses tool use, which was V3's key strength
- Pre-fetching 5 full diffs could be enormous (some commits change hundreds of lines)
- Tools let the LLM be selective about what it examines

## Detailed Phase Design

### Phase 0: Retrieval (unchanged)

- Owner: Scripts, zero LLM
- Input: ProblemStatement + repo + temporal_bound
- Output: CandidateSet (50-100 commits)
- Module: `retrieval/pipeline.py`

### Phase 1a: Script Pre-Score (new, zero LLM)

- Owner: Scripts
- Input: CandidateSet + ProblemStatement
- Output: ScoredShortlist (top 15)
- Formula: `0.5·file_overlap + 0.3·norm(signal_count) + 0.2·(1 - norm(best_rank))`
- Pre-implementation gate: measure Recall@15 on n=20 oracle before locking weights
- Rationale: Anchors triage with deterministic signals. LLM triage cannot veto top-3 by pre-score.

### Phase 1b: LLM Triage (new, 1 call)

- Owner: Harness + LLM
- Input: Bug report + 15 candidates with metadata + diff_summary (~300 chars each)
- Output: TriageResult — 3 must-examine + 4 watchlist (fixed tier sizes)
- Constraint: Top 3 by pre_score MUST appear in must_examine (harness-enforced)
- Fallback: Invalid LLM output → must_examine = top 3 by pre_score, watchlist = next 4
- Gate: Triage Recall@7 >= 0.80 on retrievable cases before approving cheap model
- Risk mitigation: Phase 1a pins top-3 regardless of LLM output

### Phase 2: Scoped Investigation (revised)

- Owner: Harness + LLM
- Input: Bug report + must-examine candidates (SHA + 1-line triage rationale) + scoped tools
- Tools: get_commit_diff, get_commit_message, get_blame, get_file_at_commit
- Scope: Full CandidateSet (existing design, not just must-examine)
- Budget: 15 tool calls (soft), 8 turns
- Must examine: At least 1 get_commit_diff on each must-examine SHA before normal exit
- Context: Harness-managed — rolling working summary (≤2K tokens) + last-turn tool results
- Exit: suspects + diff_examined → normal; budget exhausted → force conclude; 3 idle turns → force

### Phase 2b: Watchlist Expansion (new, conditional)

- Owner: Harness + LLM
- Trigger: ANY of (a) no suspects, (b) max_confidence < 0.6, (c) no evidence_quotes on top suspect
- Input: Fresh context — bug report + watchlist candidates + Phase 2 best suspect summary
- Budget: 8 tool calls, 4 turns (separate from Phase 2)
- Merge: Dedup by SHA, confidence = max(p2, p2b), evidence_quotes = union, mechanism = longer
- Re-rank: grounded_quote_count DESC, confidence DESC

## Agentic Loop Design

### Budget

- Global examination cap: ~23 calls (Phase 2 soft target 15, Phase 2b up to 8 overflow)
- Must examine all must_examine SHAs via at least 1 get_commit_diff each
- Per-phase ceilings, not hard partitions — Phase 2 early exit saves budget for 2b

### Context Compression (AgentSZZ-inspired)

1. **Cache deduplication:** Same (tool, args) returns "Already examined" instead of re-executing
2. **Formatted output:** Strip git metadata trailers, normalize whitespace
3. **Structured extraction:** Start from existing 8000-char truncation baseline. Use smart_diff for extracted_files relevance. Defer τ reduction to empirical validation.

### 4-Tier Nudge Ladder

| State | Nudge |
|-------|-------|
| Idle turn 1 | `Call get_commit_diff on {must_examine[0]}. Output tool block only.` |
| Idle turn 2 | `You have {N}/{budget} calls. Examine remaining must-examine SHAs or output suspects.` |
| Idle turn 3 | Harness force-conclude: parse best-effort from tool cache + pre-score fallback |
| Suspects w/o diff | `Suspects rejected: no diff examined. Call get_commit_diff before suspects.` |

### Exit Conditions

| Condition | Behavior |
|-----------|----------|
| Suspects parsed + diff_examined on top suspect | Normal exit |
| Budget exhausted (15 calls) | Parse last response for suspects |
| Max turns (8) | Parse last response for suspects |
| 3 consecutive idle turns | Force conclude with best-effort |
| Empty CandidateSet | Return empty immediately |
| LLM/provider failure | Abort case with structured error |

### InvestigationExitReason Enum

`normal`, `budget_exhausted`, `max_turns`, `forced_conclude`, `stall`, `provider_error`, `empty_candidates`, `watchlist_expansion_exhausted`, `watchlist_skipped`

## Model and Provider Strategy

### Provider

- **Primary:** OpenAI-compatible API (native chat completions, tool_calls, multi-turn)
- **Phase-aware routing:** `TRIAGE_MODEL` + `INVESTIGATION_MODEL` env vars
- **Cursor SDK:** Dropped for multi-turn eval. Bridge unreliable for sustained workloads.
- **Fail-fast:** No silent degradation to Ollama/Mock during gated eval runs

### Model Selection

- Start with same model for triage + investigation (e.g., claude-sonnet-4 or gpt-4o)
- Defer cheap triage (haiku/mini) until Triage Recall@7 ≥ 0.80 is proven
- Compliance spike required: test ```tool block format on chosen model before n=20

### Cost Estimate (revised)

| Phase | Model | Calls | Est. tokens | Est. cost |
|-------|-------|-------|-------------|-----------|
| 1b | same as Phase 2 | 1 | ~8K | $0.01 |
| 2 | sonnet-4 / gpt-4o | 3-8 | ~40-80K | $0.05-0.10 |
| 2b | same | 2-4 | ~20-40K | $0.02-0.05 |
| **Total/case** | | **6-13** | **~70-120K** | **$0.10-0.15** |

## Data Structures (New)

### ScoredCandidate

```python
@dataclass(frozen=True)
class ScoredCandidate:
    commit: CandidateCommit
    pre_score: float         # 0.0-1.0, composite from Phase 1a
    file_overlap: float      # overlap with extracted_files
```

### ScoredShortlist

```python
@dataclass
class ScoredShortlist:
    candidates: list[ScoredCandidate]  # top 15, sorted by pre_score desc
    total_candidates: int
    temporal_bound: str
    scoring_weights: dict[str, float]  # for reproducibility
```

### TriageResult

```python
@dataclass
class TriageResult:
    must_examine: list[TriagedCandidate]  # exactly 3
    watchlist: list[TriagedCandidate]     # exactly 4
    raw_llm_response: str                 # trace only
    model_used: str
```

### TriagedCandidate

```python
@dataclass(frozen=True)
class TriagedCandidate:
    scored: ScoredCandidate
    tier: Literal["must_examine", "watchlist"]
    triage_rank: int          # 1-based within tier
    rationale: str            # 1-line LLM explanation
```

### Suspect (unified, replaces SuspectCommit + dict suspects)

```python
@dataclass
class Suspect:
    commit_id: str
    rank: int                 # 1-based, set at output
    confidence: float         # 0.0-1.0
    mechanism: str            # causal explanation
    evidence_quotes: list[str]
    phase: str                # "investigation" or "watchlist_expansion" or "both"
    tools_used: list[str]     # which tools were called for this suspect
```

### InvestigationResult (eval-facing, slim)

```python
@dataclass
class InvestigationResult:
    issue_key: str
    suspects: list[Suspect]
    exit_reason: ExitReason
    retrieval_recall: bool        # eval-only
    trace: InvestigationTrace | None
    elapsed_s: float
```

### Phase2bResult (nested, optional)

```python
@dataclass
class Phase2bResult:
    suspects: list[Suspect]
    tool_calls: int
    turns: int
    trigger_reason: str
```

## 5-Stage Funnel Metrics

```
Recall@100 → Recall@15 → TriageRecall@7 → ExamRecall → Hit@5
```

| Metric | Pipeline | Question |
|--------|----------|----------|
| Retrieval Recall@100 | Input | Is bug_hash in CandidateSet? |
| Pre-score Recall@15 | Phase 1a | Is bug_hash in ScoredShortlist? |
| Triage Recall@7 | Phase 1b | Is bug_hash in must_examine ∪ watchlist? |
| Examination Recall | Phase 2 | Did agent call get_commit_diff on bug_hash? |
| Hit@5 | Final | Is bug_hash in top-5 suspects? |

All recall metrics are eval-only (require ground_truth_sha). Must never leak into investigation prompts.

## Contract Invariants

| Invariant | Enforcement |
|-----------|-------------|
| must_examine ∩ watchlist = ∅ | Harness validation at Phase 1b output |
| must_examine ⊆ ScoredShortlist | Harness validation |
| watchlist ⊆ ScoredShortlist | Harness validation |
| Top 3 by pre_score ∈ must_examine | Harness-enforced, LLM cannot veto |
| pre_score ∈ [0, 1] | Phase 1a validation |
| commit_id = full 40-char SHA | Normalized at retrieval |
| Phase 1b = exactly 1 LLM call | Harness-enforced |
| Phase 2 tools scoped to CandidateSet | build_scoped_tools() unchanged |
| Temporal bound propagated to all phases | Via ScoredShortlist.temporal_bound |
| ≥1 get_commit_diff per must_examine SHA | Phase 2 exit condition |
| Phase 2b suspects merge without data loss | Dedup + union + max rules |

## Pre-Implementation Experiments

Before writing V4.2 code:

1. **Recall@15 ablation:** Run pre-score formula on n=20 with oracle. Does GT land in top 15? Test weight sensitivity.
2. **Provider compliance spike:** Test ```tool block parsing on GPT-4o / Sonnet via OpenAI-compatible endpoint. n=3 cases.
3. **Triage smoke test:** One-shot triage prompt on 3 cases — does LLM rank GT in top 7 when GT is in shortlist?

## Compatibility

- Adapter from `Suspect` → `SuspectCommit` for existing `evaluate_attribution()` and D3/D6 paths
- Extend `V4InvestigationResult` rather than replace (preserve `issue_key`, `retrieval_recall`)
- Extend `InvestigationTrace` with funnel fields (`pre_score_recall_15`, `triage_recall_7`, `phase2b_triggered`)
- `build_scoped_trace()` extended to handle funnel phases

## References

- [scoped-tools-adr.md](scoped-tools-adr.md) — V4→V4.1 pivot (predecessor)
- [topology-debate.md](topology-debate.md) — V4 topology ADR
- [mechanism-design.md](mechanism-design.md) — V4 governance mechanisms
- AgentSZZ (Lyu et al., 2026) — context compression, 5 tools, ReAct
- SWERANK (2025) — bi-encoder + listwise reranker
- RGFL (2025) — reasoning-guided ranking
- HiFL (2025) — hierarchical sample-and-select
- Agentless (Xia et al., 2024) — hierarchical prompting
- MAS-SZZ — multi-agent pipeline
- RAH (AgentPatterns.ai) — fan-out only with per-leaf verification
- Anthropic Agentic Coding Report (2026) — multi-agent coordination patterns
