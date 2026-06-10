# Evaluation Framework

Five-dimension evaluation comparing agent investigation output to ApacheJIT ground truth. Results populated after feat-9 first eval run.

## Dimensions

| ID | Dimension | Criterion | Automation |
|----|-----------|-----------|------------|
| **D1** | Prediction | Agent risk level vs buggy/clean label from CSV | Deterministic |
| **D2** | Localization | Agent cited files vs fix-commit diff files (Jaccard overlap) | Deterministic |
| **D3** | Diagnosis | Agent reasoning vs JIRA issue description | LLM-as-judge + human audit |
| **D4** | Severity | Agent risk level vs JIRA priority (normalized mapping) | LLM-as-judge + human audit |
| **D5** | Recommendations | Agent recommendations vs actual fix pattern | LLM-as-judge + human audit |

## Sampling Strategy

- Stratified sample from **routed commits** within budget tier (default **$50** / ~300 LLM calls)
- Gray-zone focus (router probability 0.3–0.7) where investigation adds value
- Router-only baseline (no LLM) reported on same sample for comparison

## Results

**Status:** TBD — pending feat-9 first eval run.

| Dimension | Agent | Router-only | Random baseline |
|-----------|-------|-------------|-----------------|
| D1 Prediction | TBD | TBD | TBD |
| D2 Localization | TBD | TBD | TBD |
| D3 Diagnosis | TBD | N/A | TBD |
| D4 Severity | TBD | N/A | TBD |
| D5 Recommendations | TBD | N/A | TBD |

### Cost Actuals

TBD — pending feat-9.

### Claims (feat-9 deliverable)

- ≥3 provable claims: TBD
- ≥3 non-provable or inconclusive claims: TBD

## Human Audit

20-commit sample reviewed by human for D3–D5 judge drift detection. Protocol TBD in feat-7.

## Related Documents

- [Experiment context](experiment-context.md) — dimension rationale and oracle isolation
- [Datasets](datasets.md) — ground truth chain feeding eval
- [Architecture](../ARCHITECTURE.md) — eval harness component design
