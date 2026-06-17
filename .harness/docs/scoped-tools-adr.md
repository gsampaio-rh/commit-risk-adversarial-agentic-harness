# ADR: V4.1 Scoped Tools — Retrieval + Governed Examination

**Status:** Accepted
**Date:** 2026-06-17
**Deciders:** Builder + Evaluator (adversarial debate)
**Supersedes:** V4 metadata-only harness (Hit@5=0.062)

---

## 1. Context

V4 architecture separated retrieval (scripts, zero LLM cost) from reasoning (LLM, governed by harness). The agent pipeline used a 3-stage state machine: Planning → Examination → Attribution.

**Problem:** The LLM in V4 only saw commit metadata (SHA, message, date, files, retrieval signal). Without access to actual diffs, it could not perform causal attribution. Result: Hit@5=0.062 (1/16), 8x worse than V3's Hit@5=0.50.

**Root cause analysis** (from `.harness/docs/cursor-sdk-multi-turn.md`):
- Metadata alone insufficient for causal reasoning ("did this code change cause the bug?")
- Planning stage added overhead without measurable value
- Multi-turn context degradation between planning, examination, and attribution stages
- diff_summary field (300 chars of git show --stat) too truncated for real analysis

## 2. Options Considered

### Option A: Hybrid SDK Access (rejected)

Give the Cursor SDK agent `LocalAgentOptions(cwd=repo_path)` — full repo access.

**Rejected because:**
1. **Temporal bound not enforced.** SDK agent can `git show <fix_commit>`, violating oracle isolation.
2. **Invariant violation.** `llm_reasons_scripts_retrieve` explicitly states "The LLM never searches from scratch — it receives a curated CandidateSet." Raw repo access dissolves the retrieval/reasoning boundary.
3. **No governance.** SDK agent is a black box — no tool dispatch tracking, no budget enforcement, no scoping.
4. **CandidateSet becomes irrelevant.** Agent can ignore the candidate list and search freely.

### Option B: V3 Improvements (rejected)

Iterate on V3 prompts and tools without V4 retrieval.

**Rejected because:**
- V3 already at Hit@5=0.50 after extensive prompt engineering
- LLM wastes 25-30% of budget on mechanical search that scripts do better
- No structured retrieval — LLM might miss candidates that deterministic strategies would find

### Option C: Scoped Tools (accepted)

Keep V4 deterministic retrieval. Replace the 3-stage harness with a V3-style multi-turn loop using tools scoped to the CandidateSet.

## 3. Decision

**V4.1 = V4 Retrieval + Scoped V3 Tools**

```
JIRA → V4 Retrieval (scripts, 0 LLM) → CandidateSet
     → ScopedInvestigator (LLM + scoped tools) → Suspects
```

### Scoped tools

| Tool | Scope | Validation |
|------|-------|-----------|
| `get_commit_diff(sha)` | CandidateSet only | SHA must prefix-match a commit in the set |
| `get_commit_message(sha)` | CandidateSet only | Same |
| `get_blame(file)` | Any file | Temporal bound enforced by GitContextProvider |
| `get_file_at_commit(sha, file)` | CandidateSet only | SHA validation |

Search tools (`search_commits_by_file`, `search_commits_by_keyword`, `list_recent_commits`) are **not registered** — retrieval is already done.

### Investigation loop

1. System prompt: bug report + top 20 candidates (SHA, message, date, files, signal) + tool descriptions
2. LLM responds with `\`\`\`tool` blocks → harness dispatches, returns results
3. Budget: 15 tool calls, 8 turns (vs V3's 30/15 — smaller because no search needed)
4. LLM concludes with `\`\`\`suspects` block → harness parses and returns

### What's preserved from V4

- Deterministic retrieval (`prepare_investigation()`)
- Temporal bound enforcement (via `GitContextProvider`)
- `CandidateSet` as the agent's input boundary
- `ToolRegistry` and `ToolDefinition` infrastructure
- Structured traces (`TraceWriter`)
- Oracle isolation (ground truth never enters agent context)

### What's removed from V4

- 3-stage state machine (Planning → Examination → Attribution)
- `InvestigationBrief`, `BriefValidator`, `CompletionEvaluator`
- `PromptAssembler` 10-section template
- Multi-turn SDK sessions (`Agent.create()` + `agent.send()`)

## 4. Expected Outcome

- Hit@5 >= 0.40 (gate), targeting 0.50 (match V3)
- Latency: ~60-120s per case (V3 was ~120-240s, V4 was ~400s)
- Cost: ~$0.02-0.05 per case
- If scoped approach hits retrieval recall ceiling (8/20), Level 2 extractor is the next lever

## 5. Consequences

### Positive
- LLM sees actual diffs (the signal that made V3 work)
- Retrieval is still deterministic and fast (zero LLM cost)
- Temporal bound enforced on all tool calls
- CandidateSet scoping prevents LLM from wandering
- Simpler architecture (no state machine, no brief validation)

### Negative
- Planning and governance infrastructure (harness.py, brief_validator.py, completion.py) becomes dead code
- Less structured investigation — no explicit hypotheses or completion criteria
- Can't exceed retrieval recall ceiling without Level 2 extractor

### Risks
- Retrieval Recall@100 = 0.40 caps theoretical Hit@5 at 0.40
- SZZ noise (35%) means even perfect attribution would miss some cases
