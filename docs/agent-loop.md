# Agentic Loop — Bug Attribution

The agentic loop is the entire investigation inside `AgentOrchestrator.investigate()` — from `ProblemStatement` to `BugAttributionReport`. It runs as seven stages: five advisory LLM phases sharing a single multi-turn tool loop, followed by two unconditional script steps.

> **Canonical specification:** [system-specification.md — Agentic Loop](system-specification.md#agentic-loop) has the full stage details, loop mechanics, exit conditions, and code references.

## Seven Stages

| # | Name | Owner | Evaluation Dimension |
|---|------|-------|---------------------|
| 1 | Problem Analysis | LLM | — |
| 2 | Search | LLM via tools | — |
| 3 | Examine | LLM via tools | Retrieval Recall |
| 4 | Refine | LLM via tools | Retrieval Recall |
| 5 | Conclude | LLM | Hit@k, MRR, D3 |
| 6 | Evidence Scoring | Script | D6 Evidence Grounding |
| 7 | Report Assembly | Script | Schema tests |

**Stages 1–5** are advisory phases from `_SYSTEM_PROMPT_TEMPLATE` (Understand → Search → Examine → Refine → Conclude). They are **not** enforced as sequential gates — the LLM may interleave search, examine, and refine across turns within a single multi-turn loop.

**Stages 6–7** always run after the LLM loop exits, regardless of exit condition (including empty suspects). Evidence scores are **metadata only** — suspect rank and confidence are never modified.

## Pipeline

```
ProblemStatement (input)
    │
    ▼
┌─────────────────────────────────────────────┐
│  Stages 1–5: Multi-turn LLM tool loop       │
│  (advisory phases, shared code path)        │
│                                              │
│  Budget: 30 tool calls, 100K tokens,        │
│          $0.50, 15 turns (collective)        │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│  Stage 6: Evidence Scoring (script)          │
│  _attach_evidence_scores() → quote_in_diff() │
│  3-tier cascade: exact → normalized → fuzzy  │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│  Stage 7: Report Assembly (script)           │
│  return BugAttributionReport(...)            │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
BugAttributionReport (output)
```

## Input

| Component | Source | Role |
|-----------|--------|------|
| `ProblemStatement` | Provided by caller (eval harness builds it from JIRA) | What broke, symptoms |
| `GitContextProvider` | Local clone, bound to `COMMIT_B~1` | All git reads |

## Tools (stages 2–4)

| Tool | Typical stage | Use case |
|------|---------------|----------|
| `search_commits_by_file` | Search | Find commits touching a file |
| `search_commits_by_keyword` | Search | Search commit messages |
| `list_recent_commits` | Search | Browse recent history |
| `get_commit_diff` | Examine | Inspect candidate changes |
| `get_commit_message` | Examine | Read author intent |
| `get_file_at_commit` | Examine | Read file state at commit |
| `get_blame` | Refine | Trace line-level authorship |

All tools enforce the temporal bound. Stage association is typical, not enforced.

## Resource Limits (stages 1–5 collectively)

| Limit | Value | Enforced by |
|-------|-------|-------------|
| Tool calls | 30 | `BudgetState.total_tool_calls` (mid-batch cutoff) |
| Tokens | 100,000 | `BudgetState.budget_exceeded` |
| Cost | $0.50 | `BudgetState.budget_exceeded` |
| Turns | 15 | `for turn in range(self._max_turns)` (separate from budget) |

## Stage-to-Metric Mapping

See [evaluation-framework.md — Stage-to-Metric Mapping](evaluation-framework.md#stage-to-metric-mapping) for how each stage connects to evaluation metrics.

Key insight: **Retrieval Recall** only counts `commit_id` in `tool_trace[].args` (examine/refine tools), not SHAs in search result text. A case can have low retrieval recall even if search listed the right commit.

## Related

- [system-specification.md](system-specification.md) — full spec: loop mechanics, exit conditions, tool/suspect formats, prompt structure
- [evaluation-framework.md](evaluation-framework.md) — metrics, stage-to-metric mapping, baselines
- [system-specification.md — Temporal Model](system-specification.md#temporal-model) — COMMIT_B~1 bound
- [glossary.md](glossary.md) — term definitions
