# ITSM Change Request Analyzer — Use Case

## Problem Statement

Enterprise change management lives in ServiceNow/ITSM, not in Git. A "change request" (CR) bundles code changes with operational artifacts: runbook, rollback plan, maintenance window, approvals, cross-change dependencies, SLA impact analysis, and customer communication plans. The **Change Advisory Board (CAB)** reviews all pending CRs before deployment.

The bottleneck: CABs review **20–50 changes per week**. Members have **2 minutes per change**. Nobody reads the runbook. Nobody cross-references the rollback plan against current system state. Nobody checks whether two changes scheduled in the same window conflict on shared infrastructure. The result:

- **Rubber-stamping:** 80%+ of CRs are approved without substantive review
- **Missed conflicts:** Scheduling overlaps and dependency inversions surface as incidents, not during review
- **Stale artifacts:** Runbooks reference deprecated services; rollback plans assume pre-migration state
- **SLA surprises:** Combined downtime of co-scheduled changes exceeds monthly SLA budget — discovered after the fact
- **Reactive learning:** Failed changes trigger post-incident reviews, but systemic operational gaps (e.g., all CRs from team X lack monitoring plans) go undetected

An overnight agent that analyzes **ALL pending CRs** for the next CAB window — cross-referencing ITSM records, runbooks, rollback plans, CMDB state, SLA definitions, scheduling, and historical incident data — would transform the CAB from a rubber-stamp meeting into a risk-informed decision point.

## Why an Agent (Not Script/ML)

| Dimension | Score (1-5) | Justification |
|-----------|-------------|---------------|
| Heterogeneous reasoning | 5 | ITSM ticket prose + runbook procedures + rollback plans + CMDB service graph + SLA definitions + scheduling calendar + incident history + optional PR metadata — eight distinct artifact types requiring cross-source reasoning |
| Open-ended analysis | 5 | Each CR is a unique bundle of artifacts with varying completeness and quality; "is this change safe?" requires reasoning about the specific combination, not applying a fixed checklist |
| Explanation needs | 5 | "Reject because rollback plan assumes pre-migration state but change includes schema migration" IS the deliverable — CAB members need the reasoning chain, not a risk score |
| Novelty | 4 | Change types recur (standard/normal/emergency) and ITIL templates create structural similarity; novelty comes from the unique artifact combination and cross-CR interactions per window |
| Context evolution | 4 | CMDB topology, SLA definitions, team practices, and runbook quality evolve continuously; historical incident patterns shift as infrastructure changes |

**Agent Score:** 23/25
**Real Data:** 3/5 (★★★) — BPI Challenge 2014 has real ITIL change records from Rabobank; UCI has 24K ServiceNow incidents; EnterpriseOps-Gym provides ITSM agent benchmark. No public data includes runbooks/rollback plans — prose artifact validation still requires enterprise partner. **Updated from exploration's 2/5** after dataset research (see DECISIONS.md).
**Synth Ease:** 4/5 (ITIL templates + injectable failures make synthetic CRs highly composable)
**Total:** 26/35 (updated: Real Data 3/5 instead of 2/5)
**Verdict:** **STRONGEST** — scored as #19b in B2B enterprise use-case exploration (original exploration score 25/35 predates dataset research; see [DECISIONS.md](DECISIONS.md) "Real Dataset Research" entry for upgrade rationale)
**Eval Readiness:** ★★★½ — Strong synth GT + partial real data (BPI 2014 structured fields, UCI incidents). Full prose-artifact validation (runbooks, rollback plans) still requires enterprise partner.

The key reason this is an agent task: a CR is a **bundle of heterogeneous artifacts** (ticket prose + runbook + rollback plan + CMDB state + schedule + SLA context). Assessing "is this change safe?" requires reasoning across all of them simultaneously. Scripts check individual fields ("does it have a rollback plan? yes/no"). The agent assesses "is this rollback plan ACTUALLY FEASIBLE given the current system state and the other changes in this window?"

**Wedge vs field validators:** ServiceNow workflow validators check field presence and approval chains. The agent assesses **semantic validity** — whether the runbook matches current infrastructure, whether the rollback is feasible given the change scope, whether scheduling is sound across multiple CRs. This is the gap between "form complete" and "plan sound."

**Distinction from #19 Code Review / PR Risk:** Code review analyzes the **diff** for architectural violations and regression risk. The ITSM analyzer assesses the **operational lifecycle** — runbook quality, rollback feasibility, scheduling, communication, CAB readiness. They are complementary: #19 asks "will this code break things?" and #19b asks "is the operational plan for deploying this code sound?"

## Target User

- **Persona:** Change Management Director or CAB Chair at a mid-to-large enterprise (100+ IT staff, ITIL-aligned). Reports to CIO or VP Infrastructure. Responsible for change success rate, incident reduction from changes, and CAB efficiency.
- **Workflow:** Teams submit CRs throughout the week via ServiceNow/ITSM. Agent runs overnight on ALL pending CRs for the next CAB window. CAB chair arrives at the meeting with a pre-analyzed risk brief: each CR annotated with completeness gaps, runbook issues, scheduling conflicts, SLA exposure, and a recommended disposition (approve/conditional/reject). CAB focuses discussion on conditional and flagged CRs instead of reviewing 40 tickets cold.
- **Output format:** Per-CR risk assessment (structured JSON + rendered Markdown) with dimension-level findings and aggregate CAB window summary. Markdown report for human consumption; JSON for ITSM integration (future).

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|-------------|
| Per-dimension recall | >= 80% | Against synthetic injected failure test set (8 failure types, 160+ CRs) |
| Clean-CR false positive rate | <= 15% | Proportion of approve-path CRs incorrectly flagged on unmodified synthetic CRs |
| CAB rollup self-consistency (P0) | >= 75% | 3-class accuracy (approve/conditional/reject) against synthetic GT labels. Renamed to "CAB recommendation accuracy" at P2 when measured against expert assessment. |
| Schema compliance | >= 99% | Output JSON validates against defined schema on all input CRs |
| Cost per batch | < $2 | Token counting + compute for a 50-CR CAB window at L1 (script-only MVP) |
| Wall-clock per batch | < 30 min | Pipeline timer for 50-CR window (enables pre-CAB overnight processing) |
| Cross-run consistency | >= 95% | Same input → same output across 3 repeated runs (determinism at L1) |

## Input / Output

**Input:**
- CR bundle per change request:
  - ITSM record (vendor-neutral JSON: change ID, type [standard/normal/emergency], risk category, title, description, affected services, requestor, approvers, scheduled window)
  - Runbook (Markdown: numbered procedural steps referencing services, endpoints, commands)
  - Rollback plan (Markdown: steps to revert, assumptions, estimated duration)
  - CMDB snapshot (JSON: service graph with nodes [services, DBs, APIs] and edges [depends-on], versioned state including deprecated/active status)
  - SLA definitions (JSON: per-service-tier monthly downtime budget in minutes, current month consumption)
  - Maintenance window schedule (JSON: all CRs in the same CAB window with their scheduled times and expected durations)
  - Communication plan (Markdown, optional: customer notification, internal stakeholder alerts)
  - Historical incident index (JSON: past incidents linked to service + change category, with outcome)
  - PR/diff summary (optional, scope flags only: `schema_migration: true`, `customer_facing_api: true` — no full diff analysis, that's #4.5's job)
- Typical size per CR: 10K-50K characters / 3K-12K tokens
- Example: "Normal change to payment-service: schema migration adding `currency` column, runbook references `payment-db-v3` (deprecated last month, now `payment-db-v4`), rollback plan says 'restore DB backup' but migration is irreversible, scheduled in same window as auth-service config change that depends on payment-service being up"

**Output:**
- Per-CR risk assessment (JSON + Markdown, ~2-5KB per CR):
  - Risk summary: CR ID, overall risk level, CAB recommendation (approve/conditional/reject)
  - Per-dimension findings: runbook validity, rollback feasibility, scheduling conflicts, SLA impact, dependency chain issues, completeness gaps, communication gaps, historical pattern alerts
  - Each finding with: severity (blocker/warning/info), evidence (which artifact, which line/field), remediation suggestion
- CAB window summary (JSON + Markdown, ~1-2KB):
  - Total CRs analyzed, disposition breakdown (approve/conditional/reject), cross-CR conflicts, aggregate SLA impact, processing time, cost

## Constraints

| Dimension | Constraint | Rationale |
|-----------|-----------|-----------|
| Data | BPI Challenge 2014 primary for P0/P1 structured-field eval; synthetic fixtures for regression | [BPI Challenge 2014](https://data.4tu.nl/datasets/e9c00fe9-c87a-450e-8bd6-d5e06a6b309a) (Rabobank) provides real ITIL change records — primary dataset per `.harness/spike-bpi2014.md`. `fixtures/cab-window-01/` is full-bundle synthetic regression. No public dataset includes runbooks/rollback plans; enterprise partner required for P2 prose-artifact validation |
| ITSM dependency | No production ServiceNow/BMC dependency at P0 | Vendor-neutral JSON schema. No API integration in experiment scope — validates architecture on synthetic fixtures |
| Time | < 30 min for 50-CR batch | CAB windows are weekly; overnight processing must complete before morning meeting |
| Cost | < $2 per 50-CR batch at L1 | At script-only MVP, cost is ~$0. Budget matters at L2+ when NLP/LLM enter |
| Accuracy | >= 80% per-dimension recall on injected failures | Below this, CAB chairs spend more time verifying agent findings than they save |
| Privacy | No production ITSM data in experiment | Synthetic data only. Production deployment would require enterprise data handling agreements |
| Determinism | >= 95% cross-run consistency at L1 | CAB decisions require reproducible risk assessments |
| Scope | Operational lifecycle only; no code-level semantic analysis | Code conflict detection is #4.5 Change Window Risk's job. PR/diff is scope-context only |
