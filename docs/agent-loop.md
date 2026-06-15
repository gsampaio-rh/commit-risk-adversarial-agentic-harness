# Agent Loop — Bug Attribution

The agent loop is the multi-turn search process that attributes a reported bug to an introducing commit.

## Pipeline

```
ProblemStatement (input, opaque to agent)
    |
    v
Attribution Agent (LLM, max 30 tool calls)
  Phases: Understand -> Search -> Examine -> Refine -> Conclude
    |
    v
Evidence Scorer (Script) --> grounding scores attached (suspects unchanged)
    |
    v
BugAttributionReport (assembled in investigate())
```

The Evidence Scorer runs **inside** `AgentOrchestrator.investigate()` after the agent loop completes and before the report is returned. It grades each suspect's evidence quotes against the commit diff and attaches scores to `report.metadata["evidence_scores"]`. Suspect rank and confidence are **not** modified. At eval time, `evaluate_attribution()` reuses these attached scores instead of re-fetching diffs.

The agent receives a `ProblemStatement` as a black-box input — it contains a title and description of the bug. How that statement was constructed (e.g. from a JIRA ticket via `ProblemExtractor`) is an eval/infrastructure concern, not part of the agent loop. See [architecture.md](architecture.md) for the full system including eval setup.

## Input

| Component | Source | Role |
|-----------|--------|------|
| `ProblemStatement` | Provided by caller (eval harness builds it from JIRA) | What broke, symptoms |
| `GitContextProvider` | Local clone, bound to `COMMIT_B~1` | All git reads |

## Tools

| Tool | Use case |
|------|----------|
| `search_commits_by_file` | Find commits touching a file |
| `search_commits_by_keyword` | Search commit messages |
| `get_commit_diff` | Inspect candidate changes |
| `get_commit_message` | Read author intent |
| `get_blame` | Trace line-level authorship |
| `get_file_at_commit` | Read file state |
| `list_recent_commits` | Browse recent history |

All tools enforce the temporal bound.

## Phases

| Phase | Activity |
|-------|----------|
| **Understand** | Parse problem; identify files, symbols, error patterns |
| **Search** | Execute git queries based on extracted signals |
| **Examine** | Read candidate diffs; correlate with symptoms |
| **Refine** | Narrow suspects; gather deeper evidence |
| **Conclude** | Rank suspects; emit AttributionResponse |

## Budget

| Limit | Value | On exceed |
|-------|-------|-----------|
| Tool calls | 30 | Force conclude |
| Tokens | 100,000 | Force conclude |
| Cost | $0.50 | Force conclude |

## Post-Processing (in-pipeline)

These stages run inside `investigate()` after the agent loop, before report return:

| Stage | Owner | Purpose |
|-------|-------|---------|
| Evidence Scorer | Script | Grade evidence quotes against suspect diffs; attach scores to metadata |
| Report Assembly | Script | Build `BugAttributionReport` with LLM suspects unchanged |

Eval metrics reuse `metadata["evidence_scores"]` when scoring D6 evidence grounding — no redundant diff fetches.

## Related

- [temporal-model.md](temporal-model.md) — COMMIT_B~1 bound
- [evaluation.md](evaluation.md) — Hit@k, MRR scoring
- [architecture.md](architecture.md) — system design
