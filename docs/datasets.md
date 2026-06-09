# Datasets

> **Abstract question:** Given structured attributes of a planned intervention *before* execution, can we predict whether a failure will follow?
>
> This is not an "ITSM dataset search." Any domain that instantiates this question counts: ITSM changes, bug-inducing commits, deployments, releases, telecom config updates.

## Data Strategy

**Real-first + multi-domain:** BPI 2014 real ITIL change records validate pipeline architecture. Adjacent-domain datasets (JIT defect prediction, telecom, CI/CD) provide statistical power for predictive validation. Synthetic fixtures validate full-bundle and prose-artifact code paths. Enterprise data with prose artifacts is required before commercial claims.

---

## Active Data Sources

### BPI Challenge 2014 (ITSM — architecture validation)

[BPI Challenge 2014](https://data.4tu.nl/datasets/e9c00fe9-c87a-450e-8bd6-d5e06a6b309a) — real ITIL change records from Rabobank (HP Service Manager). Free access via 4TU.ResearchData.

| Metric | Value |
|--------|-------|
| Unique changes | 18,000+ |
| Incidents | 46,000+ |
| CAB-approval changes | 373 (374 in CSV; 1 dropped for empty Planned End) |
| CAB windows (ISO week) | 50 (51 raw; 1 empty after filter) |
| CRs per window | 1-21 |
| Recurring (CI, ChangeType) tuples | 56 (natural GT for Historical Pattern) |
| Schedule overlaps (raw BPI pairs) | 25,000+ (all changes; pipeline detected 27 among 373 CAB CRs) |
| Changes with linked incidents | 231 via `Related Change` (25 with P1/P2) |

**What it covers:** Ingest + Normalize (structured ITSM fields), Completeness (100% flagged incomplete — expected), Schedule overlap (real conflicts), Historical Pattern (56 GT tuples).

**What it lacks:** No runbooks, rollback plans, or communication plans (stages 4/5 skip). 97% missing Scheduled Downtime (stage 6 degrades). No CMDB snapshot (stage 7 skips). Opaque `change_category` IDs (L2 embedding adds no value).

**Adapter:** `src/cr_analyzer/adapters/bpi2014.py` — parses `Detail_Change.csv` (semicolon-delimited) and `Detail_Incident.csv`. Multi-CI rows grouped by Change ID. Download: `scripts/download_bpi2014.sh`. Data: `data/bpi2014/` (gitignored).

### Synthetic Fixtures (regression tests)

`fixtures/cab-window-01/` — 3 CR bundles for smoke testing:

| CR | Purpose | Bundle |
|----|---------|--------|
| `cr-001` | Full-bundle smoke test — normal change, medium risk, all 9 artifacts | Complete |
| `cr-002` | Full-bundle, high-risk — payment API schema migration | Complete |
| `cr-003` | Schedule overlap test — overlapping window with cr-001, no comms plan | 8 of 9 artifacts |

Used in `tests/conftest.py` for pytest integration.

---

## Predictive Validation Landscape

### Label quality tiers

| Tier | Definition | Example |
|------|-----------|---------|
| **A (gold)** | Explicit causal field on outcome record | BPI 2014 `Related Change`, CI build `conclusion` |
| **B (silver)** | Temporal + scope proximity join | Incident on same service within N hours post-change |
| **C (bronze)** | Proxy labels | SZZ bug-inducing commits, hotfix within 7d |
| **D (constructed)** | Multi-source joins with documented assumptions | GitHub releases + post-release bug issues |

### Three validation modes

| Mode | What it proves | Dataset requirement | Status |
|------|---------------|---------------------|--------|
| **Architecture** | Pipeline processes real data correctly | Any structured ITSM-shaped input | **Done** (BPI 2014) |
| **Predictive** | Risk scores predict actual failures | ≥200 positive labels with temporal split | **Not done** — ApacheJIT recommended |
| **Full pipeline** | All 9 stages including prose artifacts | Change records with runbooks, rollback plans | **Blocked** — no public data exists |

### Ranked shortlist

#### Tier 1 — Recommended for predictive validation

| Rank | Dataset | Domain | Size (total / positives) | Label method | Tier | Adapter effort | Semantic fit |
|------|---------|--------|--------------------------|--------------|------|----------------|-------------|
| **1** | [ApacheJIT](https://zenodo.org/records/5907847) | Software eng | 106K commits / **28K bug-inducing** | SZZ + JIRA + GumTree filters | B | 2-4 days | Medium |
| **2** | [Mozilla Regressors](https://github.com/mozilla/regressors-regressions-dataset) | Software eng | 24K commits / **12K bug-introducing sets** | Developer Bugzilla `regressor` field | A | 3-5 days | Medium |
| **3** | Constructed: GitHub Release→Bug | OSS release eng | Est. 5K-30K / **500-3K positives** | Temporal join (filtered) | D | 4-6 days | Medium |
| **4** | [RAN Updates](https://github.com/nds-group/ran-updates) | Telecom | 1,931 / **1,931 adverse-impact** | Measured traffic degradation | B | 2-3 days | **High** |
| **5** | [IBI Escalation](https://knowledgepit.ml/predicting-escalations-in-customer-support/) | Enterprise support | ~25K / **~4K escalated** | Explicit `escalated` field | A | 3-4 days | **High** |

#### Tier 2 — Methodology or scale

| Dataset | Domain | Size / positives | Tier | Notes |
|---------|--------|------------------|------|-------|
| [GHALogs](https://zenodo.org/records/10154920) | CI/CD | 513K / ~128K | A | CI gate, not production incident |
| [TravisTorrent](https://github.com/monperrus/travistorrent-java-ci-build-dataset) | CI/CD | 2.6M / ~686K | A | Massive; portal dead, mirrors available |
| [ClinicalRisk](https://zenodo.org/records/7982426) | Clinical trials | 12.7K / 5.7K | A | Methodology-only — structural analog |
| [JIT-Defects4J](https://github.com/jacknichao/JIT-Fine) | Software eng | 27K / 2.3K | A- | Highest label quality (manual) |
| [Linux Kernel HF](https://huggingface.co/datasets/pebblebed/kernel-vuln-dataset-full) | Software eng | 1.4M / 80K | B- | Scale stress-test |

#### Tier 3 — ITSM-specific (limited)

| Dataset | Size / positives | Tier | Notes |
|---------|------------------|------|-------|
| BPI 2014 | 18K changes / **25 P1/P2** | A (sparse) | Only public ITSM dataset; n=25 too small for robust stats |
| ING Bank (private) | 175K / 4K | A | Contact: Eileen.Kapel@ing.com |

---

## Domain Details

### JIT Defect Prediction

Largest public "change → failure" corpora. Commits labeled bug-inducing via SZZ algorithm or manual JIRA linkage.

| Name | URL | Commits / Positives | Label method | Tier |
|------|-----|---------------------|-------------|------|
| ApacheJIT | [Zenodo](https://zenodo.org/records/5907847) | 106K / 28K | SZZ + JIRA + GumTree | B |
| Mozilla Regressors | [GitHub](https://github.com/mozilla/regressors-regressions-dataset) | 24K / 12K sets | Developer `regressor` field | A |
| JIT-Defects4J | [GitHub](https://github.com/jacknichao/JIT-Fine) | 27K / 2.3K | Manual LLTC4J line labels | A- |
| Linux Kernel HF | [HuggingFace](https://huggingface.co/datasets/pebblebed/kernel-vuln-dataset-full) | 1.4M / 80K | `Fixes:` trailer mining | B- |
| Kamei 6-project | [Zenodo](https://zenodo.org/records/6342328) | 267K / 27K | SZZ + Bugzilla | B |
| McIntosh (Qt+OpenStack) | [GitHub](https://github.com/software-rebels/JITMovingTarget) | 37K / 3.6K | SZZ + Gerrit review | B |
| Rosa SZZ Oracle | [GitHub](https://github.com/grosa1/icse2021-szz-replication-package) | 1.9K pairs | Developer-documented BIC | A |

**Semantic gap: bug-inducing commit ≠ incident-causing change**

| Dimension | JIT defect label | ITSM CAB incident |
|-----------|------------------|---------------------|
| Failure mode | Latent defect discovered later | Immediate service impact (outage) |
| Observation | Days-months until fix | Incident logged with SLA clock |
| Change unit | VCS commit | Change request bundle |
| Severity | Binary clean/buggy | P1-P4 operational severity |
| Context | No runbooks, no CMDB, no schedule | Core CAB inputs |

**CRBundle adapter mapping (ApacheJIT):**
- `commit message` → `description`
- `project name` → `affected_services`
- `author` → `requestor`
- `author_date` → `scheduled_window.start`
- `LA, LD, NF, ND, NS, entropy` → `pr_scope_flags`
- Prior buggy commits on same files → `incident_history`
- Stages 4-7 skip (no prose artifacts, no CMDB, no schedule overlap)

### DevOps / CI/CD

No production deployment→incident dataset exists publicly. CI/CD build datasets measure **pre-production gate failure**, not service incidents.

| Name | Size / Failures | What it measures | Tier |
|------|-----------------|-----------------|------|
| GHALogs | 513K / ~128K | GitHub Actions workflow conclusion | A (CI gate) |
| TravisTorrent | 2.6M / ~686K | Travis CI build pass/fail | A (CI gate) |

### Process Mining / BPI

**BPI 2014 is the ONLY BPI year with change+incident data.** Systematic check of all 13 editions:

| Year | Domain | Has Changes? | Has Incidents? | ITSM relevance |
|------|--------|-------------|----------------|----------------|
| 2011 | Healthcare | No | No | 0 |
| 2012 | Banking (loans) | No | No | 0 |
| 2013 | IT outsourcing (Volvo) | **No** (excluded from export) | Yes | 2 |
| **2014** | **Banking IT (Rabobank)** | **Yes** | **Yes** | **3** |
| 2015 | Municipality (Dutch gov) | No | No | 0 |
| 2016 | Insurance claims | No | No | 0 |
| 2017 | Hospital billing | No | No | 0 |
| 2018 | Hospital billing (v2) | No | No | 0 |
| 2019 | Purchase-to-pay | No | No | 0 |
| 2020 | Unemployment benefits (Dutch UWV) | No | No | 0 |
| 2021-2023 | No editions published | — | — | — |

### Release Engineering + IaC

| Name | URL | Size / Positives | Tier |
|------|-----|------------------|------|
| F-Droid Crashing Releases | [Dropbox](https://www.dropbox.com/s/hr6amcyssuj194c/dataset.zip?dl=0) *(may rot)* | 2,638 / 344 | C |
| Go8 IaC defect commits | [Zenodo](https://zenodo.org/records/15276124) | 6.6K / 3.4K | C |
| Dalla Palma Ansible | [Zenodo](https://zenodo.org/records/4299908) | 85 repos / 4.9K | C |
| HotBugs.jar | [GitHub](https://github.com/carolhanna01/HotBugs-dot-jar) | 679 hotfixes | C |

### Industrial / Safety-Critical

**No viable hits.** Change control records in aviation, aerospace, manufacturing, and nuclear remain private/classified. Public databases (FAA SDR, NTSB, NASA ASRS, NRC LER) publish outcomes but not preceding change orders.

---

## Constructed Dataset Feasibility

| Path | Data available? | Join quality | Expected n | Tier | Effort | Go/No-go |
|------|-----------------|-------------|------------|------|--------|----------|
| **B: GitHub Releases → post-release bugs** | Yes (GHArchive, API) | Moderate | 5K-30K | D | 4-6 days | **Go** |
| F: CNCF release → bugs (subset of B) | Yes | Same, better metadata | 500-3K | D | 5-7 days | **Go (pilot)** |
| E: CI/CD build failure (ready-made) | Yes | Perfect | 100K-500K | C | 1-2 days | **Go (methodology)** |
| A: GitHub Deployments + Issues | Partial | Poor | <1K | C-B | 5-7 days | **No-go** |
| C: Postmortems + deploy timeline | Sparse | Poor | 20-80 | A/C | 8-12 days | **No-go (scale)** |

**Filters required for usable labels (Path B):** semver-tagged releases only, issue labels restricted to `bug`/`regression`, exclude zero-diff releases, minimum 7-day gap between consecutive releases, cap at ≤2 releases/month. Without filters, naive joins produce **60-85% positive rates at 7d** — useless.

**Sensitivity (filtered):** 24h → 3-8% positive. 7d → 8-18%. 14d → 12-25% (noisy). 30d → 95%+ (useless).

---

## Academic Landscape

20 papers catalogued on change→failure prediction. Key findings:

| # | Paper | Domain | Data | Contact |
|---|-------|--------|------|---------|
| 1 | Kapel et al. 2023 — ING Bank | ITSM | **Private** (175K changes, 4K incident-inducing) | Eileen.Kapel@ing.com |
| 2 | Güven et al. 2019 — IBM | ITSM | **Private** (300K+ changes) | sguven@us.ibm.com |
| 3 | Paul et al. 2023 — Walmart | ITSM | **Private** (27K CRQs) | subhadip.paul0@walmart.com |
| 4 | Abreu et al. 2022 — Meta DRS | Release eng | **Private** | ruiabre@meta.com |
| 5-9 | BPI 2014, ApacheJIT, Kamei, McIntosh, TravisTorrent | Various | **Public** | — |
| 10-20 | Google, SZZ, Avaya, Microsoft, Facebook, others | Various | **Private** or method-only | On-request |

**Key methodological insights:**
- **ML >> rules:** ING LightGBM wF₂ 0.93 vs 0.88 baseline rules (AUC 0.67 vs 0.55)
- **Team features matter:** ING team metrics improve precision 2→4%
- **Class imbalance:** ~2-3% positive rate; treat as risk ranking, not binary
- **86% of harmful changes closed "successful"** (IBM) — predicting "failed change" misses most harm
- **Temporal evaluation mandatory:** JIT models degrade in 3-12 months
- **Label sparsity is structural:** <5% of change→incident links recorded even in mature orgs

---

## Why Public ITSM Data Is Barren

1. **ITSM data is proprietary.** BPI 2014 exists because of an academic challenge — rare.
2. **Causal linkage is manually recorded.** `Related Change` fields: BPI 1.2%, ING 2.4%.
3. **2024-2026 ITSM datasets are agent benchmarks**, not predictive analytics with outcomes.
4. **Industrial domains have the same problem.** Data stays private/classified.
5. **Adjacent domains label differently.** ITSM-keyword searches miss JIT, telecom, clinical datasets.

---

## Recommendations

**Primary: ApacheJIT** — 28K labeled positives, 2-4 day adapter. Validates "pre-execution attributes predict post-execution failure" at statistical scale.

**Secondary: Constructed GitHub release→bug** — 3-4 day pilot on 5 CNCF repos. Gate on 5-20% positive rate.

**ITSM-specific: BPI 2014** — fix adapter for `Related Change` outcome linkage. Accept n=25 P1/P2 limitation.

**Full pipeline: Enterprise partner required.** No public data has prose artifacts.

```
Q: What are you validating?
├── "Pipeline architecture works on real ITIL data"
│   └── BPI 2014 (already done ✓)
├── "Risk prediction works (precision/recall at scale)"
│   ├── Adjacent domain OK?
│   │   ├── Yes → ApacheJIT (28K positives, 2-4 days)
│   │   └── Must be ITSM → BPI 2014 (n=25, fragile)
│   └── Willing to construct?
│       ├── Yes → GitHub release→bug pilot (3-4 days)
│       └── No → ApacheJIT only
├── "All 9 stages including prose artifacts"
│   └── Enterprise partner required (no public path)
└── "Prove concept before enterprise engagement"
    └── ApacheJIT + BPI 2014 + constructed pilot
```

---

## Research Methodology

This dataset landscape was assessed in June 2026 across 8 domains (~140 datasets/sources inspected):

| Domain | Platforms | Inspected | Candidates |
|--------|-----------|-----------|------------|
| ITSM / process mining | 4TU, BPI 2011-2023, XES, pm4py, Celonis, ProM | 23 | 0 new |
| JIT defect prediction | Zenodo, GitHub, Papers With Code, MSR, HuggingFace | 15 | 4 strong |
| DevOps / CI/CD | GitHub, Kaggle, HuggingFace, Zenodo, IEEE, GHArchive | 18 | 2 strong |
| Release engineering + IaC | GitHub, Zenodo, Kaggle, ArXiv, MSR | 16 | 2 moderate |
| Industrial / safety-critical | NASA, NTSB, FAA, NRC, UK HSE, Harvard Dataverse | 12 | 0 |
| Academic papers | Scholar, Semantic Scholar, ArXiv, ACM, IEEE, Springer | 20 papers | Mostly private |
| Constructed feasibility | GitHub API, GHArchive, VOID, postmortem DBs, OSV | 7 paths | 1 recommended |
| Unbiased platform sweep | HF, Kaggle, GitHub, Zenodo, UCI, OpenML, PwC, Google | 28 | 4 surprising |

---

**Related:** [Evaluation](evaluation.md) | [Architecture](../ARCHITECTURE.md) | [Pipeline Flow](pipeline-flow.md)
