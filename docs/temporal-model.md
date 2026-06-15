# Temporal Model — Information Asymmetry in Commit Investigation

This document defines the temporal boundary that separates investigation-time context from evaluation-time ground truth. It is the single most important design constraint in the system.

## Investigation-Time Visibility

At investigation time *T* (the date the suspect commit was authored), the agent sees only what a human reviewer would see when that commit landed. Nothing from the future exists.

### Allowed Sources

| Source | What it provides | Module |
|--------|------------------|--------|
| Git diff | Changed lines in the commit | `context/context_builder.py` via `git_context.py` |
| Commit message | Author's description of the change | `context/context_builder.py` |
| File history | Last N ancestor commits for touched files (pre-*T* only) | `context/git_context.py` |
| Author stats | Aggregated metrics from **train split only** (2003–2016) | `context/context_builder.py` → `AuthorStatsIndex` |
| Numeric CSV features | 12 code-metric fields via `_NUMERIC_FEATURES` allowlist: `la`, `ld`, `nf`, `nd`, `ns`, `ent`, `ndev`, `age`, `nuc`, `aexp`, `arexp`, `asexp` | `context/context_builder.py` |
| Router probability | XGBoost trained on train split, predicting on test commits | `routing/router.py` |
| Historical defect context (optional) | KNN over **train-split buggy commit messages only** — never fix-commit content. Enabled via `--enable-historical-defect-context` | `hypothesis/historical_rag.py` |

### Forbidden at Investigation Time

These fields exist in the data but are **never** passed to the agent or LLM:

- `buggy` — the ground truth label
- `fix` — whether the commit is itself a fix
- `year`, `author_date` — temporal metadata excluded to prevent leakage
- `commit_id` — passed as metadata key, never as a feature
- JIRA issue description, priority, resolution, components
- Fix-commit diff, files, or message
- Ground truth chain linkage (`bug_hash → fix_hash → issue_key`)

Enforcement: `_NUMERIC_FEATURES` allowlist in `CommitContextBuilder` + 7 oracle isolation tests in `tests/test_context_builder.py`. See [architecture.md §6](architecture.md) for the trust boundary diagram.

### Data Leakage Precedent

In an early iteration, the `buggy` label was accidentally passed through `csv_features`. D1 jumped to 0.86 — then collapsed to 0.40 when the leak was patched. This incident is why the allowlist exists and why oracle isolation has dedicated tests.

## Eval-Only Oracle

After the agent produces its `CommitInvestigationReport`, the evaluation harness compares the output against ground truth that **did not exist at investigation time**:

| Oracle source | Used for | Module |
|---------------|----------|--------|
| `buggy` label (CSV) | D1 — agent risk vs actual label | `runners/eval_harness.py` |
| Fix-commit files (from local git) | D2 — Jaccard overlap of localization claims vs fix diff | `runners/eval_harness.py` |
| JIRA issue description | D3 — LLM-as-judge comparing agent reasoning to real root cause | `runners/eval_judge.py` |
| JIRA priority | D4 — deterministic severity calibration | `runners/eval_harness.py` |
| Fix pattern (JIRA + fix diff) | D5 — LLM-as-judge comparing recommendations to actual fix | `runners/eval_judge.py` |
| Agent claims vs diff (no oracle) | D6 — automated evidence grounding, zero GT needed | `runners/eval_harness.py` |

JIRA metadata is fetched via the public Apache JIRA API with disk caching. It is **eval-only** — never injected into investigation context.

## SZZ and `buggy=True`

A commit marked `buggy=True` in the ApacheJIT CSV **introduced** a defect that was later fixed by another commit. The label is assigned retrospectively via SZZ-style bug-fix linkage (Keshavarz & Nagappan, MSR 2022, [Zenodo 5907847](https://zenodo.org/records/5907847)).

Key implications:

- A "buggy" commit is the one that **introduced** the problem, not the one that fixed it.
- The commit may appear completely innocuous at investigation time (e.g., whitespace changes, code-style fixes).
- The fix commit that reveals the actual defect may arrive days, weeks, or months later.
- D1 scoring: `buggy=True` requires agent risk `HIGH` or `CRITICAL`; `MEDIUM` on a buggy commit scores D1 = 0.

## Worked Example

### Bug-introducing commit: `ad6a796f` (2018-12-07)

- **Message:** `CAMEL-12978 - Fixed CS`
- **Diff:** Whitespace and code-style changes across several files

**Eval-only oracle (not injected into investigation context):**

- **JIRA CAMEL-12978** (eval-only): Type "Improvement" (eval-only), summary "Add support to configure CamelContext for KIE-Server" (eval-only)

**What the agent sees at time *T*:** The diff (cosmetic changes), the commit message ("Fixed CS"), numeric features, file history, router prior. No JIRA metadata — the ticket existed at *T* but remains eval-only until `v2-jira-context-injection` ships.

**Agent's assessment:** `MEDIUM` risk — defensible given the visible evidence.

### Fix commit: `3ad14ea4696c` (2019-02-01)

- **Message:** `CAMEL-13152: KJAR classloaded now set as CamelContext classloader for deployment-scoped contexts`
- **Gap:** 56 days between bug introduction and fix

**Eval-only oracle (not injected into investigation context):**

- **JIRA CAMEL-13152 description** (eval-only)

**What the agent never sees:** The fix commit, its diff, JIRA CAMEL-13152 description (eval-only), or the linkage between the two commits. This information is ground truth for D2, D3, and D5 scoring only.

### Timeline

```
2018-12-07  ad6a796f  "CAMEL-12978 - Fixed CS"           ← agent investigates HERE
    │
    │  (agent sees only what exists at this point)
    │
    ▼  56 days pass...
    │
2019-02-01  3ad14ea4  "CAMEL-13152: KJAR classloader..."  ← eval oracle only
```

This example illustrates why some `buggy=True` commits are structurally hard to detect: the defect they introduce is not visible in their own diff.

## JIRA Context Injection — Temporal Rules

Task `v2-jira-context-injection` injects the JIRA ticket **referenced in the commit message** into investigation context. This ticket existed before the commit was written, so it is temporally valid.

### Valid: Commit-message JIRA key (Ticket B)

```
Ticket B exists → Developer reads B → Commits change referencing B in message
                                       (this commit may accidentally introduce a new bug)
```

The JIRA key in the commit message (e.g., `CAMEL-10799`) is the task the developer was working on. Its title and type are commit-time information — the developer had this context when writing the code.

### INVALID: Ground-truth-chain JIRA key (Ticket C)

```
Bug commit → [months pass] → Bug discovered → Ticket C created → Fix commit references C
```

Ticket C (e.g., `CAMEL-11953`) describes the bug that the commit **introduced**. It was created AFTER the commit. Injecting it is oracle leakage — equivalent to telling the agent "here's what will break."

### Extraction Method

The correct source for commit-time JIRA keys is **regex extraction from the commit message**:

```python
import re
match = re.search(r'([A-Z][A-Z0-9]+-\d+)', commit_message)
```

Do NOT use `GroundTruthGraph.get_chain()` — that resolves the future fix→issue linkage.

### Violation Record (2026-06-15)

Initial implementation of `scripts/build_jira_csv.py` used `gt.get_chain(bug_commit).issue_keys` which resolves through the fix commit to the future ticket. The n=20 eval (D3=0.375) was invalid — it achieved the lift by injecting oracle information. Corrected to use commit-message extraction.

## Related

- [architecture.md §6](architecture.md) — trust boundary diagram (investigation vs evaluation)
- [evaluation.md](evaluation.md) — D1–D6 rubrics and how each oracle feeds scoring
- [experiment-context.md](experiment-context.md) — oracle isolation rules and data leakage incident
- [datasets.md](datasets.md) — ApacheJIT splits and ground truth chain files
