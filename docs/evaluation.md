# Evaluation Framework & First Run Results

Five-dimension evaluation comparing agent investigation output to ApacheJIT ground truth.

## Dimensions

| ID | Dimension | Criterion | Automation |
|----|-----------|-----------|------------|
| **D1** | Prediction | Agent risk level vs buggy/clean label from CSV | Deterministic |
| **D2** | Localization | Agent cited files vs fix-commit diff files (Jaccard overlap) | Deterministic |
| **D3** | Diagnosis | Agent reasoning vs JIRA issue description | LLM-as-judge + human audit |
| **D4** | Severity | Agent risk level vs JIRA priority (normalized mapping) | LLM-as-judge + human audit |
| **D5** | Recommendations | Agent recommendations vs actual fix pattern | LLM-as-judge + human audit |

## Sampling Strategy

- Stratified sample from **routed commits** within budget tier
- Gray-zone focus (router probability 0.3–0.7) where investigation adds value
- Router-only baseline (no LLM) reported on same sample for comparison

## First Run Results (Mock Provider)

**Configuration:** 100 commits from gray-zone + high-zone of test_small split, mock LLM provider (no real API calls), no git clones available.

### Infrastructure Metrics

| Metric | Value |
|--------|-------|
| Router AUC-ROC (train) | 0.855 |
| Test_small total | 7,526 commits |
| Gray-zone (0.3-0.7) | 2,822 commits |
| High-zone (>0.7) | 1,982 commits |
| Sample evaluated | 100 commits |
| Buggy in sample | 26 (26%) |
| Clean in sample | 74 (74%) |
| Cost actual | $0 (mock) |

### Dimension Scores

| Dimension | Mock Agent | Router-only Baseline | Random Baseline |
|-----------|-----------|---------------------|-----------------|
| D1 Prediction | 0.74 | 0.26 | ~0.50 |
| D2 Localization | 0.00 | N/A | N/A |
| D3 Diagnosis | N/A (no JIRA) | N/A | N/A |
| D4 Severity | N/A (no JIRA) | N/A | N/A |
| D5 Recommendations | N/A (no JIRA) | N/A | N/A |

### Interpretation

- **D1 Mock = 0.74**: The mock provider always outputs MEDIUM risk (mapped to "not risky"). Since 74% of the sample is clean, it achieves 74% accuracy by always predicting safe. This is a naive baseline, not a real agent result.
- **D1 Router-only = 0.26**: Router considers all INVESTIGATE+HIGH commits as risky. Since only 26% are buggy, it has 26% precision on this sample (it catches all bugs but has high false positive rate).
- **D2 = 0.00**: No localization possible without git clones for actual diff access.
- **D3–D5 = N/A**: Require JIRA client connection (eval-time only).

## Provable Claims (from this run)

1. **The harness pipeline works end-to-end**: route → investigate → evaluate → report — fully functional without external dependencies (mock mode).
2. **Ground truth chain is 100% complete**: all 22,421 train buggy commits and 1,448 test_small buggy commits have fix linkage + issue linkage across 14 projects.
3. **XGBoost router achieves AUC-ROC 0.855 on train split**: meaningful signal from 12 numeric features alone, sufficient for routing decisions.
4. **Budget governance enforces limits**: 3-turn hard cap, per-commit token budget, per-run dollar cap — all enforced by orchestrator.

## Non-Provable / Inconclusive Claims (require real runs)

1. **Agent investigative quality vs router**: requires real LLM + git clones to produce meaningful D1 improvement over router baseline.
2. **Localization accuracy (D2)**: requires git clones to access fix-commit diffs for Jaccard comparison.
3. **Diagnosis/severity/recommendations (D3-D5)**: require both real LLM reasoning and JIRA API access.
4. **Cost-effectiveness of routing**: need actual LLM costs to compare $50 budget tier with full-population investigation.
5. **Multi-turn follow-up value**: mock provider never requests follow-up; real LLM may use 2-3 turns on uncertain commits.

## Requirements for Real Evaluation

| Requirement | Status | Action |
|-------------|--------|--------|
| Git clones (Camel + Hadoop) | Not cloned | `./scripts/clone_apache_repos.sh` (~2-5GB) |
| OpenAI API key | Not configured | Set `OPENAI_API_KEY` env var |
| JIRA access | Available (public) | Automatic via JiraClient |
| Budget ($50 tier) | Governance ready | ~300 real LLM investigations |

## Human Audit

20-commit sample reviewed by human for D3–D5 judge drift detection. Protocol TBD after first real LLM evaluation run.

## Related Documents

- [Experiment context](experiment-context.md) — dimension rationale and oracle isolation
- [Datasets](datasets.md) — ground truth chain feeding eval
- [Architecture](../ARCHITECTURE.md) — eval harness component design
