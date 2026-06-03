# ITSM Change Request Analyzer — Decision Log

Tracks architectural decisions, debate outcomes, and rejected alternatives. Newest entries at the top.

Each entry records WHY a choice was made — not just WHAT was chosen. This enables future revisiting when conditions change (new models, new data, new requirements).

---

## How to Use

- **After a debate session:** Add an entry summarizing the outcome
- **After a build reveals something:** Add an entry capturing the learning
- **When revisiting a trigger fires:** Update the original entry with the new decision

---

## Decisions

### 2026-06-03 — Real-First Data Strategy (BPI Challenge 2014)

**Context:** Architecture and schemas assumed synthetic-first evaluation: full CR bundles, all nine stages always run, synthetic generator as primary dataset. Spike on BPI 2014 (see [.harness/spike-bpi2014.md](../.harness/spike-bpi2014.md)) showed real Rabobank ITIL change records are usable from day one — 18K changes, 51 CAB windows, 56 natural (CI, ChangeType) ground-truth tuples for Historical Pattern, 25K schedule overlaps — while prose artifacts (runbook, rollback, communication plan) are absent in public data.

**Decision:** Adopt a **real-first** strategy. **BPI Challenge 2014** is the **primary dataset** for P0/P1 structured-field validation, adapter development, and partial-bundle pipeline behavior. Synthetic fixtures (`fixtures/cab-window-01/`) remain as **regression tests** for full-bundle, prose-artifact stages. Schemas require only `itsm_record`; other artifacts nullable. Stages 4 (Runbook), 5 (Rollback), and 7 (Dependency Chain) **skip** when input artifacts are missing; stage 6 **degrades** to overlap-only when `sla_definitions` absent (97% missing Scheduled Downtime in BPI per spike).

**Rationale:** Building against synthetic-only schemas would force incorrect implementation contracts before feat-1. Real data grounds Completeness Check and Historical Pattern as primary-value stages; synthetic data supplements stages that need prose GT.

**Decided by:** Builder (chore-1), informed by spike-1

**Revisit trigger:** If enterprise partner supplies prose artifacts at P2, re-balance primary dataset weighting and tighten required bundle fields for production adapters.

---

### 2026-06-03 — Real Dataset Research: BPI 2014, UCI, EnterpriseOps-Gym

**Context:** The exploration document rated ITSM Change Request at Real Data 2/5 (★★☆☆) claiming "ZERO public change records." This was accepted without verification during architecture design. Post-commit review challenged this claim with a web search.

**Findings — datasets that exist:**
1. **BPI Challenge 2014** (Rabobank) — real ITIL change records from a bank, with change→incident linkage, CSV format, free access via 4TU.ResearchData. Most relevant real dataset.
2. **UCI Incident Management** — 24,918 incidents from a real ServiceNow instance, 141,712 events. Incident data, not change data, but validates Historical Pattern stage.
3. **ServiceNow-AI/EnterpriseOps-Gym** (2026) — 181 ITSM tasks, containerized benchmark with MCP servers. ServiceNow's official agent evaluation framework.
4. **ServiceNow-itsm-safety-bench** — 10 change_requests + 50 incidents + 20 CMDB items. Small but validates schema mapping.
5. **VuduVations/itsm-change-management-benchmark** — 15 incidents, 68 CMDB items, 3 CAB scenarios. First public change management benchmark.
6. **ArXiv 2604.13462** — 175K change tickets from a bank (data not public, paper only). Validates problem formulation.

**What's still missing:** No public dataset includes runbooks, rollback plans, or communication plans — the prose artifacts that are the core of our agent's value proposition. BPI 2014 has structured change fields but not the operational artifacts.

**Decision:** Update Real Data from 2/5 to 3/5 (★★★). Update Total from 25/35 to 26/35. Update Eval Readiness from ★★★ to ★★★½. Add BPI 2014 and UCI as P1 datasets for structured-field validation. Keep synthetic generator as P0 primary for prose artifacts. Note: exploration document retains original scores as locked baseline — the upgrade is experiment-local.

**Rationale:** The original "ZERO public data" claim was wrong. Structured change records exist and are usable for partial pipeline validation. The gap is specifically in prose operational artifacts (runbooks, rollback plans), not in change records themselves. This distinction matters for scoping what P1 can validate vs what requires P2 enterprise partnership.

**Decided by:** Post-commit review (user challenge)

**Revisit trigger:** If BPI 2014 change records include free-text fields that approximate runbook quality (unlikely given HP Service Manager's structure), Real Data could increase to 3.5/5.

---

### 2026-06-03 — [SKEPTIC] SLA Interval-Union Math (B1 Blocker Resolution)

**Context:** Architecture-skeptic review (B1) found that Stage 6 SLA impact calculation summed `expected_duration_min` per tier, double-counting overlapping maintenance windows. Two CRs overlapping 60 min on the same service would show 120 min of downtime.

**Options considered:**
1. Keep sum-based math — simpler but wrong for overlapping windows
2. Per-(service, tier) interval union — correct but adds algorithmic complexity
3. Use max duration instead of sum — underestimates non-overlapping windows

**Decision:** Interval union per (service, tier) (option 2). Generator must include GT cases where sum breaches but union does not, and vice versa. Added SLA union-vs-sum discrimination sub-metric (≥5 test cases).

**Rationale:** SLA math must be correct — wrong downtime calculations undermine the agent's credibility with CAB chairs. Interval union is O(n log n) per tier — negligible performance impact on 50-CR windows.

**Decided by:** Architecture-skeptic blocker resolution

**Revisit trigger:** If enterprises use different SLA models (max instead of union for co-scheduled changes), make the SLA algorithm configurable.

---

### 2026-06-03 — [SKEPTIC] Risk Synthesis L2 Selective Routing (B2 Blocker Resolution)

**Context:** Architecture-skeptic review (B2) found that full-batch Risk Synthesis L2 (LLM narrative) at $0.02-0.08/CR would cost $1-4 for a 50-CR window, exceeding the $2 batch cap.

**Options considered:**
1. Raise budget cap to $5 — accommodates LLM but changes cost commitment
2. Run LLM narrative only for conditional/reject CRs — typical 40% of batch
3. Remove LLM from Risk Synthesis entirely — template-only forever

**Decision:** Selective routing (option 2). LLM narrative runs only for CRs with conditional or reject recommendation from L1 rollup. Approve-path CRs use L1 template report. Token cap (300 tokens/CR) if conditional+reject rate exceeds 50%.

**Rationale:** Approve-path CRs don't need narrative synthesis — "no issues found" is a template. Conditional/reject CRs are where cross-dimension reasoning matters. Typical CAB window: ~60% approve → LLM runs on ~20 CRs (~$0.40-1.60), within budget.

**Decided by:** Architecture-skeptic blocker resolution

**Revisit trigger:** If >60% of CRs are conditional/reject (indicating noisy pipeline or strict rules), either relax conditional threshold or raise budget with cost-benefit documentation.

---

### 2026-06-03 — [SKEPTIC] Noise-Tier P0 Gates and Metric Renaming (B3 Blocker Resolution)

**Context:** Architecture-skeptic review (B3) found P0 eval is tautological: generator injects failures with explicit service names (L1 string match wins), and CAB accuracy is measured against the same rollup rules the pipeline implements. Metrics don't prove cross-artifact reasoning.

**Options considered:**
1. Accept injection-only metrics as sufficient for P0 architecture validation
2. Add mandatory noise-tier gates: medium-noise subset with prose-embedded refs, CMDB alias drift, missing fields
3. Defer to P2 and mark all P0 metrics as "self-consistency only"

**Decision:** Option 2 — noise-tier gates with metric renaming. Per-dimension recall must be measured on both template-clean and medium-noise subsets. CAB metric renamed to "rollup self-consistency" until P2 expert assessment. All P0 reports carry synthetic-data banner.

**Rationale:** Pure injection-on-template metrics are necessary but not sufficient. Medium-noise subset (prose-embedded refs, alias drift) tests whether the pipeline handles realistic input degradation. Renaming CAB metric prevents overclaiming. The gap between template-clean and medium-noise recall quantifies the synthetic realism ceiling.

**Decided by:** Architecture-skeptic blocker resolution

**Revisit trigger:** If medium-noise recall is within 5% of template-clean recall, the noise tier adds complexity without signal — simplify to single-tier eval.

---

### 2026-06-03 — [SKEPTIC] Generator Noise Requirements (C1 Accept)

**Context:** Architecture-skeptic concern C1 flagged that without locked noise/messiness requirements, overnight batch processing validates a rules engine, not heterogeneous agent reasoning.

**Decision:** Accept and implement. Generator v1 must include: ≥5 runbook prose variants, ≥20% implicit service references (not exact CMDB names), ≥15% CMDB alias drift (service renamed since runbook authoring). Recall variance across noise levels becomes a P0 gate (>15% variance = realism ceiling is low).

**Decided by:** Architecture-skeptic concern acceptance

**Revisit trigger:** If noise requirements make L1 recall unreachable (<50%), reduce noise percentage but document the ceiling limitation.

---

### 2026-06-03 — [SKEPTIC] Deny Runbook L3 and Rollback L3 Until Holdout Proves Need (C2, C9 Accept)

**Context:** Skeptic concerns C2 and C9 argued that Runbook L3 (semantic staleness) and Rollback L3 (temporal reasoning) are not justified before exhausting deterministic L2.5 alternatives: service alias graph, infra-type deprecation rules, date-parse + schedule-compare + RPO tables.

**Decision:** Accept. Add explicit L2.5 levels for both stages: Runbook L2.5 = alias graph + infra-type rules; Rollback L2.5 = temporal script (date parse + schedule compare). L3 LLM denied until L2.5 recall < 70% on a holdout of ≥50 prose-embedded failures.

**Decided by:** Architecture-skeptic concern acceptance

**Revisit trigger:** If L2.5 achieves ≥80% recall on prose-embedded holdout, reclassify SOTA as L2.5 (no LLM needed). Update LLM Justification table accordingly.

---

### 2026-06-03 — [SKEPTIC] Risk Synthesis L1.5 Rule Graph Before LLM (C3 Accept)

**Context:** Skeptic concern C3 argued that cross-dimension examples ("staleness matters because rollback uses same deprecated service") are joins on `evidence.service_ref` — a rule graph + template, not generation.

**Decision:** Accept. Add Risk Synthesis L1.5: rule graph joining findings by shared service_ref to emit `conditional_actions[]` with cross-dimension context. LLM at L2 only if L1.5 actionability < 60% on ≥20 flagged CRs via human actionability rubric.

**Decided by:** Architecture-skeptic concern acceptance

**Revisit trigger:** If L1.5 achieves ≥70% actionability on human spot-check, reclassify SOTA as L1.5 rule graph (no LLM at SOTA).

---

### 2026-06-03 — [SKEPTIC] Dual Scorecards P0/P2 (C4 Accept)

**Context:** Skeptic concern C4 noted that USE-CASE headlines CAB accuracy and FP rate without synthetic qualifiers, risking overclaiming given Real Data 2/5.

**Decision:** Accept. All P0 metric reports use dual scorecards: "P0-Architecture" (synthetic) and "P2-Production" (enterprise partner). External communications blocked until P2 gate passes.

**Decided by:** Architecture-skeptic concern acceptance

**Revisit trigger:** N/A — structural requirement.

---

### 2026-06-03 — [SKEPTIC] Schedule+SLA Merge Limitations (C5 Defer)

**Context:** Skeptic concern C5 flagged missed failure modes: shared infra under different service IDs, timezone enforcement, conflated debugging.

**Decision:** Defer shared_infra_tags[] to P1. Document P0 limitations: overlap detection is exact affected_services match only. Add separate abort codes for scheduling vs SLA sub-metrics (debugging concern). UTC enforcement added to Normalize stage (all timestamps normalized to UTC).

**Decided by:** Architecture-skeptic concern deferral

**Revisit trigger:** If >20% of injected scheduling conflicts are missed because services use different IDs for shared infrastructure, add shared_infra_tags[] at P1.

---

### 2026-06-03 — [SKEPTIC] PR Scope Flag Provenance (C6 Accept)

**Context:** Skeptic concern C6 noted that optional PR scope flags have no CI attestation. Missing or wrong flags cause rollback rules to fail open.

**Decision:** Accept. Generator must include test cases with flags absent + prose-only migration signals. Rollback stage emits `scope_unverified` warning when keywords suggest migration but flags are null. P0 tests flag-absent path explicitly.

**Decided by:** Architecture-skeptic concern acceptance

**Revisit trigger:** If #4.5 Change Window Risk experiment matures, define shared CI attestation schema for scope flags.

---

### 2026-06-03 — [SKEPTIC] LLM Narrative Sentiment Validator (C7 Accept)

**Context:** Skeptic concern C7 found that Risk Synthesis L2 keeps JSON recommendation from L1 rollup but LLM writes Markdown narrative. No validator prevents approve in JSON with reject language in prose.

**Decision:** Accept. Add post-generation script: recommendation class must match narrative sentiment (lexicon-based check). Mismatch → regenerate with explicit instruction or fall back to L1 template.

**Decided by:** Architecture-skeptic concern acceptance

**Revisit trigger:** If sentiment validator produces >10% false mismatches (narrative uses cautionary language for approve-with-info), tune lexicon or switch to NLI-based consistency check.

---

### 2026-06-03 — [SKEPTIC] Historical Pattern Threshold Alignment and Synonym Injection (C8 Accept)

**Context:** Skeptic concern C8 found that injection #7 uses ≥3 P1 incidents but L1 alerts at ≥2 P1/P2. L2 needs semantic category mismatch but injections use exact categories.

**Decision:** Accept. Align: injection uses ≥2 P1/P2 (matching L1 threshold). Add ≥30 synonym-category injection cases for L2 validation (e.g., "config change" vs "configuration update").

**Decided by:** Architecture-skeptic concern acceptance

**Revisit trigger:** If synonym-category injections make L1 recall collapse (expected), this validates L2 advancement trigger.

---

### 2026-06-03 — Historical Pattern Engine: Rule-Based Before ML

**Context:** The pipeline includes a stage that alerts when a CR resembles past incidents (e.g., "last 3 schema migrations on payment-service caused P1 incidents"). Could be built as pattern matching on structured incident data or as an ML classifier trained on incident/change pairs.

**Options considered:**
1. ML classifier on incident/change feature vectors — handles fuzzy patterns, requires labeled training data
2. Rule-based matching on (service, change-type, outcome) tuples — deterministic, interpretable, works on small datasets
3. Embedding similarity between CR text and past incident descriptions — handles semantic overlap, expensive per-CR

**Decision:** Rule-based matching on structured fields (service + change-type + outcome) at L1. Embedding similarity deferred to L2 as a trigger-based advancement.

**Rationale:** Incident records are structured data. "Payment-service schema-migration → P1 incident" is a tuple match, not a reasoning task. Component-toolkit hierarchy: Script > Embedding > ML for structured pattern lookups. Rule-based matching also produces interpretable alerts ("3 of last 5 schema migrations on this service caused incidents") vs. opaque similarity scores.

**Decided by:** Planner (principled — component-toolkit hierarchy)

**Revisit trigger:** If >30% of relevant patterns are semantic (e.g., runbook phrasing similarity to past failed runbooks), advance to L2 embedding.

---

### 2026-06-03 — CAB Recommendation: Trinary (Approve / Conditional / Reject)

**Context:** The agent's final output includes a per-CR recommendation. Options range from binary (approve/reject) to a numeric risk score to a categorical recommendation with conditions.

**Options considered:**
1. Binary approve/reject — simple but loses nuance; most CRs are conditional, not clearly reject
2. Numeric risk score (1-100) — granular but unactionable; CAB still asks "so do we approve this?"
3. Trinary approve/conditional/reject — matches real CAB decisions, conditional includes required remediation
4. Five-level (auto-approve/approve/conditional/hold/reject) — over-segmented for synthetic validation

**Decision:** Trinary (approve / conditional / reject) with per-finding evidence.

**Rationale:** Real CABs make three kinds of decisions: "go ahead" (approve), "fix X and Y then go" (conditional), and "no, rethink this" (reject). A numeric score punts the decision. Binary loses the conditional middle ground where most real CRs land. Conditional comes with specific findings that must be addressed — the agent tells you WHAT to fix, not just that something is wrong.

**Decided by:** Planner (domain-informed)

**Revisit trigger:** If >40% of CRs are conditional and all conditions are trivial (e.g., "add monitoring plan"), consider auto-approve with warnings for low-severity conditions.

---

### 2026-06-03 — #8 Runbook Quality Overlap: Scope Boundary

**Context:** Exploration #8 "Runbook Gap Analyzer" is a standalone use case that analyzes runbooks for completeness and drift. The ITSM CR Analyzer also reads runbooks. Where is the boundary?

**Options considered:**
1. ITSM CR Analyzer fully subsumes runbook analysis — comprehensive but large scope
2. ITSM CR Analyzer checks runbook only in CR context (is this runbook valid for THIS change?); #8 does standalone runbook quality
3. No runbook analysis in ITSM — just check it exists

**Decision:** Option 2 — contextual runbook validation only. The ITSM analyzer checks: (a) does the runbook reference the correct services per CMDB? (b) are referenced endpoints/DBs current (not deprecated)? (c) is the runbook scope consistent with the CR scope? It does NOT check: generic runbook completeness, formatting standards, or comparison against a team's runbook library.

**Rationale:** "Is this runbook valid for this change" is a cross-artifact reasoning task — it requires CMDB state, CR scope, and runbook content. "Is this runbook generally good" is a standalone quality problem. Keeping the boundary prevents #8 and #19b from diverging implementations of the same quality checks. #8 owns standalone quality; #19b owns change-context validity.

**Decided by:** Planner (scope management)

**Revisit trigger:** If building #8 Runbook Gap Analyzer later, reconcile shared utility functions (e.g., service-reference extraction) into a shared library.

---

### 2026-06-03 — PR Diff: Optional Context, Not Analyzed

**Context:** The ITSM CR may include a PR/diff reference. Should the agent analyze the diff for risk signals?

**Options considered:**
1. Full diff analysis with AST parsing — duplicates #4.5 Change Window Risk / #19 Code Review scope
2. Scope flags only (schema_migration, customer_facing_api, test_coverage_change) — lightweight metadata for risk context without code analysis
3. Ignore PR entirely — miss obvious risk signals

**Decision:** Option 2 — accept optional scope flags from CI/CD metadata. No diff parsing. Flags are boolean signals that inform runbook/rollback validation (e.g., schema_migration: true → check if rollback plan addresses irreversible migration).

**Rationale:** The ITSM analyzer is about operational readiness, not code quality. #4.5 and #19 own code-level analysis. But ignoring code context entirely is naive — a schema migration flag tells the runbook validation stage "check for irreversibility." Scope flags are the minimal interface between code analysis and operational analysis.

**Decided by:** Planner (scope management)

**Revisit trigger:** If #4.5 and #19b are both active, define a shared CR metadata schema so code analysis feeds into operational analysis automatically.

---

### 2026-06-03 — P0/P2 Eval Gate: Synthetic vs Real

**Context:** There are ZERO public ITSM change records with outcomes. All P0 evaluation uses synthetic data. How do we separate what synthetic eval proves from what it doesn't?

**Options considered:**
1. Synthetic eval proves production readiness — overconfident, real CRs have noise and ambiguity that synthetic data can't model
2. Synthetic eval proves nothing — too pessimistic, well-designed synthetic data with injected failures validates architectural soundness
3. Synthetic eval proves pipeline correctness and per-dimension recall; real eval proves domain calibration and false positive rates

**Decision:** Option 3 — dual-gate with explicit claims.

P0 synthetic gate proves:
- Pipeline processes all artifact types end-to-end
- Per-dimension recall >= 80% on injected failures
- Cross-CR conflict detection works on known overlaps
- Output schema is valid and complete
- Cost and latency targets met

P0 DOES NOT prove:
- False positive rate on real CRs (synthetic CRs may be too clean when not injected)
- Calibration of severity levels (real CRs have ambiguous severity)
- CAB recommendation accuracy on real decisions (CAB context includes politics, team trust, etc.)

P2 real gate (with enterprise partner) proves:
- False positive rate on real CRs <= 15%
- CAB recommendation aligns with expert assessment >= 75%
- Findings are actionable (CAB actually uses them to change decisions)

**Rationale:** Overclaiming on synthetic eval is the biggest integrity risk. The dual-gate makes the experiment's claims falsifiable at each stage. P0 is architectural validation; P2 is domain validation. Both are necessary, neither is sufficient alone.

**Decided by:** Planner (principled — from clinical-coding's P0/P2 split precedent)

**Revisit trigger:** If an enterprise partner provides real CRs earlier than expected, merge P0 and P2 eval on the real data rather than running synthetic eval first.

---

### 2026-06-03 — Vendor-Neutral CR Schema (No ServiceNow Dependency)

**Context:** Most enterprise ITSM runs on ServiceNow, but some use BMC Remedy, Jira Service Management, Freshservice, or custom systems. Should the experiment target ServiceNow's API schema?

**Options considered:**
1. ServiceNow-native schema — immediate production relevance but locks out 30-40% of the market
2. Vendor-neutral JSON schema — requires mapping per vendor but validates architecture independently of ITSM platform
3. Abstract schema with ServiceNow adapter — vendor-neutral core with one concrete adapter

**Decision:** Vendor-neutral JSON schema. No ITSM API dependency at P0. Synthetic data generator produces the neutral schema directly.

**Rationale:** The experiment validates an analysis pattern, not an integration. Coupling to ServiceNow's API at P0 would mean: (a) we need a ServiceNow instance to run evals, (b) schema changes with ServiceNow releases break the pipeline, (c) the architecture looks platform-specific when presenting to non-ServiceNow enterprises. Adapter layer is P2 scope — when a real integration partner exists.

**Decided by:** Planner (principled — experiment scope)

**Revisit trigger:** If a ServiceNow partnership materializes for P2, build the adapter and validate that vendor-neutral schema doesn't lose critical ServiceNow-specific fields (e.g., workflow state transitions).

---

### 2026-06-03 — #4.5 Change Window Risk Boundary

**Context:** Exploration #4.5 "Change Window Risk Analyzer" (23/25) analyzes scheduling overlaps and code-level conflicts for changes in the same deployment window. The ITSM CR Analyzer also checks scheduling. Scope overlap.

**Options considered:**
1. ITSM CR Analyzer handles all scheduling — subsumes #4.5's scheduling scope
2. ITSM handles scheduling completeness and SLA impact; #4.5 handles code-level dependency conflicts and CI/CD coordination
3. No scheduling in ITSM — just check maintenance window existence

**Decision:** Option 2 — ITSM owns operational scheduling, #4.5 owns technical dependency analysis.

ITSM checks:
- Are two CRs scheduled in overlapping windows on shared infrastructure? (from CMDB + schedule data)
- Does combined downtime exceed SLA budget? (from SLA definitions + CR durations)
- Are dependencies between co-scheduled CRs ordered correctly? (from CMDB service graph)

#4.5 checks:
- Do code changes in co-scheduled CRs conflict at API/schema level? (requires diff analysis)
- Are CI/CD pipelines coordinated for rollback sequencing? (requires deployment config)

**Rationale:** Scheduling conflict detection from structured data (CMDB, schedule, SLA) is an operational-artifact reasoning task — ITSM scope. Code-level dependency analysis requires reading diffs and understanding API contracts — #4.5 scope. The boundary is clear: ITSM reads operational artifacts, #4.5 reads code artifacts.

**Decided by:** Planner (scope management)

**Revisit trigger:** If both experiments are active, define a shared "CAB window context" JSON that ITSM produces and #4.5 consumes.

---

### 2026-06-03 — Schedule + SLA Stage Merge

**Context:** The pipeline could have separate stages for schedule conflict detection and SLA impact calculation, or merge them into one stage.

**Options considered:**
1. Separate Schedule and SLA stages — independent metrics, cleaner evolution
2. Merged Schedule + SLA stage — both consume the same inputs (CR schedule, CMDB, SLA defs) and produce related outputs
3. SLA as a post-processing step after all other stages

**Decision:** Merge into a single "Schedule + SLA" stage.

**Rationale:** Schedule conflicts and SLA impact share the same inputs (maintenance window schedule + CMDB service graph + SLA definitions) and are computed together: "these two CRs overlap on shared infra" (schedule conflict) → "combined downtime exceeds monthly budget by 15 minutes" (SLA impact). Splitting them creates artificial I/O boundaries for data that's already in memory. Per-stage metrics still measurable: schedule-conflict recall and SLA-calculation accuracy are independent metrics within the same stage.

**Decided by:** Planner (pipeline efficiency)

**Revisit trigger:** If SLA calculation becomes significantly more complex (e.g., multi-tier cascade analysis), split it out to manage stage complexity.

---

### 2026-06-03 — 9-Stage Pipeline Architecture

**Context:** The ITSM CR analysis decomposes into multiple reasoning steps. Following clinical-coding's 8-stage pattern, the pipeline needs granularity for per-stage eval without over-decomposition.

**Options considered:**
1. 9 stages: Ingest, Normalize, Completeness Check, Runbook Validation, Rollback Feasibility, Schedule & SLA Analysis, Dependency Chain, Historical Pattern, Risk Synthesis & CAB Report — each independently evaluatable
2. 7 stages: merge Runbook + Rollback, merge Schedule + Dependency — simpler but loses independent measurement of distinct reasoning tasks
3. 5 stages: Ingest, Analyze, Cross-CR, Report, Aggregate — monolithic analysis stage bundles four distinct reasoning types

**Decision:** 9 stages.

1. **Ingest** — parse CR bundle artifacts into structured format
2. **Normalize** — vendor-neutral schema alignment, field validation, deduplication
3. **Completeness Check** — ITIL-required artifact checklist per change type; customer-facing scope → comms plan required
4. **Runbook Validation** — check runbook procedures against current CMDB state (deprecated services, missing endpoints, stale references)
5. **Rollback Feasibility** — evaluate rollback plan feasibility (irreversibility detection, duration estimation, dependency chain)
6. **Schedule & SLA Analysis** — detect scheduling overlaps across CRs, calculate cumulative SLA impact per service tier
7. **Dependency Chain** — trace CMDB service graph to identify downstream impact and ordering violations
8. **Historical Pattern** — match CR against past incidents by (service, change-type) to surface risk patterns
9. **Risk Synthesis & CAB Report** — synthesize per-dimension findings into per-CR risk assessment and CAB window summary (approve/conditional/reject, disposition breakdown, aggregate SLA impact)

**Rationale:** Each stage targets a distinct reasoning dimension with independently measurable metrics. Runbook validation and rollback assessment are conceptually different tasks (procedural correctness vs. feasibility analysis) even though both read from the CR bundle. The 9-stage count matches clinical-coding's granularity level (8 stages + the clinical-specific CC/MCC stage).

**Decided by:** Planner (architecture design)

**Revisit trigger:** If Runbook Validation and Rollback Feasibility have >80% shared logic during implementation, merge them.

---

### 2026-06-03 — Synthetic-First P0 Evaluation Strategy

**Context:** There are ZERO publicly available ITSM change request datasets with annotated outcomes. Enterprise ITSM data (ServiceNow exports, CMDB snapshots) is proprietary and sensitive. The experiment needs evaluation data from day one.

**Options considered:**
1. Wait for enterprise partner to provide real data — blocks all development until partnership materializes
2. Use sanitized ITIL case studies as primary eval data — too few, too clean, no failure annotations
3. Build a synthetic CR generator producing full CR bundles (ITSM record + runbook + rollback plan + CMDB snapshot + SLA defs + schedule + incident history) with injectable failure types
4. Use LLM to generate realistic CRs — expensive, non-deterministic, hard to control failure injection precisely

**Decision:** Option 3 — synthetic CR generator with 8 injectable failure types as definitional ground truth.

Failure types:
1. Stale runbook reference (service deprecated in CMDB)
2. Infeasible rollback (irreversible migration with "restore backup" plan)
3. Scheduling overlap on shared infrastructure
4. SLA budget exceeded by combined maintenance windows
5. Missing dependency ordering (downstream change scheduled before upstream)
6. Incomplete communication plan for customer-facing service change
7. Historical incident pattern match (same service + change type → past P1)
8. Missing mandatory CR fields (no rollback plan, no runbook, no risk assessment)

Volume: 20+ CRs per failure type, 160+ total. Clean CRs: 40+ unmodified baseline for false-positive measurement.

**Rationale:** Synthetic data with injected failures provides definitional ground truth — we know exactly which failures exist because we put them there. This parallels clinical-coding's miscode injection approach. The generator is the foundation of all P0 evaluation. ITIL templates provide structural realism; injected failures provide measurable recall targets.

**Decided by:** Planner (principled — from clinical-coding precedent)

**Revisit trigger:** If synthetic CRs are too "clean" even without injection (real CRs are noisier — ambiguous descriptions, incomplete fields), add a noise injection layer to the generator.

---

### 2026-06-03 — Experiment Selection: #19b ITSM Change Request over Other B2B Candidates

**Context:** B2B exploration produced 15 STRONG/STRONGEST use cases ranked by agent fit and Eval Readiness. Top B2B candidates: #4.5 Change Window Risk (23/25, ★★★★), #15 Vendor Risk Scoring (21/25, ★★★★), #19b ITSM Change Request (23/25, ★★★). ITSM has lower Eval Readiness (★★★ vs ★★★★) but highest domain complexity.

**Options considered:**
1. #4.5 Change Window Risk — higher Eval Readiness, code-level analysis, but overlaps with existing code-review tooling
2. #15 Vendor Risk Scoring — regulatory angle, rich external data, but narrow domain (vendor management only)
3. #19b ITSM Change Request — highest cross-artifact complexity (8 artifact types), strong ITIL template basis for synthetic data, complementary to clinical-coding (healthcare vs enterprise), broadest enterprise applicability

**Decision:** #19b ITSM Change Request Analyzer

**Rationale:** The research goal is demonstrating where long-running agents provide irreplaceable value in enterprise operations. ITSM change analysis requires cross-artifact reasoning across 8 distinct data types — this stress-tests the overnight agent pattern in enterprise context. Lower Eval Readiness (★★★) is mitigated by ITIL's well-defined templates which make synthetic data generation highly composable. This experiment complements clinical-coding: healthcare validates the pattern on regulated/clinical data; ITSM validates it on operational/infrastructure data. Together they demonstrate domain-agnostic overnight agent architecture.

**Decided by:** Planner + evaluator plan review

**Revisit trigger:** If synthetic data generation proves insufficient to validate the architecture (generator can't produce realistic cross-CR interactions), fall back to #4.5 which has higher Eval Readiness and simpler data requirements.

---

<!-- Copy the template above for each new decision. Keep newest at top. -->
