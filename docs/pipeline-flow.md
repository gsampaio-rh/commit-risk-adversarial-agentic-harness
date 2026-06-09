# End-to-End Pipeline Flow

Visual walkthrough of every stage, from raw data to CAB report. Uses BPI 2014 as the concrete example; the pipeline itself is generic.

---

## Full Flow

```
 ═══════════════════════════════════════════════════════════════════════════
  ADAPTER LAYER (pre-pipeline, source-specific)
 ═══════════════════════════════════════════════════════════════════════════

 Detail_Change.csv ────┐
 (semicolon-delimited)  │
                        ▼
              ┌──────────────────────────────────────────────────────────┐
              │  bpi2014.py                                              │
              │                                                          │
              │  load_changes()                                          │
              │  • reads CSV rows, groups by Change ID                   │
              │  • aggregates CI Names → affected_services               │
              │  • maps "Minor Change" → low, "Major" → high             │
              │  • maps "Emergency Change=Y" → emergency                 │
              │  • parses Planned Start/End → ScheduledWindow             │
              │  • drops rows without valid start/end dates               │
              │                                                          │
 Detail_Incident.csv ─►│  load_incident_index()                         │
              │        │  • parses incident CSV, keys by CI Name         │
              │        │  • maps Impact 1→P1, 2→P2, etc.                 │
              │                                                          │
              │  enrich_bundles_with_incidents()                          │
              │  • joins incidents to bundles by affected_services        │
              │                                                          │
              │  derive_cab_windows()                                     │
              │  • filters CAB-approval needed = Y                       │
              │  • groups by ISO week → dict[week_label, list[CRBundle]] │
              └────────────────────────┬─────────────────────────────────┘
                                       │
                   list[CRBundle] per CAB window
                   (no runbook, no rollback, no CMDB, no SLA)
                                       │
 ═══════════════════════════════════════▼═══════════════════════════════════
  PIPELINE (generic — works for any valid CRBundle)
  Runner: run_pipeline_batch() orchestrates the full flow
 ═══════════════════════════════════════════════════════════════════════════

  ┌─── PER-CR LOOP (for each CRBundle in the window) ──────────────────┐
  │                                                                     │
  │  ┌──────────────────────────────────────────────────────────────┐   │
  │  │  STAGE 1: INGEST                              run_ingest()  │   │
  │  │                                                              │   │
  │  │  For each artifact in the bundle:                            │   │
  │  │  • runbook (Markdown) → ParsedMarkdown                      │   │
  │  │    (sections, numbered steps, service refs, code blocks)     │   │
  │  │  • rollback_plan (Markdown) → ParsedMarkdown                │   │
  │  │  • JSON artifacts → pass through as-is                      │   │
  │  │                                                              │   │
  │  │  BPI path: runbook=None, rollback=None → both skipped       │   │
  │  │  JSON fields (itsm_record, incidents) pass through          │   │
  │  │                                                              │   │
  │  │  checkpoint: ingest.json                                     │   │
  │  └───────────────────────────┬──────────────────────────────────┘   │
  │                              │ IngestOutput                         │
  │                              ▼                                      │
  │  ┌──────────────────────────────────────────────────────────────┐   │
  │  │  STAGE 2: NORMALIZE                        run_normalize()  │   │
  │  │                                                              │   │
  │  │  • standardize change_type → standard | normal | emergency  │   │
  │  │  • standardize risk_category → low | medium | high          │   │
  │  │  • derive is_customer_facing from CMDB tier (1-2 = true)    │   │
  │  │  • derive affected_tier = min tier across affected services  │   │
  │  │  • carry all parsed artifacts forward                       │   │
  │  │                                                              │   │
  │  │  BPI path: no CMDB → is_customer_facing=false, tier=4      │   │
  │  │                                                              │   │
  │  │  checkpoint: normalize.json                                  │   │
  │  └───────────────────────────┬──────────────────────────────────┘   │
  │                              │ NormalizeOutput                      │
  │                              ▼                                      │
  │  ┌──────────────────────────────────────────────────────────────┐   │
  │  │  SKIP CHECK                              determine_skips()  │   │
  │  │                                                              │   │
  │  │  • runbook=None     → skip runbook_validation (info)        │   │
  │  │  • rollback=None    → skip rollback_feasibility (info)      │   │
  │  │  • cmdb=None        → skip dependency_chain (info)          │   │
  │  │                                                              │   │
  │  │  BPI path: all three skipped (artifacts absent)             │   │
  │  └───────────────────────────┬──────────────────────────────────┘   │
  │                              │ stages_skipped + info findings       │
  │                              ▼                                      │
  │  ┌──────────────────────────────────────────────────────────────┐   │
  │  │  STAGE 3: COMPLETENESS CHECK           run_completeness()   │   │
  │  │                                                              │   │
  │  │  Universal checks (all types):                               │   │
  │  │  • title missing → warning                                   │   │
  │  │  • description missing → warning                             │   │
  │  │  • affected_services empty → BLOCKER                         │   │
  │  │                                                              │   │
  │  │  Per change_type:                                            │   │
  │  │  • normal  → runbook required (BLOCKER), rollback (BLOCKER) │   │
  │  │  • emergency → rollback required (BLOCKER)                  │   │
  │  │  • standard → runbook optional (info)                       │   │
  │  │                                                              │   │
  │  │  Customer-facing (is_customer_facing=true):                  │   │
  │  │  • comms plan missing → warning                             │   │
  │  │                                                              │   │
  │  │  BPI path: normal CRs get runbook+rollback BLOCKERS         │   │
  │  │  → most BPI CRs are rejected here                           │   │
  │  │                                                              │   │
  │  │  checkpoint: completeness.json                               │   │
  │  └───────────────────────────┬──────────────────────────────────┘   │
  │                              │ CompletenessOutput (findings[])      │
  │                              ▼                                      │
  │  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐   │
  │    STAGE 4: RUNBOOK VALIDATION         NOT IMPLEMENTED            │
  │  │ Would cross-ref runbook steps against CMDB state            │   │
  │    Skip recorded when runbook=None (always for BPI)               │
  │  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘   │
  │                              │                                      │
  │  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐   │
  │    STAGE 5: ROLLBACK FEASIBILITY       NOT IMPLEMENTED            │
  │  │ Would evaluate if rollback plan is executable               │   │
  │    Skip recorded when rollback_plan=None (always for BPI)         │
  │  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘   │
  │                              │                                      │
  │                              ▼                                      │
  │  ┌──────────────────────────────────────────────────────────────┐   │
  │  │  STAGE 8: HISTORICAL PATTERN    run_historical_pattern()    │   │
  │  │                                                              │   │
  │  │  L1 (exact match):                                           │   │
  │  │  • group incidents by (service, change_category)             │   │
  │  │  • ≥5 incidents OR any P1 → BLOCKER                          │   │
  │  │  • ≥2 P1/P2 incidents → warning                              │   │
  │  │                                                              │   │
  │  │  L2 (embedding similarity):                                  │   │
  │  │  • encode CR + incidents with all-MiniLM-L6-v2               │   │
  │  │  • cosine similarity ≥ 0.75 → match                          │   │
  │  │  • same severity thresholds as L1                             │   │
  │  │  • falls back to L1 if sentence-transformers absent          │   │
  │  │                                                              │   │
  │  │  Dual mode: runs both, deduplicates by incident_id           │   │
  │  │                                                              │   │
  │  │  BPI path: matches via exact category (L1).                  │   │
  │  │  L2 validated on synthetic data — BPI lacks semantic variety │   │
  │  │                                                              │   │
  │  │  checkpoint: historical-pattern.json                          │   │
  │  └───────────────────────────┬──────────────────────────────────┘   │
  │                              │ HistoricalPatternOutput              │
  │                              ▼                                      │
  │  AGGREGATE per-CR findings:                                         │
  │  skip_findings + completeness.findings + historical_pattern.findings│
  │                                                                     │
  └─────────────────────────────────────────────────────────────────────┘
                                 │
           all per-CR reports + NormalizeOutputs collected
                                 │
                                 ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │  STAGE 6: SCHEDULE & SLA ANALYSIS (cross-CR)  run_schedule_sla()   │
  │                                                                      │
  │  Runs ONCE per CAB window, across ALL CRs:                           │
  │  • for each pair of CRs:                                             │
  │    - find shared affected_services                                   │
  │    - check if scheduled_windows overlap                              │
  │    - identical windows → BLOCKER                                     │
  │    - overlap ≥ 60min → warning                                       │
  │    - overlap < 60min → info                                          │
  │                                                                      │
  │  Mode:                                                               │
  │  • sla_definitions present → full mode (overlap + SLA breach math)  │
  │  • sla_definitions absent → overlap_only mode                       │
  │                                                                      │
  │  BPI path: overlap_only mode (no SLA data). 27 conflicts across     │
  │  373 CRs in 50 windows.                                             │
  │                                                                      │
  │  checkpoint: schedule-sla.json                                       │
  └──────────────────────────────┬───────────────────────────────────────┘
                                 │ ScheduleSlaOutput
                                 │ (scheduling_conflicts injected into
                                 │  affected CRs' finding lists)
                                 ▼
  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
    STAGE 7: DEPENDENCY CHAIN (cross-CR)      NOT IMPLEMENTED
  │ Would trace CMDB service graph: topological sort, cycles, blast    │
    radius. Skip recorded when cmdb_snapshot=None (always for BPI)
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
                                 │
                                 ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │  STAGE 9: RISK SYNTHESIS & CAB REPORT                                │
  │                                      synthesize_report()             │
  │                                      synthesize_summary()            │
  │                                                                      │
  │  Per-CR (synthesize_report):                                         │
  │  ┌────────────────────────────────────────────────────────────────┐  │
  │  │  R1-R4 deterministic rollup:                                   │  │
  │  │  • R1: any BLOCKER finding          → Reject  (critical)      │  │
  │  │  • R2: ≥2 warnings, ≥2 dimensions   → Conditional (high)     │  │
  │  │  • R3: 1 warning or info only       → Approve (medium)       │  │
  │  │  • R4: no findings                  → Approve (low)           │  │
  │  │                                                                │  │
  │  │  L1 (template): per-dimension Markdown report with severity   │  │
  │  │  badges, evidence citations, remediation actions               │  │
  │  │                                                                │  │
  │  │  L2 (llm_narrative): selective routing                        │  │
  │  │  • approve CRs → template only (save cost)                   │  │
  │  │  • conditional/reject CRs → LLM cross-dimension narrative    │  │
  │  │  • budget ceiling: $2 per 50-CR batch                         │  │
  │  │  • falls back to template on API error                        │  │
  │  └────────────────────────────────────────────────────────────────┘  │
  │                                                                      │
  │  Per-window (synthesize_summary):                                    │
  │  ┌────────────────────────────────────────────────────────────────┐  │
  │  │  • disposition breakdown: approve / conditional / reject       │  │
  │  │  • cross-CR conflicts from schedule analysis                  │  │
  │  │  • processing stats: wall clock, cost                         │  │
  │  └────────────────────────────────────────────────────────────────┘  │
  │                                                                      │
  │  BPI path: 373 CRs → 100% reject (missing mandatory artifacts)     │
  │                                                                      │
  │  checkpoints:                                                        │
  │  • per-CR:  cab-report.json + change-risk-assessment.md             │
  │  • per-window: cab-summary.json + cab-summary.md                    │
  └──────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
 ═══════════════════════════════════════════════════════════════════════════
  OUTPUT
 ═══════════════════════════════════════════════════════════════════════════

  output/
  └── 2014-W05/                          (one dir per CAB window)
      ├── schedule-sla.json              (cross-CR conflicts)
      ├── cab-summary.json               (disposition breakdown)
      ├── cab-summary.md                 (CAB chair summary)
      ├── CR-001/
      │   ├── ingest.json                (stage 1 checkpoint)
      │   ├── normalize.json             (stage 2 checkpoint)
      │   ├── completeness.json          (stage 3 checkpoint)
      │   ├── historical-pattern.json    (stage 8 checkpoint)
      │   ├── cab-report.json            (final per-CR report)
      │   └── change-risk-assessment.md  (human-readable report)
      ├── CR-002/
      │   └── ...
      └── ...
```

---

## Data Types Between Stages

```
CRBundle ──► IngestOutput ──► NormalizeOutput ──┬──► CompletenessOutput
(adapter)    (stage 1)        (stage 2)         │    (stage 3)
                                                │
                                                ├──► HistoricalPatternOutput
                                                │    (stage 8)
                                                │
                                                └──► [NormalizeOutput passed
                                                      to Schedule & SLA
                                                      as list for cross-CR]

ScheduleSlaOutput (stage 6) ───┐
                               ├──► all findings aggregated
CompletenessOutput.findings ───┤
HistoricalPatternOutput.findings──┤
skip_findings ─────────────────┘
                               │
                               ▼
                          CabReport (stage 9, per-CR)
                          CabSummary (stage 9, per-window)
```

---

## What BPI 2014 Exercises vs. What It Doesn't

| Aspect | Exercised | Not exercised (needs full bundles) |
|--------|-----------|-----------------------------------|
| CSV → CRBundle adapter | Yes | — |
| Ingest: JSON passthrough | Yes | Markdown parsing (no runbooks) |
| Normalize: type/risk mapping | Yes | CMDB-derived fields (no CMDB) |
| Completeness: artifact checks | Yes (catches missing artifacts) | — |
| Runbook Validation (stage 4) | — | Not implemented |
| Rollback Feasibility (stage 5) | — | Not implemented |
| Schedule & SLA: overlap detection | Yes (overlap_only mode) | SLA breach math (no SLA data) |
| Dependency Chain (stage 7) | — | Not implemented |
| Historical Pattern L1 | Yes (exact category match) | — |
| Historical Pattern L2 | Validated on synthetic data | BPI lacks semantic variety |
| Risk Synthesis L1 | Yes (100% reject → R1 path) | Conditional/approve paths (all BPI CRs have blockers) |
| Risk Synthesis L2 | Implemented, validated separately | BPI CRs don't reach LLM (all reject) |

A ServiceNow adapter producing bundles with runbooks, rollback plans, and CMDB snapshots would exercise every pipeline path.

---

**Related:** [Architecture](../ARCHITECTURE.md) | [Glossary](glossary.md) | [Datasets](datasets.md) | [Evaluation](evaluation.md)
