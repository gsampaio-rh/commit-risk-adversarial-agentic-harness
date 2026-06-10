# Evaluation Framework & Results

Six-dimension evaluation comparing agent investigation output to ApacheJIT ground truth.

## Dimensions

| ID | Dimension | Criterion | Automation |
|----|-----------|-----------|------------|
| **D1** | Prediction | Agent risk level vs buggy/clean label from CSV | Deterministic |
| **D2** | Localization | Agent cited files vs fix-commit diff files (Jaccard overlap) | Deterministic |
| **D3** | Diagnosis | Agent reasoning vs JIRA issue description | LLM-as-judge (rubric 0–4) |
| **D4** | Severity | Agent risk level vs JIRA priority (normalized mapping) | Deterministic |
| **D5** | Recommendations | Agent recommendations vs actual fix pattern | LLM-as-judge (rubric 0–3) |
| **D6** | Evidence grounding | Agent claims vs actual diff/files (no LLM cost) | Automated |

### D3 Root-Cause Faithfulness (LLM-as-judge)

Rubric 0–4:
- **0**: Generic boilerplate — reasoning has no connection to the actual bug
- **1**: Vaguely related — mentions the right area but no specifics
- **2**: Partially correct — identifies some aspects but misses the core mechanism
- **3**: Mostly correct — captures the key failure mechanism but misses details
- **4**: Precise match — reasoning accurately describes the root cause

### D5 Recommendation Relevance (LLM-as-judge)

Rubric 0–3:
- **0**: Irrelevant — no connection to the actual fix
- **1**: Tangentially related — right direction, wrong specific action
- **2**: Relevant — aligns with the fix pattern but lacks precision
- **3**: Precise match — recommendations directly describe the applied fix

### D6 Evidence Grounding (automated, zero LLM cost)

Scores 0–4 based on four grounding signals:
1. Agent provides localization claims (specific file paths)
2. Localization files overlap with actual touched files
3. Reasoning mentions specific file names from the diff
4. Evidence content contains diff-specific strings (not generic)

D6 catches boilerplate reports that classify correctly (high D1) but cite no concrete artifacts.

## Acceptance Thresholds

Defined before the n=100 real eval run (2026-06-10). Three tiers: GATE blocks V1 delivery, TARGET is the quality bar for an acceptable deliverable, STRETCH marks excellence.

| Dimension | GATE (blocks V1) | TARGET (deliverable) | STRETCH (excellent) |
|-----------|-------------------|----------------------|---------------------|
| **D1** Prediction | >= 0.70 | >= 0.80 | >= 0.90 |
| **D2** Localization | >= 0.15 | >= 0.25 | >= 0.40 |
| **D3** Diagnosis | >= 0.20 | >= 0.35 | >= 0.50 |
| **D4** Severity | >= 0.60 | >= 0.75 | >= 0.85 |
| **D5** Recommendations | >= 0.25 | >= 0.40 | >= 0.55 |
| **D6** Evidence grounding | >= 0.60 | >= 0.70 | >= 0.80 |

**GATE rule:** All six gates must pass simultaneously on a stratified eval with n >= 50 (50/50 buggy/clean). Any single dimension below its gate blocks V1 delivery.

**Baseline soft constraint:** If D1 agent < D1 baseline (always-predict-clean or router-only), emit WARNING in the eval report. Documented, not blocking.

**Calibration note:** These thresholds were set using only the n=20 judge-v1 results (D1=0.85, D2=0.13, D3=0.20, D4=0.84, D5=0.43, D6=0.75). The n=20 results fail D2 GATE (0.13 < 0.15) and are at the edge of D3 GATE (0.20 = 0.20). This is intentional — gates should have teeth.

## Sampling Strategy

- Stratified sample from **routed commits** within budget tier
- 50/50 buggy/clean split prioritizing buggy commits with full GT chains (fix + JIRA linkage)
- Gray-zone + high-zone focus where investigation adds value
- Router-only baseline (no LLM) reported on same sample for comparison

## LLM Provider

Primary: **Cursor SDK** (`cursor-sdk/claude-sonnet-4-6`). Same provider used for both agent investigation and D3/D5 judging. Fallback: OpenAI. Offline: deterministic mock.

## Run Infrastructure

Each eval run creates a timestamped folder:

```
output/runs/YYYY-MM-DD_HH-MM-SS_<real|mock>_n<count>/
├── run-config.json          # CLI args, git rev, python version, stratification
├── run.log                  # full timestamped log
├── investigations/          # per-commit agent reports (what the agent produced)
│   ├── <hash>_<project>.json
│   └── ...
├── evaluations/             # per-commit eval scores (how it scored vs ground truth)
│   ├── <hash>_<project>.json
│   └── ...
├── eval-report.json         # aggregate D1–D6 scores, baselines, strata (unified)
└── eval-report.md           # human-readable report
```

## Data Leakage Fix (2026-06-10)

**All eval runs before `real_n5` (2026-06-10_13-17-45) are invalidated.** The `buggy` label from the CSV was being passed to the agent as part of `csv_features`, allowing the agent to read the ground truth answer during investigation. This was fixed by excluding `buggy` from the context builder's feature passthrough. Prior D1 scores (0.85–0.86) were inflated; the agent was partially reading the label instead of investigating.

## Real Eval Results (post-leak-fix, n=5 smoke)

**Configuration:** 5 commits (2 buggy with full GT chain, 3 clean), `cursor-sdk/claude-sonnet-4-6`, real git clones, real JIRA, LLM-as-judge for D3/D5. First clean run after `buggy` label leak fix.

### Headline Scores

| Dimension | Score | Buggy stratum (n=2) | Clean stratum (n=3) | GATE | Status |
|-----------|-------|---------------------|---------------------|------|--------|
| D1 Prediction | 0.40 | 0.00 | 0.67 | >= 0.70 | **FAIL** |
| D2 Localization | 0.08 | 0.20 | 0.00 | >= 0.15 | **FAIL** |
| D3 Diagnosis | 0.13 | 0.13 | — | >= 0.20 | **FAIL** |
| D4 Severity | **0.84** | 0.84 | — | >= 0.60 | PASS |
| D5 Recommendations | **0.50** | 0.50 | — | >= 0.25 | PASS |
| D6 Evidence grounding | **0.85** | 0.88 | 0.83 | >= 0.60 | PASS |

### Baselines

| Baseline | D1 Score |
|----------|----------|
| Agent (claude-sonnet-4-6) | 0.40 |
| Always-predict-clean | 0.60 |
| Router-only | 0.40 |

**WARNING:** Agent D1 (0.40) does not beat always-predict-clean baseline (0.60).

### Key Findings

- **D1 collapsed from 0.86 to 0.40** after removing the `buggy` label leak — confirms the prior score was artificial
- **D1 buggy stratum = 0.00**: agent classified both buggy commits as MEDIUM instead of HIGH. Without the label hint, it under-estimates risk on genuinely buggy commits
- **D6 = 0.85 remains strong**: the agent still cites real files and diff content — the grounding quality is independent of the label leak
- **D4 = 0.84 and D5 = 0.50 held steady**: severity calibration and recommendation quality were not driven by the label
- **n=5 is too small for conclusions** — 2 buggy commits is statistically fragile. Requires n=50+ to validate

### Cost

| Phase | Cost | Per commit |
|-------|------|-----------|
| Investigation (5 commits) | $0.014 | ~$0.003 |
| Judge (2 buggy × D3+D5) | ~$0.006 | ~$0.003 |

## Invalidated Results (pre-leak-fix, for reference only)

Prior runs (judge-v1 n=20, real n=100) had inflated D1 scores due to `buggy` label leakage. Archived in `output/runs/` for reference but not valid for acceptance threshold evaluation.

| Run | D1 | D6 | Note |
|-----|----|----|------|
| judge-v1 (n=20) | 0.85 | 0.75 | `buggy` in csv_features |
| real_n100 | 0.86 | 0.84 | `buggy` in csv_features |
| **real_n5 (clean)** | **0.40** | **0.85** | **First clean run** |

## Next Steps to Reach Acceptance Thresholds

The agent currently fails D1, D2, and D3 gates. These are the improvement paths, ordered by expected impact:

### 1. Fix D1 — Prediction (0.40 → gate 0.70)

The agent defaults to MEDIUM too often. Without the `buggy` label, it lacks a strong signal to push toward HIGH/CRITICAL.

| Approach | Effort | Expected impact |
|----------|--------|-----------------|
| **Prompt engineering**: add explicit classification rubric mapping diff characteristics to risk levels | Low | High — the agent reasons well (D6=0.85) but doesn't translate findings to HIGH/CRITICAL |
| **Inject router score**: pass the XGBoost probability to the agent as context (not the label) | Low | Medium — gives the agent a calibrated prior without leaking ground truth |
| **Multi-turn follow-up on MEDIUM**: when agent says MEDIUM, trigger a focused second turn asking "is this closer to LOW or HIGH?" | Medium | Medium — forces a binary decision |
| **Tune risk thresholds**: consider MEDIUM as "risky" for D1 scoring (not just HIGH/CRITICAL) | Low | High on metric, but changes the evaluation semantics |

### 2. Fix D2 — Localization (0.08 → gate 0.15)

The agent localizes files but not the *right* files (low Jaccard overlap with fix-commit files).

| Approach | Effort | Expected impact |
|----------|--------|-----------------|
| **Prompt engineering**: ask the agent to distinguish "files changed" from "files most likely to contain the bug" | Low | Medium — the agent currently lists all touched files |
| **Multi-turn with file history**: on turn 2, show the agent recent changes to its localized files and ask it to narrow down | Medium | Medium — more context may help |
| **Weight localization by confidence**: let the agent rank files by suspicion level | Low | Low — helps readability but doesn't fix accuracy |

### 3. Fix D3 — Diagnosis (0.13 → gate 0.20)

The hardest dimension. The agent reasons about code structure but misses the actual bug mechanism.

| Approach | Effort | Expected impact |
|----------|--------|-----------------|
| **Richer context**: include test files in the diff (test failures often describe the bug) | Medium | High — test names and assertions are strong bug indicators |
| **Multi-turn investigation**: turn 2 asks "what specific failure mode could this introduce?" instead of generic follow-up | Medium | Medium — forces specificity |
| **Few-shot examples**: include 2-3 example investigation reports in the system prompt showing good root-cause analysis | Low | Medium — calibrates the agent's reasoning depth |
| **Longer diff window**: increase from 4K to 8K chars to avoid truncating critical context | Low | Low-Medium — some diffs are truncated before the buggy hunk |

### Priority Order

1. **Prompt engineering for D1** (low effort, high impact) — add risk classification rubric + inject router probability
2. **Few-shot examples for D3** (low effort, medium impact) — show the agent what good investigation looks like
3. **Re-run n=50** to validate improvements against thresholds
4. **Multi-turn for D1/D3** (medium effort) — only if prompt changes don't reach gate
5. **Localization focus for D2** (medium effort) — likely improves with better D3

## Human Audit

Post-fix eval sample should be reviewed for D3–D5 judge drift detection. Protocol: compare judge scores against human ratings on the same rubric. Pending until n=50+ clean run available.

## Related Documents

- [Experiment context](experiment-context.md) — thesis, dimensions, cost governance
- [Datasets](datasets.md) — ApacheJIT ground truth chain
- [Architecture](../ARCHITECTURE.md) — six-dimension harness component design
