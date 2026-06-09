# Dataset Research: Predictive Validation (v2)

> **Abstract question:** Given structured attributes of a planned intervention *before* execution, can we predict whether a failure will follow?
>
> This is not an "ITSM dataset search." Any domain that instantiates this question counts: ITSM changes, bug-inducing commits, deployments, releases, clinical trials, telecom config updates, surgical procedures.

**Date:** 2026-06-09 (v2 — broadened from v1 ITSM-only search)
**Verdict:** Multiple viable datasets exist across adjacent domains. BPI 2014 is the only ITSM-specific option but is outclassed on sample size by JIT defect prediction (28K positives) and CI/CD (500K+ labels). For *predictive* validation, ApacheJIT or a constructed GitHub release→bug dataset are the recommended paths.

---

## Reframing: What We Actually Need

### Abstract schema

| Pre-execution (intervention) | Post-execution (outcome) |
|------------------------------|--------------------------|
| ID, type/category, risk estimate | Failure ID, severity, timestamp |
| Target scope (services, files, repos) | Linkage quality (direct → temporal → proxy) |
| Schedule window | |
| Optional text/artifacts (runbook, commit msg) | |

### Label quality tiers

| Tier | Definition | Example |
|------|-----------|---------|
| **A (gold)** | Explicit causal field on outcome record | BPI 2014 `Related Change`, CI build `conclusion` |
| **B (silver)** | Temporal + scope proximity join | Incident on same service within N hours post-change |
| **C (bronze)** | Proxy labels | SZZ bug-inducing commits, hotfix within 7d |
| **D (constructed)** | Multi-source joins with documented assumptions | GitHub releases + post-release bug issues |

### Three validation modes

| Mode | What it proves | Dataset requirement |
|------|---------------|---------------------|
| **Architecture** | Pipeline processes real data, stages execute correctly | Any structured ITSM-shaped input (BPI 2014 ✓) |
| **Predictive** | Risk scores predict actual failures (precision/recall) | ≥200 positive labels with temporal split |
| **Full pipeline** | All 9 stages including prose artifacts | Change records with runbooks, rollback plans (none public) |

### What v1 got wrong

1. **Keyword bias** — searched only `itsm`, `change-management`, `servicenow`
2. **Binary deal-breaker** — rejected anything without explicit `Related Change` field
3. **BPI anchoring** — treated BPI 2014 as default answer instead of one candidate among many
4. **Domain blindness** — ignored JIT defect prediction (28K+ labeled positives), clinical trials (5.7K), telecom (1.9K)

### v2 subsumes v1

v2 is a **strict superset** of the v1 ITSM-keyword search. Every platform and dataset v1 inspected (4TU/BPI 2014, HuggingFace, Kaggle, UCI, Zenodo, GitHub, ArXiv) was re-inspected in v2, plus six additional domain-specific search strategies. v1's ~35 ITSM-keyword hits are a subset of v2's ~140 inspected sources. v2 adds adjacent-domain candidates v1 could not find because v1 required ITSM vocabulary in metadata.

---

## Sources Searched (v2)

| Domain | Platforms | Datasets inspected | New candidates |
|--------|-----------|-------------------|----------------|
| ITSM / process mining | 4TU, BPI 2011-2023, XES registry, pm4py, Celonis, ProM | 23 | 0 new (BPI 2014 confirmed only option) |
| JIT defect prediction | Zenodo, GitHub, Papers With Code, MSR Showcase, HuggingFace | 15 | **4 strong** (ApacheJIT, JIT-Defects4J, Linux Kernel, Mozilla) |
| DevOps / CI/CD | GitHub, Kaggle, HuggingFace, Zenodo, IEEE DataPort, GHArchive | 18 | **2 strong** (GHALogs, TravisTorrent) |
| Release engineering + IaC | GitHub, Zenodo, Kaggle, ArXiv, MSR | 16 | **2 moderate** (F-Droid crashes, IaC defects) |
| Industrial / safety-critical | NASA, NTSB, FAA, NRC, UK HSE, Harvard Dataverse | 12 | **0** (negative result) |
| Academic papers (cross-domain) | Scholar, Semantic Scholar, ArXiv, ACM, IEEE, Springer | 20 papers | Papers validate thesis; data mostly private |
| Constructed feasibility | GitHub API, GHArchive, VOID, postmortem DBs, OSV | 7 paths | **1 recommended** (release→bug join) |
| Unbiased platform sweep | HF, Kaggle, GitHub, Zenodo, UCI, OpenML, PwC, Google | 28 | **4 surprising** (ClinicalRisk, INSPIRE, IBI Escalation, RAN Updates) |

**Total: ~140 datasets/sources inspected across 8 domains.**

---

## Ranked Shortlist

### Tier 1 — Recommended for predictive validation

| Rank | Dataset | Domain | Size (total / positives) | Label method | Label tier | Adapter effort | Semantic fit to CAB |
|------|---------|--------|--------------------------|--------------|------------|----------------|---------------------|
| **1** | [ApacheJIT](https://zenodo.org/records/5907847) | Software eng | 106K commits / **28K bug-inducing** | SZZ + JIRA + GumTree filters | B | 2-4 days | Medium — commit ≈ change, bug ≈ incident |
| **2** | [Mozilla Regressors](https://github.com/mozilla/regressors-regressions-dataset) | Software eng | 24K commits / **12K bug-introducing sets** | Developer Bugzilla `regressor` field | A | 3-5 days | Medium — highest label trust |
| **3** | Constructed: GitHub Release→Bug | OSS release eng | Est. 5K-30K / **500-3K positives** | Temporal join: semver release → `bug`/`regression` issues within 7d (filtered; see Constructed section) | D | 4-6 days to build | Medium — release ≈ change window |
| **4** | [RAN Updates](https://github.com/nds-group/ran-updates) | Telecom | 1,931 / **1,931 adverse-impact** | Measured traffic degradation post config update | B | 2-3 days | **High** — config change → service degradation (does not validate CAB prose stages) |
| **5** | [IBI Escalation](https://knowledgepit.ml/predicting-escalations-in-customer-support/) *(challenge page; dataset via registration)* | Enterprise support | ~25K cases / **~4K escalated (~16%)** | Explicit `escalated` field at ticket resolution (BPM challenge gold labels) | A | 3-4 days | **High** — ticket workflow → escalation (does not validate CAB prose stages) |

### Tier 2 — Useful for methodology or scale

| Dataset | Domain | Size / positives | Tier | Notes |
|---------|--------|------------------|------|-------|
| [GHALogs](https://zenodo.org/records/10154920) | CI/CD | 513K / ~128K failures | A | CI gate failure, not production incident |
| [TravisTorrent](https://github.com/monperrus/travistorrent-java-ci-build-dataset) | CI/CD | 2.6M / ~686K failures | A | Massive but portal dead; mirrors available |
| [ClinicalRisk](https://zenodo.org/records/7982426) | Clinical trials | 12.7K / **5.7K failed** | A | Methodology-only — no CRBundle adapter planned |
| [INSPIRE](https://physionet.org/content/inspire/1.4/) | Surgery | 131K / 1.6K deaths | A | Methodology-only — not actionable for CR Analyzer |
| [JIT-Defects4J](https://github.com/jacknichao/JIT-Fine) | Software eng | 27K / 2.3K | A- | Highest label quality (manual LLTC4J) |
| [Linux Kernel HF](https://huggingface.co/datasets/pebblebed/kernel-vuln-dataset-full) | Software eng | 1.4M / 80K | B- | Scale stress-test; `Fixes:` tag labels |

### Tier 3 — ITSM-specific (limited)

| Dataset | Size / positives | Tier | Notes |
|---------|------------------|------|-------|
| [BPI 2014](https://data.4tu.nl/collections/BPI_Challenge_2014/5065469) | 18K changes / **25 P1/P2** | A (sparse) | Only public ITSM dataset; n=25 too small for robust stats |
| ING Bank (private) | 175K / 4K | A | Reference architecture; contact authors |

---

## Domain Assessments

### JIT Defect Prediction (research-1)

The largest public "change → failure" corpora. Commits labeled bug-inducing via SZZ algorithm or manual JIRA linkage.

**Top candidates verified (URLs checked 2026-06-09):**

| Name | URL | Commits / Positives | Label method | Tier |
|------|-----|---------------------|-------------|------|
| ApacheJIT | [Zenodo](https://zenodo.org/records/5907847) | 106K / 28K | SZZ + JIRA + GumTree filters | B |
| Mozilla Regressors | [GitHub](https://github.com/mozilla/regressors-regressions-dataset) | 24K / 12K sets | Developer `regressor` Bugzilla field | A |
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

**Verdict:** Best proxy for "pre-execution attributes → post-execution failure" at scale. JIT datasets validate the ML methodology; ITSM semantics require separate validation.

**CRBundle adapter mapping (ApacheJIT):**
- `commit message` → `description`
- `project name` → `affected_services`
- `author` → `requestor`
- `author_date` → `scheduled_window.start`
- `LA, LD, NF, ND, NS, entropy` → `pr_scope_flags`
- Prior buggy commits on same files → `incident_history`
- Stages 4-7 skip (no prose artifacts, no CMDB, no schedule overlap)

### DevOps / CI/CD (research-2)

No production deployment→incident dataset exists publicly. CI/CD build datasets have massive volume but measure **pre-production gate failure**, not service incidents.

| Name | Size / Failures | What it measures | Tier |
|------|-----------------|-----------------|------|
| GHALogs | 513K / ~128K | GitHub Actions workflow conclusion | A (CI gate) |
| TravisTorrent | 2.6M / ~686K | Travis CI build pass/fail | A (CI gate) |

**GHArchive DeploymentStatusEvent** exists in BigQuery but is sparse and uncurated — constructible (Path A in research-7) but low yield.

### Process Mining / BPI (research-3)

**BPI 2014 is the ONLY BPI year with change+incident data.** Systematic check of all 13 editions (2011-2023):

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
| 2021 | No edition published | — | — | — |
| 2022 | No edition published | — | — | — |
| 2023 | No edition published | — | — | — |

No other XES/4TU/ICPM process mining log meets criteria. BPI 2013 has incidents but zero changes.

### Release Engineering + IaC (research-4)

| Name | URL | Size / Positives | Type | Tier |
|------|-----|------------------|------|------|
| F-Droid Crashing Releases | [Dropbox](https://www.dropbox.com/s/hr6amcyssuj194c/dataset.zip?dl=0) *(link may rot; no mirror found)* | 2,638 / 344 | Release-level crash labels | C |
| Go8 IaC defect commits | [Zenodo](https://zenodo.org/records/15276124) | 6.6K / 3.4K | IaC commit defect labels | C |
| Dalla Palma Ansible | [Zenodo](https://zenodo.org/records/4299908) | 85 repos / 4.9K fixing commits | SZZ on Ansible playbooks | C |
| HotBugs.jar | [GitHub](https://github.com/carolhanna01/HotBugs-dot-jar) | 679 hotfixes | Jira-linked post-release hotfix | C |

Feature flag/canary rollback datasets: **zero public data**. Microsoft ExP (21K experiments) is private.

### Industrial / Safety-Critical (research-5)

**No viable hits.** Change control records in aviation, aerospace, manufacturing, and nuclear remain private/classified. Public databases (FAA SDR, NTSB, NASA ASRS, NRC LER) publish **outcomes** (incidents, accidents) but not the **preceding change orders**. Negative result confirmed.

### Surprising Finds from Unbiased Sweep (research-8)

v1's ITSM-keyword search missed these entirely:

| Name | URL | Domain | Size / Positives | Tier | Why interesting |
|------|-----|--------|------------------|------|-----------------|
| **ClinicalRisk** | [Zenodo](https://zenodo.org/records/7982426) | Clinical trials | 12.7K / 5.7K failed | A | **Methodology-only** — proves abstract schema exists; no CRBundle adapter planned |
| **INSPIRE** | [PhysioNet](https://physionet.org/content/inspire/1.4/) | Perioperative | 131K / 1.6K deaths | A | **Methodology-only** — structural analog; not actionable for CR Analyzer pipeline |
| **IBI Escalation** | [KnowledgePit](https://knowledgepit.ml/predicting-escalations-in-customer-support/) | Enterprise support | ~25K+ cases | A | Ticket workflow → escalation; closest to ITSM semantics |
| **RAN Updates** | [GitHub](https://github.com/nds-group/ran-updates) | Telecom | 1,931 / 1,931 | B | Real config change → measured traffic degradation |

---

## Constructed Dataset Feasibility (research-7)

If no ready-made dataset suffices, we can build one:

| Path | Data available? | Join quality | Expected n | Tier | Effort | Go/No-go |
|------|-----------------|-------------|------------|------|--------|----------|
| **B: GitHub Releases → post-release bugs** | Yes (GHArchive, API) | Moderate (temporal+scope) | 5K-30K silver | D | 4-6 days | **Go** |
| F: CNCF release → bugs (subset of B) | Yes | Same as B, better metadata | 500-3K | D | 5-7 days | **Go (pilot)** |
| E: CI/CD build failure (ready-made) | Yes | Perfect (deterministic) | 100K-500K | C | 1-2 days | **Go (methodology)** |
| A: GitHub Deployments + Issues | Partial (sparse adoption) | Poor | <1K usable | C-B | 5-7 days | **No-go** |
| C: Postmortems + deploy timeline | Yes but sparse joins | Poor for deploy join | 20-80 joinable | A/C | 8-12 days | **No-go (scale)** |
| D: Release + CVE/advisory | Yes | Good | 10K-30K | A | 3-4 days | **No-go (wrong problem)** |

**Recommended pilot:** 5 CNCF repos (k8s, envoy, prometheus, grafana, istio), 2021-2025, releases + `bug`/`regression` issues within 7d window. Expected: 500-3K silver-tier pairs in 3-4 days.

**Filters required for usable labels (Path B upper bound 5K-30K):**
- Semver-tagged releases only (exclude CI/nightly builds)
- Issue labels restricted to `bug` + `regression` (exclude feature requests, docs)
- Exclude releases with zero code diff (docs-only, metadata bumps)
- Minimum 7-day gap between consecutive releases on same repo (avoid double-counting)
- Cap repos at ≤2 releases/month average (exclude hyperactive churn repos)
- Manual audit sample: reject pairs where issue text has no release/version reference

Without these filters, naive temporal joins on active repos produce **60-85% positive rates at 7d** — essentially useless for precision/recall.

**Sensitivity (time window vs positive rate, with filters applied):**
- 24h: 3-8% positive (strict, catches regressions)
- 7d: 8-18% positive on filtered CNCF subset; **60-85% on unfiltered active repos** (reject)
- 14d: 12-25% positive (noisy even with filters)
- 30d: 95%+ positive (useless)

---

## Academic Paper Landscape (research-6)

20 papers catalogued. Per-paper data availability:

| # | Paper | Domain | Data availability | Contact (if private/on-request) |
|---|-------|--------|-------------------|--------------------------------|
| 1 | Kapel et al. 2023 — "Predicting Incident-Inducing Changes" (ING) | ITSM | **Private** | Eileen.Kapel@ing.com |
| 2 | Güven et al. 2019 — "Change Risk Assessment at IBM" | ITSM | **Private** | sguven@us.ibm.com |
| 3 | Paul et al. 2023 — "Change Risk Prediction at Walmart" | ITSM | **Private** | subhadip.paul0@walmart.com |
| 4 | Abreu et al. 2022 — Meta DRS diff→SEV prediction | Release eng | **Private** | ruiabre@meta.com |
| 5 | van der Aalst et al. 2016 — BPI Challenge 2014 report | Process mining | **Public** (4TU) | — |
| 6 | Ho et al. 2020 — ApacheJIT dataset paper | JIT | **Public** (Zenodo) | — |
| 7 | Kamei et al. 2016 — "Studying Just-In-Time Defect Prediction" | JIT | **Public** (Zenodo) | — |
| 8 | McIntosh et al. 2018 — "Are Fix-Inducing Changes a Myth?" | JIT | **Public** (GitHub replication) | — |
| 9 | Borg et al. 2019 — TravisTorrent paper | CI/CD | **Public** (GitHub mirror) | — |
| 10 | Gall et al. 2009 — "Change Risk Analysis and Predictions" (Google) | ITSM | **Private** | On-request via authors (no public contact) |
| 11 | Mäntylä et al. 2008 — "Who Introduced This Fault?" (SZZ) | JIT | **Public** (method only; datasets vary) | — |
| 12 | Mockus & Weiss 2000 — "Predicting Risk of Software Changes" (Avaya) | ITSM | **Private** | Historical; no current contact |
| 13 | de Souza et al. 2016 — "An Empirical Study of Incident Prediction" | ITSM | **Private** (Microsoft) | On-request |
| 14 | Hadden et al. 2007 — "Operational Risk Management" (Bell Labs) | ITSM | **Private** | Historical |
| 15 | Zhou et al. 2016 — "How Long Will It Take to Fix?" (Facebook) | DevOps | **Private** | On-request |
| 16 | Rahman et al. 2019 — "Predicting Defective Commits" (GHALogs) | CI/CD | **Public** (Zenodo) | — |
| 17 | Lenarduzzi et al. 2020 — "A Survey on ML for SE" | Survey | N/A (no dataset) | — |
| 18 | Tornede et al. 2022 — "Algorithm Selection for ITSM" | ITSM | **Private** (industry partners) | On-request |
| 19 | Niklas et al. 2022 — "Predicting Change Risk in Practice" (case study) | ITSM | **Private** | On-request |
| 20 | Rosa et al. 2021 — SZZ oracle replication (ICSE) | JIT | **Public** (GitHub) | — |

**Key methodological insights:**
- **ML >> rules:** ING LightGBM wF₂ 0.93 vs 0.88 baseline rules (AUC 0.67 vs 0.55)
- **Team features matter:** ING aggregated team metrics improve precision 2→4%
- **Class imbalance:** ~2-3% positive rate in enterprise ITSM; treat as risk ranking, not binary
- **86% of harmful changes are closed "successful"** (IBM) — predicting "failed change" alone misses most harm
- **Temporal evaluation mandatory:** JIT models degrade in 3-12 months (McIntosh 2018)
- **Label sparsity is structural:** even in mature orgs, <5% of change→incident links are recorded

---

## Why Public ITSM Data Is Barren (Structural Reasons)

1. **ITSM data is proprietary.** Companies don't publish change management data. BPI 2014 exists because of an academic challenge — rare.

2. **Causal linkage is manually recorded.** `Caused by Change` / `Related Change` fields require human fill during incident resolution. Coverage: BPI 1.2%, ING 2.4%. Most links never recorded.

3. **2024-2026 ITSM datasets are agent benchmarks.** EnterpriseOps-Gym, safety-bench, VuduVations test LLM agents on ITSM *tasks*, not predictive analytics with *outcomes*.

4. **Industrial domains have the same problem.** Aviation, nuclear, manufacturing have mature change control but data stays private/classified. Public databases publish outcomes without preceding change orders.

5. **Adjacent domains label differently.** "Change → failure" exists in JIT defect prediction (commits → bugs), telecom (config updates → degradation), clinical trials (protocols → outcomes) — but uses domain-specific vocabulary that ITSM-keyword searches miss.

---

## Recommendations

### For predictive validation (proving the ML thesis)

**Primary: ApacheJIT** — 28K labeled positives, verified public access, 2-4 day adapter. Validates "pre-execution attributes predict post-execution failure" at statistical scale. Document semantic gap (bug ≠ P1/P2 incident).

**Secondary: Constructed GitHub release→bug** — builds silver-tier dataset with release semantics (closer to CAB change windows). 3-4 day pilot on 5 CNCF repos. If positive rate 5-20% and manual audit precision ≥40%, scale to 20 repos.

**Supplementary: RAN Updates** — 1.9K real telecom config changes with measured service impact. Closest non-ITSM semantic match to "planned change → service degradation."

### For ITSM-specific validation

**BPI 2014 remains the only option.** Fix adapter to use `Related Change` as direct outcome linkage. Implement temporal split. Accept n=25 P1/P2 limitation — results are directional, not definitive.

**Contact ING authors** (Eileen.Kapel@ing.com) for potential academic data access. Low probability but high value.

### For full pipeline validation (all 9 stages)

**Not possible with public data.** No public dataset has runbooks, rollback plans, or CMDB snapshots alongside change records and incident outcomes. Enterprise partner required.

### What not to pursue

- Other BPI years (none have change data)
- Industrial/safety datasets (structural access barriers)
- Pure incident logs without change records (UCI ServiceNow, ASRS, NTSB)
- Feature flag/canary datasets (don't exist publicly)
- Postmortem→deploy joins at scale (<80 joinable records)

---

## Decision Tree

```
Q: What are you validating?
├── "Pipeline architecture works on real ITIL data"
│   └── BPI 2014 (already done ✓)
├── "Risk prediction works (precision/recall at scale)"
│   ├── Adjacent domain OK?
│   │   ├── Yes → ApacheJIT (28K positives, 2-4 days)
│   │   │        + RAN Updates (1.9K, high semantic fit)
│   │   └── Must be ITSM → BPI 2014 (n=25, fragile)
│   └── Willing to construct?
│       ├── Yes → GitHub release→bug pilot (3-4 days)
│       └── No → ApacheJIT only
├── "All 9 stages including prose artifacts"
│   └── Enterprise partner required (no public path)
└── "Prove concept before enterprise engagement"
    └── ApacheJIT + BPI 2014 + constructed pilot
        = multi-tier evidence for partnership pitch
```

---

**Related:** [Datasets](datasets.md) | [Evaluation](evaluation.md) | [Architecture](../ARCHITECTURE.md)
