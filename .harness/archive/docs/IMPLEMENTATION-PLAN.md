# ITSM Change Request Analyzer — Implementation Plan

Ordered build backlog for the MVP pipeline. All stages start at **L1** (script-only, zero LLM cost). Each step produces testable output validated against `schemas/` and `fixtures/`.

See [README.md](README.md) for project overview and [ARCHITECTURE.md](ARCHITECTURE.md) for design details.

---

## Steps

### 1. Project Setup

Create `pyproject.toml` (Python 3.12+, pydantic v2, pytest), directory structure (`src/pipeline/stages/`, `src/schemas/`, `tests/`), and `.gitignore`. Add `check-jsonschema` as dev dependency for fixture validation.

**Verify:** `pip install -e .` succeeds; `pytest` discovers test directory.

### 2. Pydantic Models from JSON Schemas

Generate Pydantic v2 models from `schemas/*.schema.json`. At minimum: `CRBundle`, `Finding`, `IngestOutput`, `NormalizeOutput`, and all stage output models. Models should validate against the JSON Schemas they're derived from.

**Verify:** `python -c "from src.schemas import CRBundle"` imports; loading `fixtures/cab-window-01/cr-001/itsm_record.json` into the ITSM record model succeeds.

### 3. BPI 2014 Adapter

CSV parser for `data/bpi2014/Detail_Change.csv` and `Detail_Incident.csv` (semicolon-delimited). GROUP BY Change ID for multi-CI rows. Map fields per `.harness/spike-bpi2014.md`. Emit `CRBundle` objects with only `itsm_record` required; nullable artifacts. Derive CAB windows from `CAB-approval needed = Y` grouped by ISO week.

**Verify:** Adapter produces valid partial bundles for sample changes; at least one bundle validates against `cr-bundle.schema.json` with only `itsm_record` populated.

### 4. Stage 1 — Ingest

Parse CR bundle artifacts (JSON files + Markdown content) into structured `IngestOutput`. Extract Markdown structure from runbook and rollback (headings, numbered steps, code blocks). Merge into single CR object per `schemas/stage-outputs/ingest.schema.json`.

**Verify:** Run on all 3 fixture CRs; output validates against ingest schema; runbook steps parsed with service references extracted.

### 5. Stage 2 — Normalize

Map fields to vendor-neutral schema. Normalize timestamps to ISO-8601 UTC. Standardize change types and risk categories. Derive `is_customer_facing` (CMDB tier 1-2 = true) and `affected_tier` (highest tier among affected services).

**Verify:** Output validates against normalize schema; `is_customer_facing` correct for each CR; timestamps in UTC.

### 6. Stage 3 — Completeness Check (primary-value milestone)

**Primary-value milestone (real-first):** Highest value on BPI 2014 where title, description, runbook, rollback, and comms are often missing.

ITIL-required artifact checklist per change type. Rules: normal requires runbook + rollback + risk assessment; customer-facing scope requires communication plan. Flag missing fields/artifacts.

**Verify:** cr-001 and cr-002 pass completeness; cr-003 flagged for missing communication plan (customer-facing auth-gateway). Compare against `expected/stage-outputs/completeness-cr-002.json`.

### 7. Stage 4 — Runbook Validation (L1)

String match CMDB service names/IDs against runbook step text. Flag steps referencing services with `status: deprecated` or `status: migrating`. Detect version mismatches.

**Verify:** cr-002 step 3 flagged for `payment-db-v3` (deprecated). cr-001 and cr-003 produce no runbook findings. Compare against `expected/stage-outputs/runbook-validate-cr-002.json`.

### 8. Stage 5 — Rollback Feasibility (L1)

Pattern matching on PR scope flags + rollback text keywords. Rule: `schema_migration: true` + rollback contains "restore backup" → infeasible (blocker). Extract duration estimate from text.

**Verify:** cr-002 flagged as infeasible (schema migration + restore backup). cr-001 and cr-003 feasible. Compare against `expected/stage-outputs/rollback-assess-cr-002.json`.

### 9. Stage 6 — Schedule & SLA Analysis (L1)

Cross-CR stage. Interval overlap detection on shared `affected_services`. SLA uses **interval-union** per (service, tier) — overlapping windows count merged duration, not sum. Compare against remaining monthly budget.

**Verify:** cr-001 × cr-003 overlap detected on order-service. SLA uses union math (not sum). Compare against `expected/stage-outputs/schedule-sla.json`.

### 10. Stage 7 — Dependency Chain (L1)

Cross-CR stage. Build DAG from CMDB `depends-on` edges. Topological sort for deployment ordering. Flag inversions (dependent scheduled before prerequisite). Cycle detection. Blast radius via transitive closure.

**Verify:** No ordering violations in fixture (no injection #5). cr-002 blast radius includes order-service (depends on payment-api). Compare against `expected/stage-outputs/dependency-chain.json`.

### 11. Stage 8 — Historical Pattern (L1) (primary-value milestone)

**Primary-value milestone (real-first):** BPI 2014 provides 56 recurring (CI, ChangeType) tuples with natural ground truth.

Exact match on `(service, change_category)` tuples against incident history. Alert when ≥2 past P1/P2 incidents exist for same tuple.

**Verify:** cr-002 flagged (payment-api + schema_migration = 2 prior P2 incidents). cr-001 and cr-003 not flagged. Compare against `expected/stage-outputs/historical-pattern-cr-002.json`.

### 12. Stage 9 — Risk Synthesis & CAB Report (L1)

Aggregate findings from stages 3-8. Deterministic severity rollup: R1 any blocker → reject; R2 ≥2 warnings across ≥2 dimensions → conditional; R3 single warning or info → approve; R4 no findings → approve. Template-based report generation (JSON + Markdown).

**Verify:** cr-001 approve, cr-002 reject, cr-003 conditional. Compare against all `expected/stage-9/` golden files.

### 13. E2E Pipeline Runner

Sequential orchestrator: reads CR bundles from input directory, runs stages 1-9, writes per-stage JSON to output directory. Stages 6-7 receive all CRs (cross-CR). Support `--resume-from-stage N` for development iteration. Batch summary generation.

**Verify:** `python -m pipeline run fixtures/cab-window-01/ --output output/` produces valid outputs for all 3 CRs. E2E test compares stage-9 output against golden files.

### 14. Synthetic CR Generator

Build the ITIL-template generator per ARCHITECTURE.md Generator Design section. **After** BPI adapter and core pipeline — supplementary regression data, not the primary build path. 200+ CRs with 8 injectable failure types (20+ per type), 30+ multi-label, 40+ clean. Fixed seed for reproducibility. Noise injection knobs: prose messiness, CMDB alias drift, optional field omission.

**Verify:** Generator produces 200+ CRs; all validate against cr-bundle schema; injection counts match volume targets; fixed seed produces identical output.

### 15. Eval Harness

Metrics collection against synthetic GT. Per-stage metrics (recall, precision per dimension). E2E metrics (aggregate recall, FP rate, CAB rollup self-consistency, schema compliance, cost, wall-clock, cross-run consistency). Dual scorecards: P0-Architecture vs P2-Production.

**Verify:** All per-stage metric targets from ARCHITECTURE.md section 9 are measured and reported. P0 success gate checklist passes or documents gaps.
