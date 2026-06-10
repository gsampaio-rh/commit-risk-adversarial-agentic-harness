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
├── eval-report.json         # aggregate D1–D6 scores, baselines, strata
├── eval-report.md           # human-readable report
└── investigations/          # per-commit investigation reports
    ├── <hash>_<project>.json
    └── ...
```

## Real Eval Results (judge-v1, 20 commits)

**Configuration:** 20 commits (10 buggy with full GT chain, 10 clean), `cursor-sdk/claude-sonnet-4-6`, real git clones, real JIRA, LLM-as-judge for D3/D5.

### Headline Scores

| Dimension | Score | Buggy stratum | Clean stratum |
|-----------|-------|---------------|---------------|
| D1 Prediction | **0.85** | 0.70 | 1.00 |
| D2 Localization | 0.13 | 0.27 | 0.00 |
| D3 Diagnosis | 0.20 | 0.20 | — |
| D4 Severity | 0.84 | 0.84 | — |
| D5 Recommendations | 0.43 | 0.43 | — |
| D6 Evidence grounding | **0.75** | 0.78 | 0.73 |

### Baselines

| Baseline | D1 Score |
|----------|----------|
| Agent (claude-sonnet-4-6) | **0.85** |
| Always-predict-clean | 0.50 |
| Router-only (INVESTIGATE/HIGH=risky) | 0.50 |

### Key Findings

- **D1 = 0.85** beats both baselines (always-clean 0.50, router-only 0.50) on a balanced 50/50 sample
- **D3 = 0.20** is the honest signal — judge catches that most agent reasoning is directionally correct but misses the actual root-cause mechanism. One commit scored 4/4 (perfect root-cause match)
- **D6 = 0.75** confirms the agent isn't generating boilerplate — it cites real file names and diff content
- **The D6↔D3 gap** (0.75 vs 0.20) is the investigation quality gap: the agent reads the diff faithfully but can't deduce the underlying bug without running the code
- **Cost**: $0.066 total (~$0.003/commit investigation + ~$0.003/commit judging)

### Cost Breakdown

| Phase | Cost | Per commit |
|-------|------|-----------|
| Investigation (20 commits) | $0.066 | ~$0.003 |
| Judge (10 buggy × D3+D5) | ~$0.030 | ~$0.003 |
| D6 grounding | $0 | $0 |

## Provable Claims

1. **Agent beats both baselines on balanced sample**: D1=0.85 vs always-clean=0.50 and router-only=0.50
2. **Agent produces grounded evidence (D6=0.75)**: not boilerplate — cites real files from the diff
3. **LLM-as-judge D3 catches reasoning failures**: 6/10 buggy commits scored 0 (generic reasoning), proving word-overlap D3 was inadequate
4. **D4 severity alignment is strong (0.84)**: agent risk levels match JIRA priority well
5. **End-to-end pipeline validated**: route → real git context → Cursor SDK investigation → JIRA-backed evaluation → timestamped artifacts

## Non-Provable / Requires More Data

1. **D3 improvement path**: requires prompt engineering or multi-turn investigation to improve root-cause identification
2. **D2 localization at scale**: n=20 with Jaccard=0.27 on buggy — needs larger sample for statistical confidence
3. **Cost-effectiveness of routing at $50 tier**: not yet tested at 300-commit scale
4. **Cross-project generalization**: only Camel + Hadoop tested
5. **Judge reliability**: no human audit of D3/D5 judge scores yet (20-commit protocol TBD)

## Human Audit

20-commit sample should be reviewed for D3–D5 judge drift detection. Protocol: compare judge scores against human ratings on the same rubric. Pending.

## Related Documents

- [Experiment context](experiment-context.md) — thesis, dimensions, cost governance
- [Datasets](datasets.md) — ApacheJIT ground truth chain
- [Architecture](../ARCHITECTURE.md) — six-dimension harness component design
