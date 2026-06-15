> **DEPRECATED** — This document is superseded by [system-specification.md](system-specification.md) (see "Temporal Model" section). Kept for historical reference only.

# Temporal Model — Bug Attribution Information Boundary

This document defines the temporal boundary that separates investigation-time context from evaluation-time ground truth.

## Mental Model

The agent simulates an engineer at **bug-report time** — after the defect exists but **before any fix commit lands**.

```
[bug introduced] --> [bug reported in JIRA] --> agent investigates HERE --> [fix commit(s)]
                         ^ input                    ^ git bound = COMMIT_B~1      ^ invisible
```

## Investigation-Time Visibility

### Allowed Sources

| Source | What it provides | Module |
|--------|------------------|--------|
| JIRA ticket | Title + description (symptoms, stack traces, repro steps) | `context/problem_extractor.py` |
| Git history (pre-bound) | Commits, diffs, messages, blame at or before `COMMIT_B~1` | `context/git_context.py` |

### Forbidden at Investigation Time

| Field | Why forbidden |
|-------|---------------|
| `bug_hash` label | Ground truth answer |
| Fix commit diff, message, or touched files | Reveals the fix |
| `COMMIT_B` content | Beyond temporal bound |
| Ground truth chain linkage | Retrospective SZZ assignment |

## Temporal Bound: COMMIT_B~1

| Rule | Detail |
|------|--------|
| Bound definition | `COMMIT_B~1` = parent of earliest fix commit |
| Multi-fix policy | Use the **earliest** fix commit as `COMMIT_B` |
| Fix commit invisibility | `COMMIT_B`'s diff, message, metadata NOT visible |
| Bound source | Fix commit SHA sets the bound, not investigation context |

## GitContextProvider Enforcement

Every git read verifies ancestry before returning data:

```
git merge-base --is-ancestor <requested_commit> <COMMIT_B~1>
```

If the requested commit is not an ancestor of `COMMIT_B~1`, the provider returns an error.

## Eval-Only Oracle

| Oracle source | Used for |
|---------------|----------|
| `bug_hash` (commit_links.csv) | Hit@k, MRR |
| JIRA description | D3 Attribution judge |
| Suspect commit diffs | D6 Evidence grounding |
| Search trace | Retrieval Recall |

## Related

- [architecture.md](architecture.md) — trust boundary diagram
- [evaluation.md](evaluation.md) — Hit@k, MRR, D3, D6 rubrics
- [agent-loop.md](agent-loop.md) — multi-turn search loop and tools
