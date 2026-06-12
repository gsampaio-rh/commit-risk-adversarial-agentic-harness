# ITSM Change Request Analyzer — Architecture Skeptic Review

**Date:** 2026-06-03  
**Reviewer role:** architecture-skeptic  
**Target:** [ARCHITECTURE.md](./ARCHITECTURE.md)  
**Audit artifact:** [skeptic-itsm-change-request-2026-06-03.json](../.harness/audits/skeptic-itsm-change-request-2026-06-03.json)

---

## Executive Summary

The architecture applies **minimum capable component** discipline well at MVP: all nine stages start at L1 script/template, zero LLM cost, and five stages never need models at SOTA. The design weakens where **P0 evaluation, budget, and SLA math** must prove the overnight-agent story: synthetic ITIL templates plus explicit service names make L1 metrics largely tautological, **SLA budgeting double-counts overlapping maintenance**, and **Risk Synthesis L2 on 50 CRs can exceed the stated $2 batch cap**. Fix three blockers before implementation; treat LLM ceilings (Runbook/Rollback L3, CAB narrative L2) as unproven until deterministic L2.5 paths fail on labeled holdouts.

---

## Blockers (must fix before build)

### B1 — SLA impact arithmetic double-counts overlapping maintenance

**Stage:** Schedule & SLA Analysis  

Stage 6 detects interval overlap for scheduling conflicts, but SLA impact **sums `expected_duration_min` per tier** without merging overlapping windows. Two CRs overlapping 60 minutes on the same service should contribute ~60 minutes of downtime, not 120. Injected failure #4 and the ≥90% SLA breach accuracy gate are **not achievable** under sum-based math when overlaps exist — or they pass for the wrong reason.

**Fix:** Per-(service, tier) **interval union** across all CR windows in the CAB batch, then compare union length to `monthly_downtime_budget_min - consumed_this_month_min`. Add GT cases: overlapping CRs where sum breaches but union does not (and vice versa).

---

### B2 — $2 batch budget incompatible with target Risk Synthesis L2

**Stage:** pipeline-wide  

Risk Synthesis L2 SOTA costs ~$0.02–$0.08/CR → **$1–$4 per 50-CR window**, before Runbook/Rollback encoder costs. USE-CASE and E2E metric #6 cap the batch at **<$2**. Target SOTA cannot run full-batch LLM narrative without selective routing or a higher budget.

**Fix:** P0 contract = L1-only (≈$0). Define Target routing: LLM narrative only for `conditional|reject` or tier-1/high-risk CRs, with token cap; or raise budget with documented per-CR ceiling.

---

### B3 — P0 synthetic eval is tautological for the stated agent value proposition

**Stage:** pipeline-wide  

The generator injects stale refs with **explicit CMDB service names** (L1 string match wins). Historical Pattern L1 uses **exact tuples** matching injections. CAB accuracy is measured against **the same rollup rules** Stage 9 implements. Passing ≥80% recall and ≥75% CAB accuracy does **not** prove cross-artifact semantic reasoning — it proves the generator and rule engine agree. This mirrors **Prior Auth #3** (Real Data 2/5, strong synth, zero real path): headline metrics risk overclaiming while realism knobs were deferred to this review.

**Fix:** Mandatory P0 sub-gates on **medium+ noise** injection subset; per-dimension recall on **prose-embedded failures only**; rename CAB metric to **rollup self-consistency** until P2; banner all P0 reports as synthetic-only.

---

## Concerns (should address; may defer with rationale in DECISIONS.md)

### C1 — Synthetic ITIL templates and overnight-agent eval validity

Template-clean CRs are an **upper bound** (documented). Without locked noise/messiness requirements, overnight batch processing validates a **rules engine**, not heterogeneous agent reasoning. **Defer only if** P0 gate explicitly accepts injection-only recall with a written “not agent-value proof” limitation.

**Recommendation:** Generator v1: ≥5 runbook prose variants, ≥20% implicit service refs, ≥15% CMDB alias drift; gate on recall variance across noise levels (Known Unknown #1 trigger: >15%).

---

### C2 — Runbook L3 LLM not justified before L2.5 deterministic layer

Implicit service references (“card processing gateway”) are **embedding + alias table** problems. Bastion/VPN staleness is **CMDB node type/status** rules. L3 triggers require L2 recall <70% on prose failures, but **synthetic data won't exercise L2**, so L3 is either dead or unmeasured.

**Alternative:** L2.5 script — service alias graph, infra-type deprecation rules; holdout ≥50 prose-embedded stale refs before any L3 A/B.

---

### C3 — Risk Synthesis L2+ LLM for CAB narrative

Cross-dimension examples (“staleness matters because rollback uses same deprecated service”) are **joins on `evidence.service_ref`** — a rule graph + template, not generation. L1 rollup (R1–R4) is already deterministic and auditable.

**Alternative:** Risk L1.5 rule graph emitting `conditional_actions[]`; human actionability rubric; LLM only if L1.5 <60% on ≥20 flagged CRs.

---

### C4 — P0 metric overclaiming (Prior Auth #3 parallel)

USE-CASE: Real Data **2/5**, Eval Readiness **★★★**, verdict **STRONGEST**. Architecture lists what P0 does *not* prove, but success tables still headline **CAB accuracy** and **clean-CR FP** without synthetic qualifiers in every row.

**Recommendation:** Dual scorecards (P0-Architecture vs P2-Production); downgrade external claims until enterprise partner path.

---

### C5 — Schedule + SLA merge: missed failure modes

| Gap | Risk |
|-----|------|
| Overlap only on exact `affected_services` | Misses shared infra under different service IDs |
| No UTC enforcement in stage 6 | False overlap / miss across zones |
| Merged stage debugging | Schedule vs SLA regressions conflated |
| Sum vs union SLA (blocker B1) | False SLA breaches |

**Alternative:** Document P0 limitation; optional `shared_infra_tags[]` at P1; separate abort codes for scheduling vs SLA sub-metrics.

---

### C6 — Optional PR scope flags without provenance (rollback credibility)

Rollback L1: `schema_migration: true` + “restore backup” → infeasible. Flags are **optional**, no CI attestation. Missing/wrong flags → rules fail open; synthetic #2 may always include flags while production CRs omit them.

**Alternative:** Generator cases with **flags absent** + prose-only migration signals; warn `scope unverified` when keywords present but flags null.

---

### C7 — LLM narrative vs deterministic recommendation mismatch

L2 keeps JSON recommendation from L1 rollup but LLM writes Markdown narrative. No validator prevents **approve** in JSON with **reject** language in prose — silent trust failure.

**Alternative:** Post-gen script: recommendation class must match narrative sentiment lexicon.

---

### C8 — Historical Pattern thresholds and dead L2 trigger

Injection #7: **≥3** P1 incidents; L1 alerts at **≥2** P1/P2. L2 needs semantic category mismatch — injections use **exact categories**. L2 advancement won't fire on primary dataset.

**Alternative:** Align thresholds; add ≥30 synonym-category injection cases.

---

### C9 — Rollback L3 for temporal reasoning

“Tuesday backup / Thursday–Saturday change” = **date parse + schedule compare + RPO table**, not LLM. Same pattern as Runbook L3 — won't trigger on synthetic.

**Alternative:** L2.5 temporal script before L3.

---

## Nits (optional improvements)

| ID | Item |
|----|------|
| N1 | Fan-out stages 3–5 when wall-clock >25 min at 100 CRs (Known Unknown #7) |
| N2 | Define P0 CAB narrative actionability rubric (15 CRs, 2 raters) — trigger currently unmeasurable |
| N3 | Elevate customer-facing missing comms from **warning** to **blocker** or auto-conditional |
| N4 | Golden-file ingest/normalize hashes — avoid misattributing parser failures to Runbook stage |

---

## Orientation: LLM vs deterministic

| Scope | Count / estimate |
|-------|------------------|
| Stages | 9 |
| MVP (all L1) | 9× script/template, **0 LLM** |
| SOTA (target, no optional L3) | 5 script, 2 encoder, 1 embedding, **1 LLM** (Risk Synthesis L2) |
| Optional LLM ceilings | Runbook L3, Rollback L3 (budget: max one per CR unless tier-1 + high risk) |
| MVP cost (50 CR) | ~$0 |
| Target cost (50 CR, full L2 SOTA) | ~$0.65–$4.15 — **exceeds $2** if Risk L2 on all CRs |

---

## Per-Stage Verdicts

| # | Stage | L1 start OK? | Triggers measurable? | Cheaper alt exists? | Verdict |
|---|-------|:------------:|:--------------------:|:-------------------:|---------|
| 1 | Ingest | ✓ | ✓ | ✓ (script) | Ship L1; golden fixtures for P1 formats |
| 2 | Normalize | ✓ | ✓ | ✓ | Ship L1 |
| 3 | Completeness Check | ✓ | ✓ | ✓ | Ship L1; revisit comms severity |
| 4 | Runbook Validation | ✓ | ✓ (not on synthetic) | ✓ L2.5 before L3 | L1 P0; encoder L2 OK; **deny L3** until holdout |
| 5 | Rollback Feasibility | ✓ | ✓ (not on synthetic) | ✓ L2.5 temporal | L1 P0; flags need provenance |
| 6 | Schedule & SLA | ✓ | ✓ after B1 fix | ✓ | **Fix union SLA** before build |
| 7 | Dependency Chain | ✓ | ✓ | ✓ | Ship L1; add noisy CMDB slice |
| 8 | Historical Pattern | ✓ | ✗ on synthetic | ✓ (synonym table before embed) | Align ≥3/≥2; add synonym injections |
| 9 | Risk Synthesis | ✓ | ✗ (actionability) | ✓ L1.5 rule graph | L1 P0; **defer L2 LLM** or route selectively |

---

## Mandatory challenge areas (harness plan)

### 1. Synthetic ITIL templates vs overnight-agent eval

**Verdict: Concern (feeds blocker B3).** Templates are composable but too clean for agent-proof eval. Noise injection is listed as P0 mitigation but **not gated**. Overnight run proves batch orchestration + checkpoint resume, not irreplaceable reasoning, until messiness knobs are required.

### 2. Runbook L3 and Risk Synthesis L2+ LLM necessity

**Runbook L3:** Not justified vs alias graph + encoder + infra-type rules. **Risk L2:** Not justified vs L1.5 cross-dimension rule graph + templates; also breaks budget at scale.

### 3. P0 eval overclaiming (Prior Auth #3)

Architecture **documents** the split; marketing fields (STRONGEST, Agent 23/25) and unified success metrics **obscure** it. CAB accuracy on synthetic rollup is **circular**, not expert alignment.

### 4. Schedule + SLA merge failure modes

Merge is efficient for I/O but combines **pairwise overlap logic** with **flawed aggregate SLA math** (B1). Infra-level overlap and timezones are out of scope for P0 — must be explicit limitations in eval reports.

### 5. Optional PR scope without diffs — rollback credibility

Scope flags are the right **minimal interface** to #4.5, but without provenance they are **untrusted hints**. P0 must test flag-absent paths; otherwise rollback stage credibility is synthetic-only.

---

## Debate Questions

1. Is **injection-only P0 pass** sufficient to close harness feat-5, or must **medium-noise recall** be a hard gate?
2. Should **Risk Synthesis L2 LLM** be removed from P0 scope entirely given the **$2/50-CR** constraint?
3. Which SLA model do enterprises use for co-scheduled changes: **union**, **max**, or **sum** of durations — and which should GT encode?
4. What is the production **trust boundary** for `pr_scope_flags` (CI job, manual, ITSM field)?
5. Should **Schedule** and **SLA** split once union math lands, or stay merged with strict sub-metric reporting?

---

## Resolution Table

| ID | Severity | Finding | Disposition | Resolution |
|----|----------|---------|------------|------------|
| B1 | Blocker | SLA math double-counts overlapping maintenance | **Fixed** | ARCHITECTURE.md Stage 6 now uses interval-union per (service, tier). Added SLA union-vs-sum discrimination sub-metric. DECISIONS.md entry added. |
| B2 | Blocker | $2 batch budget incompatible with Risk L2 full-batch | **Fixed** | ARCHITECTURE.md budget section now defines selective routing: LLM narrative only for conditional/reject CRs. Token cap at >50% flagged rate. DECISIONS.md entry added. |
| B3 | Blocker | P0 synthetic eval tautological | **Fixed** | ARCHITECTURE.md adds noise-tier P0 gates, prose-embedded failure subset metrics, renames CAB metric to "rollup self-consistency." All P0 reports carry synthetic-data banner. DECISIONS.md entry added. |
| C1 | Concern | Synthetic ITIL templates too clean | **Accept+implement** | Generator v1 noise requirements locked: ≥5 runbook variants, ≥20% implicit refs, ≥15% CMDB alias drift. DECISIONS.md entry added. |
| C2 | Concern | Runbook L3 LLM not justified before L2.5 | **Accept** | L2.5 deterministic layer (alias graph + infra-type rules) required before L3. L3 denied until holdout. DECISIONS.md entry added. |
| C3 | Concern | Risk Synthesis L2+ LLM for CAB narrative | **Accept** | L1.5 rule graph added before LLM. LLM only if L1.5 actionability <60%. DECISIONS.md entry added. |
| C4 | Concern | P0 metric overclaiming | **Accept** | Dual scorecards (P0-Architecture / P2-Production). External claims blocked until P2. DECISIONS.md entry added. |
| C5 | Concern | Schedule+SLA merge missed failure modes | **Defer** | shared_infra_tags[] deferred to P1. UTC enforcement in Normalize. Separate abort codes for sub-metrics. DECISIONS.md entry added. |
| C6 | Concern | PR scope flags without provenance | **Accept** | Generator includes flag-absent test cases. Rollback emits scope_unverified warning. DECISIONS.md entry added. |
| C7 | Concern | LLM narrative vs deterministic recommendation mismatch | **Accept** | Post-generation sentiment validator added. DECISIONS.md entry added. |
| C8 | Concern | Historical Pattern thresholds and dead L2 trigger | **Accept** | Injection threshold aligned to ≥2 P1/P2. ≥30 synonym-category cases added for L2. DECISIONS.md entry added. |
| C9 | Concern | Rollback L3 temporal reasoning | **Accept** | Merged with C2 — L2.5 temporal script required before L3. DECISIONS.md entry added. |
| N1 | Nit | Fan-out stages 3-5 at scale | Noted | Deferred to P2 scale testing (Known Unknown #7) |
| N2 | Nit | P0 CAB narrative actionability rubric | Noted | Addressed by L1.5 actionability gate in C3 resolution |
| N3 | Nit | Customer-facing missing comms severity | Noted | Deferred — current warning level is conservative; upgrade to blocker if CAB feedback supports it |
| N4 | Nit | Golden-file ingest/normalize hashes | Noted | Good practice — add to P1 implementation backlog |

**Harness result (post-fix):** **pass** — all 3 blockers resolved, all 9 concerns addressed (8 accepted + 1 deferred with trigger), 4 nits noted.
