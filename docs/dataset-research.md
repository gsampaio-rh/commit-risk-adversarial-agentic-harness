# Dataset Research: Predictive Validation

> Can we find a public dataset where we can predict "will this change cause an incident?" and then validate the prediction against what actually happened?

**Date:** 2026-06-09
**Verdict:** No ideal public dataset exists. BPI 2014 is the best available — partial, small sample, but real.

---

## Deal-breaker Criteria

Immediate discard if a dataset does NOT have BOTH:

1. **Change/release records** with structured fields (type, risk, services, schedule)
2. **Incident records with direct linkage** to the causing change (not just same service/time window)

Ranking factors (nice-to-have):
- 100+ changes with linked incidents
- Temporal ordering (incident date vs change date) for proper split
- Prose artifacts (runbooks, rollback plans) to exercise the full pipeline
- Public access, free license

---

## Sources Searched

| Source | Searched | Results inspected | Candidates found |
|--------|----------|-------------------|------------------|
| HuggingFace datasets (tags: itsm, change-management, servicenow) | Yes | ~20 | 0 pass deal-breaker |
| Kaggle (ITSM, change management, incident prediction) | Yes | ~10 | 0 pass deal-breaker |
| UCI ML Repository | Yes | 1 relevant (incident event log) | 0 pass deal-breaker |
| 4TU.ResearchData / Zenodo | Yes | BPI 2014 (already have) + 1 SLA dataset | 1 partial (BPI 2014) |
| GitHub (data repos, benchmarks) | Yes | ~10 repos inspected | 0 pass deal-breaker |
| ArXiv papers 2023-2026 | Yes | 3 relevant papers | 0 have public data |
| Known candidates from aspirational archive | Yes (5 checked) | 5 | 0 pass deal-breaker |

---

## Candidate Assessments

### PASS (partial): BPI Challenge 2014

| Field | Value |
|-------|-------|
| URL | https://data.4tu.nl/collections/BPI_Challenge_2014/5065469 |
| Access | Free, CC0 license |
| Source | Rabobank Group ICT, HP Service Manager |
| Size | 18K changes, 46K incidents |
| Change→Incident linkage | `Related Change` field on incidents — **560 incidents linked to 231 changes** |
| High-severity linked | **29 incidents (P1/P2) across 25 changes** |
| Temporal ordering | Yes — incident `Open Time` vs change `Planned Start` |
| Prose artifacts | None (no runbooks, rollback plans, descriptions) |

**Predictive validation viability:**
- **What works:** 231 changes have known incident outcomes. 25 are high-severity (ground truth "problematic"). The remaining ~17,769 changes without linked incidents serve as "safe" examples.
- **What's weak:** 25 P1/P2 changes is a very small sample for precision/recall. The `Related Change` field is only 1.2% populated — most linkages may simply not have been recorded (false negatives in labels). Features available to the pipeline are limited (no descriptions, no runbooks).
- **Temporal split:** Viable. Changes have `Planned Start`, incidents have `Open Time`. Can separate "past incidents as context" from "future incidents as outcome."
- **Current code issue:** The adapter stores `Related Change` (a Change ID) in `change_category`, then joins incidents to bundles by CI name (over-broad). The direct `Related Change == change_id` linkage is not used for outcome labeling.

**Verdict:** Usable for a minimal predictive validation experiment, but results will be statistically fragile (n=25 for positive class). Worth doing as a proof-of-concept, not a definitive answer.

---

### FAIL: UCI Incident Management Event Log

| Field | Value |
|-------|-------|
| URL | https://archive.ics.uci.edu/dataset/498 |
| Access | Free, CC BY 4.0 |
| Source | ServiceNow platform, anonymous IT company |
| Size | 24,918 incidents, 141,712 events |
| Change→Incident linkage | `rfc` field: **99.3% empty** (176 populated). `caused_by` field: **99.98% empty** (3 populated). |

**Why it fails:** No change records in the dataset — only incidents. The `rfc` and `caused_by` fields point to change request IDs but the change requests themselves are absent. Even if they existed, the fill rates make them useless (176 and 3 populated records respectively).

---

### FAIL: ServiceNow-AI/EnterpriseOps-Gym

| Field | Value |
|-------|-------|
| URL | https://huggingface.co/datasets/ServiceNow-AI/EnterpriseOps-Gym |
| Access | Free, Apache 2.0 |
| Type | Agentic benchmark — 1,150 tasks, 512 tools, 8 domains |

**Why it fails:** This is a benchmark for evaluating LLM agents on multi-step ITSM tasks (e.g., "assign this incident", "create a change request"). It tests agent planning ability, not change risk prediction. No change→incident outcome data exists in the dataset. Tasks are evaluated by SQL state checks, not by prediction accuracy.

---

### FAIL: ServiceNow/ServiceNow-itsm-safety-bench

| Field | Value |
|-------|-------|
| URL | https://github.com/ServiceNow/ServiceNow-itsm-safety-bench |
| Access | Free (GitHub) |
| Size | 10 change requests, 50 incidents, 20 CMDB items |

**Why it fails:** This is a safety benchmark — it tests whether AI agents can resist social engineering to manipulate ITSM records (SLA backdating, priority downgrades, approval bypass). The 10 change requests are not linked to incidents as outcomes. They're scenario triggers for testing agent safety behavior.

---

### FAIL: VuduVations/itsm-change-management-benchmark

| Field | Value |
|-------|-------|
| URL | https://huggingface.co/datasets/VuduVations/itsm-change-management-benchmark |
| Access | Free (HuggingFace) |
| Size | 15 incidents, 68 CMDB items, 3 scenarios |

**Why it fails:** Designed for evaluating an ITIL reflexion agent that generates RFCs (Requests for Change) and scores them on 6 dimensions. The 15 incidents are context for RFC generation, not outcomes of changes. No change→incident outcome linkage. Too small for any statistical analysis even if it did.

---

### FAIL: ArXiv 2604.13462 — ING Bank (Kapel et al., 2026)

| Field | Value |
|-------|-------|
| URL | https://arxiv.org/html/2604.13462v1 |
| Data access | **Private — not available** |
| Size | 175K closed change tickets, ~4K incident-inducing (2.4%) |
| Linkage | `Caused by Change` field on incidents + Solution field text matching |
| Model | LightGBM, HGBC, XGBoost — LightGBM best (AUC ~0.85) |

**Why it fails the filter:** Data is private (regulated bank). Cannot download.

**Why it matters as reference:** This paper validates our exact problem formulation. ING built a predictive model for "will this change cause a P1/P2 incident?" using features like team metrics, change attributes, and CMDB data. Their rule-based system is similar to our R1-R4 approach. They found ML models outperform rule-based (AUC 0.85 vs lower). Key insight: only 2.4% of changes are incident-inducing — massive class imbalance.

Related paper: Kapel et al. (2024) "On the Difficulty of Identifying Incident-Inducing Changes" (ICSE-SEIP) — same ING data, emphasizes that linking changes to incidents is hard even with good data. False positive rates are high.

---

### FAIL: Other HuggingFace/Kaggle datasets inspected

| Dataset | Reason for discard |
|---------|-------------------|
| 6StringNinja/synthetic-servicenow-incidents | Incidents only, synthetic, no changes |
| ameau01/synthetic-it-support-tickets | Incidents only (745), synthetic, no changes |
| Snaseem2026/devops-incident-response | DevOps incidents, synthetic, no change records |
| ServiceNow/insight_bench | 500 simulated incidents for analytics, no changes |
| Tobi-Bueck/customer-support-tickets | Support tickets (not ITSM changes), no outcome linkage |
| mindweave/help-desk-tickets | Synthetic tickets, no change management |
| Kaggle: Incident Response Log | Incident logs, no change records |
| Zenodo 15142797 | SLA compliance data from logistics, no changes |

---

## Why the Landscape Is Barren

Three structural reasons why public change→incident datasets barely exist:

1. **ITSM data is proprietary.** Companies don't publish their change management data. The BPI 2014 dataset exists because it was released for an academic challenge — this is rare.

2. **Causal linkage is manually recorded.** The `Caused by Change` or `Related Change` field requires a human to fill it during incident resolution. Coverage is typically <5% even in well-run organizations (BPI: 1.2%, ING: 2.4%). Most incident-change links are never recorded.

3. **Synthetic datasets focus on agent evaluation.** The 2024-2026 wave of ITSM datasets (EnterpriseOps-Gym, safety-bench, VuduVations) are designed for testing LLM agents on ITSM tasks — not for predictive analytics. They contain ITSM record snapshots, not outcome data.

---

## Ranked Shortlist

| Rank | Dataset | Viability | Sample size | Prose artifacts |
|------|---------|-----------|-------------|-----------------|
| 1 | **BPI 2014** (already have) | Partial — 231 changes with outcomes, 25 high-severity | Small (n=25 positive) | None |
| — | ING Bank (reference only) | Perfect but private | Large (175K changes, 4K incident-inducing) | Unknown |
| — | Everything else | Does not pass deal-breaker | — | — |

---

## Recommendation

1. **BPI 2014 is the only viable option.** Run a proof-of-concept predictive evaluation with it, accepting the small sample limitation. Fix the adapter to use `Related Change` as direct outcome linkage (not just CI fan-out). Implement temporal split. Measure precision/recall on the 25 high-severity changes.

2. **Document the limitation honestly.** Any metrics from 25 positive examples are directional, not definitive. The 1.2% linkage rate means most "safe" labels may actually be unlabeled positives (incidents happened but weren't recorded).

3. **The ING paper (ArXiv 2604.13462) is the reference architecture.** Their approach validates ours: same problem, same features (change attributes + team metrics + CMDB), same finding that rule-based underperforms ML. Their data is private, but their methodology can guide our evaluation design.

4. **For production-quality validation, enterprise partner data is required.** No public dataset will ever have the combination of change records + incident outcomes + prose artifacts + sufficient volume. This is a P2 milestone that needs a partnership.

---

**Related:** [Datasets](datasets.md) | [Evaluation](evaluation.md) | [Architecture](../ARCHITECTURE.md)
