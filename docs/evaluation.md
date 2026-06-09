# Evaluation

> Evaluated on BPI 2014 real data + synthetic fixtures. Metrics validate architecture, not production performance.

## BPI 2014 Full Evaluation

The evaluation harness (`src/cr_analyzer/eval/evaluate.py`) runs the complete pipeline on all BPI 2014 CAB windows.

| Metric | Result |
|--------|--------|
| Total CRs processed | 373 |
| Total windows | 50 |
| Task completion rate | 100% |
| Schema compliance | 100% |
| Wall clock time | 11.6s |

### Disposition Breakdown

| Recommendation | Count | % |
|----------------|-------|---|
| Approve | 0 | 0% |
| Conditional | 0 | 0% |
| Reject | 373 | 100% |

All BPI CRs are rejected because missing artifacts (runbook, rollback, communication plan) produce blocker findings in Completeness Check. This is correct behavior — incomplete bundles should not pass.

### Completeness

100% of BPI CRs flagged as incomplete. Expected — no BPI change has runbook, rollback, or communication plan artifacts.

### Schedule Overlaps

27 scheduling conflicts detected across 50 windows. These are real temporal overlaps on shared CI infrastructure from the Rabobank dataset.

### Historical Pattern

| Metric | L1 (exact match) | L2 (embedding) | Delta |
|--------|-------------------|-----------------|-------|
| Total findings | 9 | 0 | -9 |
| CRs with findings | 9 | 0 | -9 |

**Why L2 shows zero on BPI:** BPI `change_category` values are opaque Change IDs (e.g., "C00012345"), not semantic text. Embedding similarity cannot extract meaning from opaque identifiers. L2's value is validated separately on synthetic data with meaningful category text.

---

## L1 vs L2 Comparison

### Historical Pattern

| Scenario | L1 | L2 | Winner |
|----------|----|----|--------|
| Exact category match | Finds it | Finds it | Tie |
| Same semantics, different category name | Misses it | Finds it | L2 |
| Opaque IDs (BPI 2014) | Finds it (exact) | Misses it | L1 |

L2 is validated on synthetic fixtures where categories have semantic meaning (see `tests/test_historical_pattern_l2.py`). The dual-path mode (L1 + L2) is the recommended default — it preserves L1 exact match while adding L2 semantic coverage.

### Risk Synthesis

| Scenario | L1 (template) | L2 (LLM narrative) |
|----------|---------------|---------------------|
| Approve CRs | Template report | Template report (LLM skipped — cost savings) |
| Conditional/Reject CRs | Template report | LLM cross-dimension narrative |
| LLM API unavailable | Template report | Fallback to template (no crash) |

Selective routing ensures cost ceiling compliance: LLM only for ~40% of batch (conditional + reject). Budget: < $2 per 50-CR window.

---

## Test Suite

147 tests across 13 files. All pass.

| Test file | Tests | Covers |
|-----------|-------|--------|
| `test_models.py` | 27 | Pydantic models, fixture roundtrips, enum validation |
| `test_ingest.py` | 9 | Full/partial/empty bundle parsing |
| `test_normalize.py` | 13 | Field standardization, derived fields, CMDB tier logic |
| `test_completeness.py` | 11 | ITIL rules per change type, customer-facing comms |
| `test_schedule_sla.py` | 9 | Overlap detection, severity tiers, SLA mode |
| `test_skip.py` | 5 | Conditional skip logic, skip findings |
| `test_historical_pattern.py` | 9 | L1 exact match, threshold rules, empty history |
| `test_historical_pattern_l2.py` | 12 | L2 embedding, dual-path, graceful fallback |
| `test_risk_synthesis.py` | 13 | R1-R4 rules, CabSummary, Markdown rendering |
| `test_risk_synthesis_l2.py` | 15 | Selective routing, cost tracking, LLM fallback |
| `test_runner.py` | 9 | E2E single CR, batch pipeline, disk checkpoints |
| `test_bpi2014_adapter.py` | 10 | CSV parsing, field mapping, CAB window derivation |
| `test_evaluate.py` | 5 | Eval harness on 1-window subset |

---

## Target Metrics

| Metric | Target (P0) | Current |
|--------|-------------|---------|
| Task completion rate | 100% | 100% (373/373) |
| Schema compliance | >= 99% | 100% |
| Cost per 50-CR batch (L1) | < $2 | $0 |
| Wall-clock (total eval) | < 30 min per window | 11.6s total for all 373 CRs across 50 windows |
| Cross-run consistency (L1) | >= 95% | 100% (deterministic) |

For aspirational P0/P2 success gates and projected metric evolution across levels, see [.harness/archive/docs/ARCHITECTURE-aspirational.md](../.harness/archive/docs/ARCHITECTURE-aspirational.md#p0-success-gates-vs-p2-commercial-claims).

---

## Predictive Validation

### What we measure today vs what we need

| Dimension | Current (architecture validation) | Target (predictive validation) |
|-----------|----------------------------------|-------------------------------|
| Task completion | 100% — pipeline processes all CRs | Same |
| Schema compliance | 100% — outputs are valid | Same |
| **Prediction accuracy** | **Not measured** | Precision/recall: "of changes that caused incidents, how many did we flag?" |
| **False positive rate** | **Not measured** | "Of changes we flagged as risky, how many actually caused incidents?" |
| Consistency | 100% deterministic | Same |

### What's blocking

Predictive validation requires a dataset with both change features (input) and incident outcomes (labels). We searched HuggingFace, Kaggle, UCI, Zenodo, GitHub, and ArXiv (2020-2026). No public dataset passes the deal-breaker criteria: change records + direct incident linkage + sufficient sample size. See [dataset-research.md](dataset-research.md) for full findings.

**BPI 2014** is the only partial candidate: 231 changes with linked incidents (25 with P1/P2). This is enough for a proof-of-concept but not for statistically robust conclusions.

### What "good" looks like

A dataset where we can:
1. Run the pipeline on a change request, producing a risk score
2. Look at what actually happened after the change was deployed
3. Compare: did the pipeline correctly identify high-risk changes?
4. Compute precision, recall, F1 on a meaningful sample (100+ labeled changes)

### Next steps

1. Run a proof-of-concept predictive evaluation on BPI 2014 using `Related Change` as outcome labels (n=25 positive, ~17K negative). Accept small-sample limitation.
2. Fix adapter to use temporal split (only past incidents as input, post-change incidents as labels).
3. For production-quality validation, enterprise partner data is required.
