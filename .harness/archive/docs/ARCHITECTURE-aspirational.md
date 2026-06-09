# ARCHITECTURE.md — Aspirational Content Archive

> Archived from ARCHITECTURE.md during restructure. Contains content for features not yet implemented, datasets not yet integrated, and operational processes not yet in place. Preserved for reference when advancing to P1/P2.

---

## Synthetic CR Generator Design

**Taxonomy:**
- Change types: standard (pre-approved, low risk), normal (CAB review required), emergency (expedited approval)
- Risk categories: low, medium, high (mapped to ITIL risk matrix)
- CAB window groupings: 5-15 CRs per window for scheduling/dependency eval

**Artifact templates:**
- ITSM record: ServiceNow-shaped JSON with vendor-neutral field names
- Runbook: Markdown with numbered procedural steps, service references, command examples
- Rollback plan: Markdown with revert procedure, assumptions, duration estimate
- CMDB snapshot: JSON service graph (nodes + edges) with versioned state
- SLA definitions: JSON per-tier monthly downtime budgets
- Maintenance schedule: JSON array of all CRs in the CAB window
- Communication plan: Markdown stub (customer notification, stakeholder alerts)
- Incident history: JSON index of past incidents by service + change category
- PR scope flags: optional JSON with boolean scope signals

**Injection taxonomy (8 failure types = definitional GT):**

| # | Failure type | What's injected | Which stage detects it |
|---|-------------|-----------------|----------------------|
| 1 | Stale runbook reference | Runbook references service marked `deprecated` in CMDB | Runbook Validation |
| 2 | Infeasible rollback | "Restore backup" plan for irreversible schema migration | Rollback Feasibility |
| 3 | Scheduling overlap | Two CRs on shared infrastructure with overlapping windows | Schedule & SLA Analysis |
| 4 | SLA budget exceeded | Combined maintenance exceeds monthly downtime budget | Schedule & SLA Analysis |
| 5 | Missing dependency ordering | Downstream CR scheduled before upstream prerequisite | Dependency Chain |
| 6 | Incomplete communication | Customer-facing scope change with no communication plan | Completeness Check (via Normalize tier flag) |
| 7 | Historical incident pattern | Same (service, change_category) tuple with ≥2 past P1/P2 incidents | Historical Pattern |
| 8 | Missing mandatory fields | No rollback plan, no runbook, or no risk assessment | Completeness Check |

**Volume targets:**
- 20+ CRs per single-failure type = 160+ single-injection CRs
- Multi-label CRs (2-3 simultaneous failures): 30+ for E2E difficulty
- 30+ multi-CR CAB windows for stages 6-7 (cross-CR evaluation)
- 40+ clean CRs (unmodified baseline) for false-positive measurement
- Total: 200+ CRs, generator seeds reproducible (fixed seed) for regression

**Realism knobs (decisions deferred to skeptic review):**
- Prose messiness: template-clean ITIL vs enterprise-sloppy (abbreviations, incomplete sentences)
- Noise injection: randomly omit optional fields, add irrelevant ITSM boilerplate
- CMDB staleness: percentage of services with version drift between runbook authoring and current snapshot

---

## P1 Datasets (Not Yet Integrated)

| Source | Type | Size | Access | GT Alignment | Pipeline stages | What it covers / gaps |
|--------|------|------|--------|:------------:|-----------------|----------------------|
| [UCI Incident Management](https://archive.ics.uci.edu/dataset/498/) | **Real** | 24,918 incidents, 141,712 events, 36 attributes | Free (UCI ML Repository) | Partial | Historical Pattern | ServiceNow-extracted incident process log. No change records directly, but incident history index can be validated against real incident patterns. Anonymized. |
| [ServiceNow-AI/EnterpriseOps-Gym](https://huggingface.co/datasets/ServiceNow-AI/EnterpriseOps-Gym) | Synthetic benchmark | 181 ITSM tasks, containerized MCP servers | Free (HuggingFace) | Partial | Pipeline-wide (agentic benchmark) | ServiceNow's official enterprise ops benchmark (2026). Evaluates LLM agents on multi-step ITSM planning with SQL verifiers. Different eval paradigm (action-based, not finding-based) but useful for agent-comparison baselines. |
| [ServiceNow-itsm-safety-bench](https://github.com/ServiceNow/ServiceNow-itsm-safety-bench) | Seed data | 10 change_requests, 50 incidents, 20 CMDB items | Free (GitHub) | Partial | Ingest, Normalize, Completeness Check | ServiceNow instance snapshot with change + CMDB structure. Small but validates vendor-neutral schema mapping. Safety-focused scenarios (approval bypass, record manipulation). |
| [VuduVations/itsm-change-management-benchmark](https://huggingface.co/datasets/VuduVations/itsm-change-management-benchmark) | Synthetic benchmark | 15 incidents, 68 CMDB items, 3 CAB scenarios | Free (HuggingFace) | Partial | Risk Synthesis & CAB Report | First public change management benchmark for CAB evaluation. Small but includes RFC→CAB scoring pipeline. Useful for Report stage comparison. |
| ArXiv 2604.13462 (international bank, 2026) | Paper (proprietary data) | 175K change tickets (1 year) + incident linkage | Paper only (data not public) | None | — | Predictive incident risk scoring for change management using ML. Validates that our problem formulation matches real enterprise practice. **Reference architecture**, not usable data. |

---

## Reference Sources

| Source | Type | Access | GT Alignment | Purpose |
|--------|------|--------|:------------:|---------|
| [Scoutflo SRE Playbooks](https://github.com/Scoutflo/Scoutflo-SRE-Playbooks) | Real runbooks | Free (GitHub, 414 playbooks: 232 K8s + 157 AWS + 25 Sentry) | None | **Runbook prose templates.** Real operational runbooks with procedural steps, service references, diagnostic commands. Not paired with change requests, but provides realistic prose structure for generator templates instead of invented runbook text. |
| [KubePlaybook](https://github.com/K8sPlayBook/KubePlaybook) (IBM Research) | Real playbooks | Free (GitHub, 130 Ansible playbooks + NL prompts) | None | Ansible remediation playbooks with natural language descriptions. Useful for rollback plan prose patterns — Ansible playbook structure mirrors rollback procedural steps. |
| ITIL certification case studies | Reference | Proprietary (sanitized) | None | Format reference for generator template diversity. NOT evaluation GT — too clean and idealized. |
| ServiceNow community examples | Reference | Public (community forums) | None | Field naming conventions and workflow structure for realistic CR templates. Input diversity only. |
| BMC Remedy documentation | Reference | Public (vendor docs) | None | Alternative ITSM field patterns for vendor-neutral schema validation. |

---

## Ground Truth Strategy (P0/P1/P2)

**P0 — Synthetic injection (no annotation cost, primary eval path):**
1. Generate 200+ CR bundles with ITIL-template generator → inject 8 failure types (20+ each) → run pipeline → measure per-dimension recall/precision.
2. Run Schedule & SLA Analysis and Dependency Chain on multi-CR CAB windows → measure cross-CR conflict detection accuracy.
3. Run full pipeline on 40+ clean CRs → measure false-positive rate per dimension.
4. CAB rollup self-consistency: 3-class accuracy (approve/conditional/reject) against GT labels derived from injected severity rules.

**P1 — Real data + format diversity (low cost, parser robustness + partial validation):**
5. Run Ingest + Normalize against **BPI Challenge 2014** change records (Rabobank) — test structured-field parsing on real ITIL change data from HP Service Manager. No runbooks but validates field extraction and change→incident linkage.
6. Run Historical Pattern against **UCI Incident Management** event log (24K incidents) — validate incident pattern matching on real ServiceNow data.
7. Compare pipeline structure against **EnterpriseOps-Gym** ITSM task definitions — benchmark agent approach against ServiceNow's official eval framework.
8. Use ITIL case studies and ServiceNow community examples as **input format variants** — test parser robustness on non-template CRs.
9. Vary prose messiness knobs in generator — measure per-dimension recall stability across messiness levels.

**P2 — Enterprise partner pilot (high cost, production-readiness):**
7. Enterprise partner provides historical CRs that led to incidents → compare agent pre-CAB assessment to known failure modes.
8. CAB chair evaluates 50+ agent reports → labels each finding as TP/FP/missed → builds labeled eval set for real-path validation.
9. False-positive rate on real CRs and CAB recommendation alignment with expert judgment.

**Proxy metrics (when no GT):** Output count consistency across runs (<10% variance), field presence rate on Normalize output (>99% for required fields), template coverage for Report (% finding types with generated narrative).

---

## Ground Truth Coverage by Stage

| Stage | Direct GT? | Best source | Gap |
|-------|:----------:|-------------|-----|
| Ingest | No | Synthetic CRs (structure consistency) | No parsing GT. Evaluate via downstream stage quality — if Normalize fails, Ingest may be the bottleneck. |
| Normalize | No | — | No GT. Evaluate via cross-CR output consistency (field presence rate >99% on required fields). |
| Completeness Check | **Direct** | Injected failures #6 (missing comms) + #8 (missing fields) | Completeness rules are deterministic against ITIL checklist. GT = which fields are missing. |
| Runbook Validation | **Direct** | Injected failure #1 (stale runbook reference) | CMDB-vs-runbook cross-reference. GT = which runbook steps reference deprecated/missing services. |
| Rollback Feasibility | **Direct** | Injected failure #2 (infeasible rollback) | Rollback plan vs change scope. GT = whether rollback actions are feasible given the change type. |
| Schedule & SLA Analysis | **Direct** | Injected failures #3 (overlap) + #4 (SLA exceeded) | Cross-CR scheduling data. GT = which CR pairs overlap and cumulative downtime calculation. |
| Dependency Chain | **Direct** | Injected failure #5 (dependency inversion) | CMDB service graph + schedule ordering. GT = prerequisite violations in CR ordering. |
| Historical Pattern | **Direct** | Injected failure #7 (incident pattern match) | Incident history index. GT = which (service, change_category) tuples have high prior failure rates. |
| Risk Synthesis & CAB Report | Partial | Derived from per-stage GT labels | CAB recommendation GT = deterministic rule from per-dimension severity rollup. Evidence citation quality has no GT — proxy via completeness count. |

---

## Synthetic Concentration Risk

The primary dataset (Synthetic CR Generator) introduces systemic bias:

| Risk | Detail |
|------|--------|
| **ITIL-template clean** | Generated CRs follow ITIL best-practice templates. Real enterprise CRs have abbreviations, incomplete sentences, copy-paste artifacts, and organizational jargon. Agent performance on synthetic data is an **upper bound**. |
| **English-only** | No multi-language support. Enterprises with global operations may have CRs in local languages. |
| **Idealized CMDB** | Synthetic CMDB snapshots have complete service graphs. Real CMDBs have stale entries, missing edges, inconsistent naming, and manual-update lag. |
| **No organizational politics** | Real CAB decisions factor in team trust, deployment freeze windows, executive overrides, and historical relationships. Synthetic CRs can't model these soft factors. |
| **Uniform artifact quality** | All generated runbooks have consistent formatting. Real runbooks vary from detailed step-by-step to single-paragraph hand-waves. |

**Mitigations (ranked by feasibility):**

| Priority | Approach | What it adds |
|----------|---------|-------------|
| P0 | **Noise injection in generator** | Randomly degrade runbook quality (remove steps, abbreviate services), add ITSM boilerplate, omit optional fields. Partial realism within template structure. |
| P1 | **BPI 2014 + UCI real data** | Real change records (Rabobank) and incident logs (ServiceNow). Tests structured-field pipeline on real ITIL data. No runbooks but validates Ingest/Normalize/Historical Pattern on non-synthetic inputs. |
| P1 | **EnterpriseOps-Gym + safety-bench** | ServiceNow's official benchmarks for agent comparison and schema validation. Different eval paradigm but provides competitive baseline. |
| P1 | **Format diversity from reference sources** | Use ITIL/ServiceNow/BMC examples to create non-template CR variants. Tests parser robustness beyond template-clean inputs. |
| P2 | **Enterprise partner data** | Real CRs with real outcomes including runbooks and rollback plans. The only way to measure FP rates on prose artifacts, calibrate severity levels, and validate organizational context. |

---

## Inter-stage Validation (Not Implemented)

Script-level pre-checks between producer and consumer stages. Free deterministic gates that catch silent degradation before it propagates downstream.

| Checkpoint | Producer → Consumer | What it checks | Failure action |
|-----------|---------------------|----------------|---------------|
| Normalize completeness | Normalize → Completeness Check | All required ITSM fields present (change_id, type, risk_category, scheduled_window, affected_services). CMDB snapshot has ≥1 service node. SLA definitions cover all affected service tiers. | Fail pipeline with diagnostic: "Normalize output missing required fields: [list]. Check Ingest parser or input CR bundle." |
| Service ID resolution | Completeness Check → Runbook Validation | Service IDs referenced in ITSM record exist in CMDB snapshot. No orphan service references. | Warn (non-blocking): unresolvable service IDs logged. May indicate CMDB staleness or ITSM data entry error — useful signal, not a pipeline bug. |
| Downtime extraction | Rollback Feasibility → Schedule & SLA Analysis | Each CR has `expected_duration_min` (numeric, >0). Rollback estimated duration extracted. Schedule window start < end. | Fail pipeline: SLA arithmetic requires valid duration inputs. Cannot compute downtime budget without them. |
| Window calendar | Schedule & SLA Analysis → Dependency Chain | All CRs in CAB window have resolved schedule entries. No duplicate change_id in window. Service graph edges reference valid node IDs. | Fail pipeline: Dependency Chain requires valid graph. Duplicate IDs or orphan edges corrupt topological sort. |
| Finding handoff | All dimension stages → Risk Synthesis & CAB Report | Each stage output has ≥0 findings with required schema fields (dimension, severity, finding, evidence). No null severity values. | Warn with list of malformed findings. Report stage can render partial results but flags incomplete analysis. |

---

## Unimplemented Stage Evolution Levels

### Stage 4: Runbook Validation — L2/L3

| Level | Component | What it does | Performance | Cost | Trigger to advance |
|-------|-----------|-------------|-------------|------|-------------------|
| L2 (SOTA) | NLP encoder (service-reference extraction) | Named entity recognition for service references in procedural prose. Extracts implicit references ("deploy to the card processing gateway" → `payment-service`). Matches extracted entities against CMDB nodes with semantic similarity. | 2-5s per CR | ~$0.005/CR (local inference) | When encoder recall < 70% on injected stale refs AND residual errors require understanding runbook INTENT vs CMDB state (not just entity extraction) |
| L3 | LLM (semantic staleness detection) | LLM reads runbook + CMDB snapshot and reasons about whether the procedure is valid given current system state. Catches implicit staleness: "SSH to the bastion host" when bastion was replaced by VPN. | 5-15s per CR | ~$0.03-0.10/CR | — (ceiling, entered only on L2 trigger) |

### Stage 5: Rollback Feasibility — L2/L3

| Level | Component | What it does | Performance | Cost | Trigger to advance |
|-------|-----------|-------------|-------------|------|-------------------|
| L2 (SOTA) | NLP encoder (rollback action classification) | Classify rollback plan actions into categories: snapshot-restore, script-revert, manual-intervention, no-revert. Match action category against change type for feasibility assessment. Extract temporal references for duration estimation. | 2-5s per CR | ~$0.005/CR (local inference) | When encoder recall < 70% on injected infeasible rollbacks AND residual errors require temporal reasoning or cross-reference with change timeline |
| L3 | LLM (contextual feasibility reasoning) | LLM reads rollback plan + change description + timeline and reasons about feasibility: "rollback says revert Tuesday backup but change runs Thursday-Saturday — 3 days of data loss." Handles implicit infeasibility that requires understanding temporal context. | 5-15s per CR | ~$0.03-0.10/CR | — (ceiling, entered only on L2 trigger) |

---

## Review & Continuous Improvement (Not In Place)

### Review Types

| Review | Trigger | What it examines | Output |
|--------|---------|------------------|--------|
| **Per-stage regression** | After any stage implementation change | Run full synthetic test set → compare metrics before/after → flag regressions > 2% | Stage-level pass/fail with metric delta |
| **Cross-CR regression** | After stages 6-7 changes | Run multi-CR CAB window test cases → verify scheduling/dependency detection is stable | Window-level conflict detection accuracy |
| **Evolution advancement review** | Stage trigger condition met | Assess whether advancing to next level improves target metric by > 5% on held-out test set | Advancement report: metric lift, cost increase, go/no-go decision |
| **False positive audit** | Monthly or after FP rate > threshold | Manual review of top-10 false positive findings → categorize root cause (overly aggressive rule, CMDB noise, parser error) | FP categorization + rule refinement backlog |
| **Generator quality review** | After generator changes or before skeptic review | Assess whether synthetic CRs are realistic enough → compare artifact structure against ITIL references | Realism assessment + generator improvement backlog |

### Feedback Loops

| # | Loop | Source → Destination | What flows |
|---|------|---------------------|------------|
| 1 | Stage metric → Evolution trigger | Per-stage metric results → evolution level advancement decision | When recall drops below trigger threshold, advance to next level |
| 2 | FP audit → Rule refinement | False positive categorization → Completeness/Runbook/Rollback rule updates | Overly aggressive rules are relaxed; CMDB noise patterns are filtered |
| 3 | Generator review → Injection tuning | Realism assessment → generator noise/messiness knobs | If synthetic CRs are too clean, increase noise injection |
| 4 | Cross-CR regression → Window calibration | Window-level conflict detection → Schedule & SLA / Dependency Chain threshold tuning | Adjust overlap detection sensitivity based on false conflict rates |
| 5 | E2E recommendation audit → Severity weights | CAB recommendation accuracy analysis → severity rollup rule calibration | If too many CRs are "conditional" (>40%), relax warning-count threshold |
| 6 | P2 partner feedback → Generator grounding | Real CR structure/noise patterns → generator template updates | Real-world patterns inform next generation of synthetic data |

### Advancement Protocol

1. **Measure:** Run current level on full synthetic test set. Record per-stage metrics.
2. **Trigger check:** Does the trigger condition for advancement fire? (e.g., recall < threshold)
3. **Implement:** Build the next level implementation alongside the current one (dual-path).
4. **A/B compare:** Run both levels on the same test set. Next level must show > 5% metric improvement.
5. **Cost check:** Verify cost increase stays within pipeline budget gate.
6. **Promote:** Set new level as default. Keep previous level as fallback.
7. **Regression gate:** Run full regression suite. No metric on any other stage may regress > 2%.

---

## P0 Success Gates vs P2 Commercial Claims

**P0 success gates (synthetic eval — proves architecture works):**
- Per-dimension recall >= 80% on injected failures (template-clean tier)
- **Noise-tier gate (skeptic B3 fix):** Per-dimension recall >= 65% on medium-noise subset (prose-embedded refs, CMDB alias drift, missing optional fields). If recall variance > 15% between template-clean and medium-noise, the synthetic realism ceiling is low — document limitation and prioritize P2.
- **Prose-embedded failure subset:** Runbook/Rollback stages must report recall separately on explicit-name vs prose-embedded injections. Prose-embedded recall may be lower at L1 — this is expected and quantifies the L2 advancement opportunity.
- Clean-CR FP <= 15%
- CAB rollup self-consistency >= 75% (renamed from "CAB recommendation accuracy" — see skeptic B3)
- Task completion 100%; schema compliance >= 99%; cost < $2 per 50-CR batch; wall-clock < 30 min
- **All P0 reports must carry banner:** "Evaluated on synthetic ITIL-template data with injected failures. Metrics are architectural validation, not production performance claims."

**P0 does NOT prove:** Real-CR FP rates, expert CAB alignment, finding actionability, or cross-artifact semantic reasoning beyond template-matching.

**P2 commercial-claims gate (enterprise partner):** Real-CR FP <= 15%; expert CAB alignment >= 75%; actionability >= 70%; >= 1 CAB decision changed per window.

### Metric Evolution (Projected)

| Metric | L1 (MVP) | L2 (NLP/encoder) | L3 (LLM) |
|--------|----------|-------------------|-----------|
| Per-dimension recall | Baseline (expected 50-80% on template-clean synthetic) | +10-20% from prose understanding | +5-10% from edge case reasoning |
| False positive rate | Low at L1 (rules are precise) | May increase (NLP over-extracts) | May increase (LLM hallucination risk) |
| Cost per batch | ~$0 | ~$0.10-0.30 | ~$0.50-1.50 |
| Wall-clock per batch | <5 min | 10-20 min | 20-30 min |
| Cross-run consistency | ~100% (deterministic) | 95-99% (model inference variance) | 85-95% (LLM sampling variance) |

---

## Resolved Known Unknowns

| # | Unknown | Resolution |
|---|---------|------------|
| 1 | **Synthetic realism ceiling** | Deferred to P2 — BPI 2014 real data used for P0 instead of synthetic-only. Metric sensitivity to noise not yet measured. |
| 2 | **ITIL-template bias** | Acknowledged as limitation. BPI 2014 provides non-template input for structured-field stages. Prose stages untested on non-ITIL input. |
| 3 | **Zero real-path validation until P2** | BPI 2014 provides partial real-path validation for structured stages. Prose stages (4/5) have no real data. |
| 5 | **Generator prose vs parser fragility** | Deferred — generator not yet built beyond cr-001 fixture. |
| 6 | **English/ITIL-only bias** | Accepted as P0 scope limitation. |
| 7 | **CAB workload assumptions** | Validated on BPI 2014: 50 windows, 1-21 CRs each. 11.6s for 373 CRs total. |
| 8 | **#4.5 integration timing** | Deferred — #4.5 experiment not started. `pr_scope_flags` reserved in schema. |
| 9 | **CMDB quality & rollback without code** | Stage 7 (Dependency Chain) not implemented. Rollback (stage 5) not implemented. |
