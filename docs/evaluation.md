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

**Oracle selection**: D3 uses a tiered oracle strategy. The `judge_oracle` tag in `DimensionScore.details` records which oracle was used:

| Condition | Oracle | `judge_oracle` tag |
|---|---|---|
| JIRA description present | JIRA issue description | `judge_oracle=jira` |
| JIRA description absent, fix files available | Fix diff contents | `judge_oracle=fix-diff-fallback` |
| JIRA description absent, no fix files | Score 0 (no oracle) | `judge_oracle=unavailable` |

The fix-diff fallback (`D3_FIX_DIFF_FALLBACK_RUBRIC`) uses the same 0–4 rubric but replaces the JIRA description context with the set of files touched by the fix commit. This prevents systematic bias against commits where JIRA descriptions are empty (e.g., `f897d46870ba`).

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

Defined before the n=100 real eval run (2026-06-10). Three tiers: GATE blocks V1 delivery, TARGET is the quality bar, STRETCH marks excellence.

| Dimension | GATE (blocks V1) | TARGET (deliverable) | STRETCH (excellent) |
|-----------|-------------------|----------------------|---------------------|
| **D1** Prediction | >= 0.70 | >= 0.80 | >= 0.90 |
| **D2** Localization | >= 0.15 | >= 0.25 | >= 0.40 |
| **D3** Diagnosis | >= 0.20 | >= 0.35 | >= 0.50 |
| **D4** Severity | >= 0.60 | >= 0.75 | >= 0.85 |
| **D5** Recommendations | >= 0.25 | >= 0.40 | >= 0.55 |
| **D6** Evidence grounding | >= 0.60 | >= 0.70 | >= 0.80 |

**GATE rule:** All six gates must pass simultaneously on a stratified eval with n >= 50 (50/50 buggy/clean). Any single dimension below its gate blocks V1 delivery.

**Baseline soft constraint:** If D1 agent < D1 baseline (always-predict-clean or router-only), emit WARNING.

## Sampling Strategy

- Stratified sample from **routed commits** within budget tier
- 50/50 buggy/clean split prioritizing buggy commits with full GT chains (fix + JIRA linkage)
- Gray-zone + high-zone focus where investigation adds value
- Router-only baseline (no LLM) reported on same sample for comparison

## LLM Provider

Primary: **Cursor SDK** (`cursor-sdk/claude-sonnet-4-6`). Same provider for investigation and D3/D5 judging. Fallback: OpenAI → Ollama → Mock.

## Run Infrastructure

Each eval run creates a timestamped folder:

```
output/runs/YYYY-MM-DD_HH-MM-SS_<real|mock>_n<count>/
├── run-config.json          # CLI args, git rev, python version, stratification
├── run.log                  # full timestamped log
├── investigations/          # per-commit agent reports
│   └── <hash>_<project>.json
├── evaluations/             # per-commit eval scores vs ground truth
│   └── <hash>_<project>.json
├── eval-report.json         # aggregate D1–D6 scores, baselines, strata
└── eval-report.md           # human-readable report
```

---

## Results

### iter-1 Production (Claude Sonnet 4.6, n=20 stratified)

**Run:** `output/runs/2026-06-10_17-12-55_real_n20/`
**Provider:** cursor-sdk/claude-sonnet-4-6
**Sample:** 10 buggy (full GT chain) + 10 clean
**Cost:** $0.097 (24 minutes)

| Dimension | Score | Buggy (n=10) | Clean (n=10) | GATE | Status |
|-----------|-------|-------------|-------------|------|--------|
| D1 Prediction | 0.60 | 0.80 | 0.40 | >= 0.70 | FAIL (iter-1 target 0.50 PASS) |
| D2 Localization | 0.15 | 0.30 | 0.00 | >= 0.15 | PASS (at boundary) |
| D3 Diagnosis | 0.20 | 0.20 | — | >= 0.20 | PASS (at boundary) |
| D4 Severity | **0.90** | 0.90 | — | >= 0.60 | PASS |
| D5 Recommendations | **0.37** | 0.37 | — | >= 0.25 | PASS |
| D6 Evidence grounding | **0.85** | 0.90 | 0.80 | >= 0.60 | PASS |

**Baselines:** Agent D1=0.60 beats always-predict-clean (0.50) and router-only (0.50).

### iter-1 Commit Smoke (Claude, named commits)

**Run:** `output/runs/2026-06-10_16-51-19_real_n3/`

| Commit | Risk | D1 | D2 | D3 | D6 | Notes |
|--------|------|----|----|----|----|----|
| f897d46 | HIGH (0.68) | 1.0 | 1.0 | 0.0 | 1.0 | D3=0 because JIRA has no description (judge infra) |
| 90846b5 | HIGH (0.72) | 1.0 | 0.17 | 0.0 | 1.0 | Wrong root cause — binary incompat vs actual deadlock |
| b4c933b7 | HIGH (0.78) | 1.0 | 0.04 | **0.75** | 1.0 | Correct classloader mechanism (judge 3/4) |

### Pre-iter-1 Baseline (post-leak-fix, n=5)

**Run:** `output/runs/2026-06-10_13-17-45_real_n5/`
**Provider:** cursor-sdk/claude-sonnet-4-6

| Dimension | Score | GATE | Status |
|-----------|-------|------|--------|
| D1 | 0.40 | >= 0.70 | FAIL |
| D2 | 0.08 | >= 0.15 | FAIL |
| D3 | 0.13 | >= 0.20 | FAIL |
| D4 | **0.84** | >= 0.60 | PASS |
| D5 | **0.50** | >= 0.25 | PASS |
| D6 | **0.85** | >= 0.60 | PASS |

### iter-1 Impact Summary

| Dimension | Baseline | iter-1 (n=20) | Delta | iter-1 target | Status |
|-----------|----------|--------------|-------|---------------|--------|
| D1 | 0.40 | 0.60 | **+0.20** | >= 0.50 | PASS |
| D2 | 0.08 | 0.15 | +0.07 | — | — |
| D3 | 0.13 | 0.20 | **+0.07** | >= 0.18 | PASS |
| D4 | 0.84 | 0.90 | +0.06 | — | — |
| D5 | 0.50 | 0.37 | -0.13 | — | — |
| D6 | 0.85 | 0.85 | 0.00 | >= 0.70 | PASS |

### Key Findings (iter-1)

1. **D1 lift is real** — rubric + staged CoT + router probability moved D1 from 0.40 to 0.60. Buggy recall is 0.80 (8/10 correctly HIGH).
2. **D3 at boundary** — moved from 0.13 to 0.20. Some commits get precise root cause (b4c933b7 D3=0.75) but others miss entirely (90846b5 D3=0.0).
3. **False positive problem** — 6/10 clean commits classified HIGH. Claude generates convincing SUPPORTED hypotheses even on clean commits.
4. **D6 stable** — grounding quality unchanged at 0.85. The rubric did not cause guessing.
5. **D5 regressed** — from 0.50 to 0.37. The more specific reasoning may be producing more specific but wrong recommendations.

---

### V1 Delivery (n=50, all six GATE thresholds)

**Run:** `output/runs/2026-06-11_06-13-50_real_n50/`
**Commit:** `ce394c4`
**Sample:** 25 buggy + 25 clean (stratified)
**Cost:** ~$0.35

| Dimension | Score | GATE | TARGET | Status |
|-----------|-------|------|--------|--------|
| D1 Prediction | 0.70 | >= 0.70 | >= 0.80 | GATE PASS / target miss |
| D2 Localization | 0.19 | >= 0.15 | >= 0.25 | GATE PASS / target miss |
| D3 Diagnosis | 0.23 | >= 0.20 | >= 0.35 | GATE PASS / target miss |
| D4 Severity | **0.894** | >= 0.60 | >= 0.75 | PASS |
| D5 Recommendations | **0.413** | >= 0.25 | >= 0.40 | PASS |
| D6 Evidence grounding | **0.770** | >= 0.60 | >= 0.70 | PASS |

**Verdict:** V1 delivery confirmed — all 6 GATE thresholds pass. D1/D2/D3 below TARGET tier → V2 experiment activated.

---

### V2 Run 1 — n=50 (selector fix + bundle expand + D2 localization + H3a)

**Run:** `output/runs/2026-06-11_23-57-42_real_n50/`
**Cost:** $0.395
**Changes vs V1:** contrastive hypothesis, selector fix, bundle expand, D2 SUPPORTED-only filter

| Dimension | Score | TARGET | Status |
|-----------|-------|--------|--------|
| D1 Prediction | 0.70 | >= 0.80 | FAIL |
| D2 fix_chain_only | 0.334 | >= 0.25 | PASS |
| D3 Diagnosis | 0.29 | >= 0.35 | FAIL |
| D4 Severity | 0.894 | >= 0.75 | PASS |
| D5 Recommendations | 0.427 | >= 0.40 | PASS |
| D6 Evidence grounding | 0.780 | >= 0.70 | PASS |
| FP rate | 32% | <= 25% | FAIL |

**Verdict:** FAIL. D1/FP regression — clean FP (32%) caused by test/examples commits. Activated FP-fix-archetype.

---

### V2 Run 2 — n=50 (+ archetype FP fix + H3a RAG)

**Run:** `output/runs/2026-06-12_03-55-28_real_n50/`
**Cost:** $0.395
**Changes vs V2 run 1:** archetype FP fix (test-only/examples-only commits), H3a historical RAG

| Dimension | Score | TARGET | Status |
|-----------|-------|--------|--------|
| D1 Prediction | 0.72 | >= 0.80 | FAIL (-0.08) |
| D2 fix_chain_only | **0.384** | >= 0.25 | **PASS** |
| D3 Diagnosis | 0.31 | >= 0.35 | FAIL (-0.04) |
| D4 Severity | 0.881 | >= 0.75 | **PASS** |
| D5 Recommendations | 0.387 | >= 0.40 | marginal |
| D6 Evidence grounding | **0.780** | >= 0.70 | **PASS** |
| FP rate | **24%** | <= 25% | **PASS** |

**Verdict:** FAIL on D1 and D3. FP fixed (32%→24%). Root causes:
- **D1=0.72:** 8 BUG→MEDIUM (hidden-fix-in-CS commits, diff looks cosmetic), 6 clean→HIGH (≥2 SUPPORTED findings, policy cannot distinguish)
- **D3=0.31:** Prompt-engineering ceiling confirmed at 0.28–0.31. JIRA context not available to agent. See [prompt-engineering-ceiling analysis](../.harness/.archive/docs/prompt-engineering-ceiling.md) (archived).

**Next:** `v2-jira-context-injection` (D3 ceiling), `spike-confidence-equation` (D1/FP calibration).

---

## Data Leakage Fix (2026-06-10)

**All eval runs before `real_n5` (2026-06-10_13-17-45) are invalidated.** The `buggy` label was being passed to the agent as part of `csv_features`. Fixed by switching to an allowlist of numeric features only. Prior D1 scores (0.85–0.86) were inflated.

## Human Audit

Post-fix eval should be reviewed for D3–D5 judge drift detection. Protocol: compare judge scores against human ratings on the same rubric. Pending until n=50+ clean run available.

---

## Related

- [architecture.md](architecture.md) — evaluation as feedback loop (§4)
- [harness.md](harness.md) — evaluation framework operational details
- [experiment-context.md](experiment-context.md) — thesis and oracle isolation
- [datasets.md](datasets.md) — ApacheJIT ground truth chain
