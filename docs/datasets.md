# Datasets

## Data Strategy

**Real-first:** BPI Challenge 2014 real ITIL change records drive the adapter, partial-bundle schemas, and primary-value stages (Completeness, Historical Pattern). Synthetic fixtures validate full-bundle and prose-artifact code paths. We accept synthetic metrics as an upper bound — enterprise data with prose artifacts is required before commercial claims.

---

## BPI Challenge 2014 (Primary)

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
| Changes with linked incidents | 1,747 |

### What it covers

- **Ingest + Normalize:** Structured ITSM fields (change ID, type, risk, services, schedule window, requestor)
- **Completeness:** All CRs missing runbook/rollback/comms → 100% flagged incomplete (expected)
- **Schedule overlap:** Real scheduling conflicts across shared CIs
- **Historical Pattern:** 56 (CI, ChangeType) tuples with >= 2 incidents → natural ground truth for L1 exact match

### What it lacks

- No runbooks, rollback plans, or communication plans → stages 4/5 skip
- 97% missing Scheduled Downtime → stage 6 degrades to overlap-only
- No CMDB snapshot → stage 7 skips
- `change_category` values are opaque IDs (e.g., "C00012345"), not semantic text → L2 embedding adds no value on BPI data

### Adapter

`src/cr_analyzer/adapters/bpi2014.py` — parses `Detail_Change.csv` (semicolon-delimited, 21 columns) and `Detail_Incident.csv`.

Key mappings:
- Multi-CI rows grouped by Change ID into single `CRBundle`
- Risk: Minor → low, Business → medium, Major Business → high
- Type: Emergency Change=Y → emergency; "Standard *" → standard; "Release *" → normal; default → normal
- `affected_services` from grouped CI Name values
- CAB windows: filter `CAB-approval needed=Y`, group by ISO week of Planned Start

Download script: `scripts/download_bpi2014.sh`. Data stored in `data/bpi2014/` (gitignored).

---

## Synthetic Fixtures (Regression)

`fixtures/cab-window-01/` contains 3 CR bundles for smoke testing and regression:

| CR | Purpose | Bundle |
|----|---------|--------|
| `cr-001` | Full-bundle smoke test — normal change, medium risk, all 9 artifacts | Complete |
| `cr-002` | Full-bundle, high-risk — payment API schema migration | Complete |
| `cr-003` | Schedule overlap test — overlapping window with cr-001, no comms plan | 8 of 9 artifacts |

Each CR directory contains up to 9 files: `itsm_record.json`, `runbook.md`, `rollback_plan.md`, `cmdb_snapshot.json`, `sla_definitions.json`, `maintenance_schedule.json`, `communication_plan.md`, `incident_history.json`, `pr_scope_flags.json`. `cr-003` omits `communication_plan.md` by design.

Fixtures are used in `tests/conftest.py` for pytest integration.

---

## Requirements for Predictive Validation

To validate that the pipeline correctly predicts which changes will cause incidents, a dataset needs:

| Requirement | Why | BPI 2014 status |
|-------------|-----|-----------------|
| Change records with structured fields | Pipeline input | Yes (18K changes) |
| Incident records with **direct linkage** to causing change | Ground truth labels | Partial (560 incidents, 231 changes, via `Related Change`) |
| 100+ changes with linked incidents | Statistical power | No (25 high-severity, 231 total) |
| Temporal ordering | Prevent data leakage | Yes (incident `Open Time` vs change `Planned Start`) |
| Prose artifacts (runbooks, rollback plans) | Exercise full pipeline | No |

BPI 2014 is the only public **ITSM** dataset that partially meets these criteria. v2 research ([dataset-research.md](dataset-research.md)) found adjacent-domain candidates with tiered labels (A/B/C/D):

| Candidate | Positives | Label tier | ITSM fit |
|-----------|-----------|------------|----------|
| ApacheJIT | 28K bug-inducing commits | B | Methodology proxy (commit ≈ change) |
| Mozilla Regressors | 12K bug-introducing sets | A | Methodology proxy |
| Constructed release→bug | 500-3K (estimated) | D | Release ≈ change window |
| RAN Updates | 1,931 adverse-impact | B | High semantic fit (config → degradation) |
| BPI 2014 | 25 P1/P2, 231 total linked | A (sparse) | Only true ITSM option |

For predictive validation at scale, ApacheJIT is the recommended primary path. BPI 2014 suffices for ITSM-specific proof-of-concept only.

---

## Future Datasets

For planned but not yet integrated datasets and the full research into public ITSM datasets, see:
- [dataset-research.md](dataset-research.md) — verified assessment of all public candidates (2026)
- [.harness/archive/docs/ARCHITECTURE-aspirational.md](../.harness/archive/docs/ARCHITECTURE-aspirational.md#p1-datasets-not-yet-integrated) — aspirational dataset plans
