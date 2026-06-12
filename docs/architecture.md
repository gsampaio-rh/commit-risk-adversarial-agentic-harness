# Architecture — Commit Risk Investigation Harness

A closed-loop commit investigation system: a deterministic harness controls what the agent sees and how it runs; the LLM performs mechanism-level hypothesis generation; Script stages enforce risk policy; a six-dimension adversarial eval scores output against oracle; failures drive method iteration.

## 1. What This System Is

This is a **verifiable commit investigation system** — not an LLM wrapper, not a classifier, not a pipeline that calls GPT and parses JSON. Its core is an investigation harness (production) plus a separate evaluation framework and improvement cycle. The agent is one controlled subsystem inside the harness.

The thesis: harness engineering (routing, context assembly, Schema enforcement, cost governance) and a separate evaluation framework (six-dimension adversarial scoring against ground truth) together produce better investigations than raw model quality alone. ApacheJIT's ground truth chain — buggy commits linked to fixing commits linked to JIRA issues — enables evaluation dimensions that no score-only model can satisfy. A model can predict "buggy" but cannot prove it investigated the *mechanism*. This system can.

**Current phase:** V2 pipeline active. All pipeline stages are implemented. V1 delivery gate passed (n=50, all six GATE thresholds). V2 targets D1≥0.80 and D3≥0.35 — both currently below target (D1=0.72, D3=0.31 on latest n=50).

**Current results (V2, n=50 run 2 — 2026-06-12):** D1=0.72, D2_fix_chain=0.384, D3=0.31, D4=0.881, D5=0.387, D6=0.78, FP=24%. Root causes: D1 gap = hidden-fix-in-CS commits; D3 gap = prompt-engineering ceiling, JIRA context not injected.

## 2. Design Philosophy

### Adversarial verification

Evaluation is designed to *catch bad reasoning*, not confirm good accuracy. A D1=0.90 score is meaningless if D3=0.15 — the agent classified based on surface features ("big diff = risky") without understanding the actual bug mechanism. The six-dimension panel exists to expose exactly this pattern.

### Oracle isolation

The agent sees only commit-time context — what a human reviewer would have at the moment of review. Ground truth (buggy labels, fix commits, JIRA tickets) is reserved exclusively for evaluation. Proven critical: removing the leaked `buggy` label dropped D1 from 0.86 to 0.40 on identical commits.

### Script controls decisions; LLM generates evidence

The LLM does one focused thing: generate mechanism hypotheses with evidence quotes from the diff. Everything else is deterministic infrastructure:

- **Archetype detection** (`archetype.py`) — Script identifies clean-commit patterns and production defect signals
- **Evidence tiering** (`evidence_tagger.py`) — Script tags hypotheses SUPPORTED/SPECULATIVE/REFUTED/UNVERIFIABLE
- **Risk policy** (`risk_policy.py`) — Script pure function computes risk_level as the single source of truth
- **Quality gate** (`quality_gate.py`) — Script determines if follow-up is needed; no LLM self-report

The old pattern (LLM produces classification in one giant prompt; Script corrects it post-hoc) is replaced by: LLM produces hypotheses and evidence; Script derives classification deterministically.

### Grounding before guessing

Empty or boilerplate output is structurally rejected. The pipeline requires at least one hypothesis with an `evidence_quote` that is a substring of the diff. D6 (automated, zero LLM cost) checks whether the report cites real files and diff content. D6=0.833 on the iter-2 panel proves the agent grounds its work in actual artifacts.

### Harness provides infrastructure; pipeline stages produce investigation

The harness is deterministic infrastructure. The pipeline stages are the investigation process running inside.

| Responsibility | Harness / Script stages | Agent (LLM) |
|---------------|------------------------|-------------|
| Which commits to investigate | Routing | — |
| What context to provide | Context builder | — |
| Archetype classification | archetype.py | — |
| When to stop / follow-up | quality_gate.py | — |
| What format to output | Schema validation per stage | Fills HypothesisArtifact schema |
| Risk level computation | risk_policy.py | — |
| How much to spend | Budget enforcement | — |
| **Mechanism hypothesis generation** | — | **Core LLM value** |
| **Evidence quote extraction** | — | **Core LLM value** |

## 3. The Investigation Method

### 3.1 Intent

Investigation means: classify risk *and* articulate *what could break*, with diff-grounded evidence. The output must be actionable for a human reviewer — not "this is a large change" but "if `getAmazonAWSHost()` returns a value without a leading dot, the URL will be malformed."

### 3.2 Five-stage pipeline model

The investigation pipeline follows **five conceptual stages** (archetype → quality gate). Operational docs also use **Stage 0** (context assembly, harness) and **Assembly** (report merge) — seven nodes total in [agent-loop.md](agent-loop.md). The five stages below are the investigation *method* components:

1. **Archetype detection** — Script identifies if the commit's PRIMARY change is a known clean-commit pattern (version bump, label rename, type migration, comment-only, pure refactor), returns `"AMBIGUOUS"` when no clean pattern matches and `has_production_defect_signals()` is false, or has production defect signals (guard removal, lifecycle change, inverted condition).

2. **Hypothesis generation** — LLM generates 2–3 mechanistic candidates, each structured as "If ⟨condition⟩ then ⟨failure⟩ in ⟨location⟩" with an `evidence_quote` substring from the diff. The LLM prompt is narrow: produce mechanisms, not verdicts.

3. **Evidence tiering** — Script tags each hypothesis:
   - `SUPPORTED`: diff shows a concrete mechanism (removed guard, inverted condition, wrong default, lifecycle ordering change) at a call site
   - `SPECULATIVE`: relies on assumed external behavior (cross-version API breakage, theoretical caller impact) not shown in the diff
   - `REFUTED`: diff shows the guard/check is present
   - `UNVERIFIABLE`: diff truncated or file not visible

4. **Risk policy** — `risk_policy.evaluate_risk()` computes `risk_level` from archetype + tagged hypotheses + router prior + defect signals. Single source of truth. No rubric in the LLM prompt. `PolicyVerdict` includes `cap_reason` and `applied_rules[]` for auditability.

5. **Quality gate** — `InvestigationQualityGate` checks whether the pipeline output is sufficient. Fires deterministically on structural signals (no SUPPORTED hypothesis despite defect signals, empty localization + truncated diff, schema failure). Not on LLM self-reported confidence.

This pipeline is fully implemented. All five stages are active in production evals.

### 3.3 Classification rules (design-level)

Four constraints define correct investigation at the architecture level. All are enforced by `risk_policy.py` as a pure Script function.

- **Mechanism floor:** SUPPORTED hypothesis (not capped by archetype) → risk ≥ HIGH
- **Archetype cap:** Clean archetype + no production defect signals → risk ≤ MEDIUM (router prior cannot override)
- **Speculative cap:** All-SPECULATIVE/UNVERIFIABLE + no defect signals → risk ≤ MEDIUM
- **Localization semantics:** `localization[]` = defect locus, not all touched files (D2 Jaccard penalizes extras)

### 3.4 Output contract

The `CommitInvestigationReport` (Pydantic-validated) is the enforcement surface for the method:

- `risk_assessment` — level + confidence (0–1)
- `evidence[]` — at least one required; empty reports rejected
- `localization[]` — file + lines + rationale (defect locus, not analysis scope)
- `findings[]`, `recommendations[]`, `reasoning_summary`
- `policy_verdict` — `{risk_level, cap_applied, cap_reason, applied_rules[], supported_count}`
- `metadata.per_stage` — `[{stage, tier, tokens, latency_ms, cost_usd}]` for each pipeline stage

Schema validation ensures the agent cannot produce freeform text that evades evaluation. `cap_reason` and `applied_rules[]` make every risk decision auditable without re-running the pipeline.

## 4. Evaluation Framework as Feedback Loop

Evaluation is not a report card — it's the mechanism by which the system learns whether the investigation method works.

### 4.1 Six dimensions as a diagnostic panel

| ID | Question answered | Method | Cost |
|----|-------------------|--------|------|
| D1 | Did the agent predict risk correctly? | Risk level vs buggy label | None |
| D2 | Did it point to the right files? | Agent files vs fix-commit files (Jaccard) | None |
| D3 | Does the reasoning match the actual root cause? | LLM-as-judge rubric 0–4 (adversarial judge) | LLM |
| D4 | Is risk severity calibrated? | Risk vs JIRA priority | None |
| D5 | Are recommendations aligned with the actual fix? | LLM-as-judge rubric 0–3 (adversarial judge) | LLM |
| D6 | Does the agent cite real artifacts? | Claims vs actual diff/files | None |

**Judge model constraint:** D3/D5 must use a model different from the investigator (or a blind judge with rubric stripped). Same-model judging is the self-evaluation anti-pattern.

### 4.2 Dimension coupling

Cross-dimension reads expose failure modes that no single metric catches:

| Pattern | Meaning | Example |
|---------|---------|---------|
| D6 high + D3 low | Describes structure, not failure mechanism | iter-1 baseline (D6=0.85, D3=0.13) |
| D3 high + D1 low | Identifies mechanism but won't commit to classification | b4c933b7: D3=1.0, D1=0.0 |
| D1 high + D6 low | Guessing — correct prediction with no evidence | Regression guard for pipeline changes |
| D2 low + D3 low | Lists analyzed files, not defect site | "files touched" ≠ "files containing the bug" |

These couplings define what the improvement cycle targets: a change that lifts D1 but drops D6 is a *regression*, not progress.

### 4.3 Gates and baselines

- **GATE:** All six must pass simultaneously on n≥50 stratified (50/50 buggy/clean). Any single failure blocks delivery.
- **Soft baseline rule:** Agent D1 must beat always-predict-clean and router-only.
- **Regression guard:** D6 ≥ 0.70 is a hard constraint in every eval run.
- **Judge independence:** Cross-model judge validation must be applied before any n=20 D3/D5 delivery claims.

## 5. Improvement Cycle

### 5.1 Cycle steps

| Step | Action | Artifact |
|------|--------|----------|
| 1. Hypothesize | Which dimension, which method change | Breadcrumb |
| 2. Implement | Prompt/context/policy change | Code diff |
| 3. Smoke | n=5 stratified, catch regressions fast | `output/runs/..._real_n5/` |
| 4. Validate | n=20 stratified, measure dimension scores | `eval-report.json` |
| 5. Compare | vs baseline + gates, per-commit JSON review | Updated baseline |
| 6. Decide | If trending → n=50 for confidence; if flat → pivot | State update |

### 5.2 Hard constraints (every iteration)

- Oracle isolation holds — agent never sees `buggy`, `fix`, `year`, `author_date`, JIRA
- 392+ tests pass after every change
- D6 ≥ 0.70 (grounding regression = immediate revert)
- Each iteration tracked as a breadcrumb with before/after scores
- Cross-model judge validation decision applied before n=20/n=50 D3/D5 claims

### 5.3 Phase roadmap

| Capability | Focus | Status |
|------------|-------|--------|
| Harness design | Investigation harness architecture + oracle isolation | **Complete** |
| Hybrid prompt | A+B staged CoT + router probability | **Complete** (D1=0.60, D3=0.20) |
| D3 failure taxonomy | Classify root-cause failure modes from eval data | **Complete** |
| Smart diff + clean-commit | 16K diff + dual-path clean-commit rubric | **Complete** (D1=0.75, panel n=12) |
| n=20 delivery gate | Stratified n=20 evaluation gate | **Complete** |
| Judge infrastructure | D3 JIRA fallback + cross-model judge validation | **Complete** |
| Five-stage Script/LLM pipeline | HypothesisEngine + evidence_tagger + risk_policy + quality_gate + smart-diff | **Complete** (V1 n=50 all gates pass) |
| Multi-turn A/B | 2-turn investigation on hard subset | **Deferred** — ΔD3 < threshold |
| V1 n=50 delivery | All six GATE thresholds on n=50 stratified | **Complete** (D1=0.70, D3=0.23) |
| Composite selector | Grounded evidence scoring + production-file rank tie-breakers | **Complete** |
| SUPPORTED-only localization | SUPPORTED-only filter in report_builder | **Complete** (D2_fix_chain=0.384) |
| Extended context | Test-adjacency + blame context injection | **Complete** |
| Historical defect context | ApacheJIT KNN defect-category priors; project-level fallback | **Complete** (net lift +0.01±0.03, ceiling confirmed) |
| Archetype FP fix | Test-only/examples-only commit archetype detection | **Complete** (FP 32%→24%) |
| V2 n=50 delivery | D1≥0.80, D3≥0.35 | **In progress** — D1=0.72, D3=0.31 |
| JIRA context injection | Inject JIRA summary for D3 ceiling | **Planned** |
| Adaptive confidence | 7-signal confidence score → adaptive stopping | **Planned** |

## 6. Trust Boundaries and Data Flow

```
                    ┌─────────────────────────────────────────────────────────┐
                    │               INVESTIGATION (commit-time only)           │
                    │                                                          │
 ApacheJIT CSVs ───┤──► CommitContextBuilder (16K diff; smart-diff with per-file priority) │
 Local git clones ─┤       (allowlist: numeric features only)                 │
 XGBoostRouter ────┤──► router_probability (ML prior, not risk verdict)       │
                    │         │                                                │
                    │         ▼                                                │
                    │   archetype.py (Script)                                  │
                    │         │                                                │
                    │         ▼                                                │
                    │   HypothesisEngine (LLM — Call 1)                       │
                    │         │                                                │
                    │         ▼                                                │
                    │   evidence_tagger.py (Script-first)                     │
                    │         │                                                │
                    │         ▼                                                │
                    │   risk_policy.py (Script — single source)               │
                    │         │                                                │
                    │         ▼                                                │
                    │   quality_gate.py (Script — deterministic)              │
                    │         │                                                │
                    │         ▼                                                │
                    │   CommitInvestigationReport                              │
                    │   (PolicyVerdict + per_stage metadata)                  │
                    └─────────────────────────────┬────────────────────────────┘
                                                  │
                    ┌─────────────────────────────┼────────────────────────────┐
                    │               EVALUATION (ground truth access)            │
                    │                                                          │
                    │  GroundTruthGraph ──────────┤                            │
                    │  (bug→fix→issue linkage)    │                            │
                    │                             ▼                            │
                    │  JiraClient (cached) ──► EvalHarness (D1–D6)             │
                    │                         AdversarialJudge (D3/D5)         │
                    │                         judge_model ≠ investigator_model │
                    │                             │                            │
                    │                             ▼                            │
                    │                    Timestamped run folder                │
                    └──────────────────────────────────────────────────────────┘
```

**What never enters investigation context:** `buggy`, `fix`, `year`, `author_date`, JIRA metadata, fix-commit diff, ground truth linkage. Enforced by allowlist in `CommitContextBuilder` + oracle isolation tests.

**Risk classification never enters investigation context:** The LLM does not receive `HIGH/MEDIUM/LOW` rubric tiers in its prompt. Risk is computed by `risk_policy.py` after hypothesis generation.

## 7. Implementation Map

### Components (current)

| Component | Module | Role | Status |
|-----------|--------|------|--------|
| `CursorSDKProvider` | `infra/llm.py` | LLM calls (Stage 1 + judge). Fallback: OpenAI → Mock | Active |
| `AgentOrchestrator` | `pipeline/orchestrator.py` | Pipeline coordinator, budget tracking, report assembly | Active |
| `XGBoostRouter` | `routing/router.py` | Zero-cost routing on numeric features (AUC=0.855) | Active |
| `CommitContextBuilder` | `context/context_builder.py` | Deterministic context bundle — smart-diff + bundle injection + missing_reasons | Active |
| `BundleExpander` | `context/bundle_expand.py` | Test-adjacency + blame context injection for missing-context commits | Active |
| `GitContextProvider` | `context/git_context.py` | Git CLI wrapper (diff, message, files, history) | Active |
| `archetype.py` | `analysis/archetype.py` | detect_archetype() + has_production_defect_signals() — FP fix for test/examples commits | Active |
| `evidence_tagger.py` | `analysis/evidence_tagger.py` | tag_hypothesis() Script-first tiering (SUPPORTED/SPECULATIVE/REFUTED/UNVERIFIABLE) | Active |
| `risk_policy.py` | `analysis/risk_policy.py` | evaluate_risk() — single source of risk_level | Active |
| `quality_gate.py` | `analysis/quality_gate.py` | InvestigationQualityGate — deterministic follow-up trigger | Active |
| `HypothesisEngine` | `hypothesis/hypothesis_engine.py` | LLM Stage 1 — mechanism + evidence_quote; contrastive selector; historical defect context injection | Active |
| `HypothesisPrompts` | `hypothesis/hypothesis_prompts.py` | Focused prompt templates (~40 lines, hypothesis + evidence_quote only) | Active |
| `HistoricalRAG` | `hypothesis/historical_rag.py` | ApacheJIT KNN defect-category priors; project-level fallback distribution | Active |
| `ReportBuilder` | `pipeline/report_builder.py` | SUPPORTED-only localization + defect-signal file ranking (D2 precision) | Active |
| `GroundTruthGraph` | `infra/ground_truth.py` | Bug→fix→issue index from replication package | Active |
| `EvalHarness` | `runners/eval_harness.py` | Six-dimension scoring, stratified sampling, aggregate reports | Active |
| `AdversarialJudge` | `runners/eval_judge.py` | LLM-as-judge D3/D5; cross-model judge validation (same-model judging acceptable) | Active |
| `CommitInvestigationReport` | `pipeline/orchestrator.py` | Pydantic schema — PolicyVerdict + per_stage metadata | Active |

### Components removed

| Component | Reason | Replacement |
|-----------|--------|-------------|
| `INVESTIGATION_SYSTEM_PROMPT` monolith (~135 lines) | Monolithic prompts anti-pattern; caused iter-2b round-1 catastrophic fail | `HypothesisEngine` + `HypothesisPrompts` |
| `_apply_clean_commit_risk_cap()` post-hoc regex | Duplicate risk policy; no audit trail | `risk_policy.py evaluate_risk()` |
| `_should_follow_up()` LLM self-report | Self-evaluation anti-pattern | `quality_gate.py InvestigationQualityGate` |
| `follow_up_needed` field in LLM output | LLM controlling its own follow-up | Removed — quality gate is deterministic |

### V1 scope

- Two Apache projects: Camel and Hadoop
- Local full clones under `data/repos/`
- Default eval budget: $50 (~300 investigations)
- 89+ tests (unit + integration)

### Deferred

- All 15 project clones (V1 scope: Camel + Hadoop)
- Line-level localization (GumTree mappings)
- Live JIRA during investigation (JIRA summary injection is V2 planned; live API is post-V2)
- Agent framework selection (LangGraph, CrewAI)
- Production deployment
- Multi-turn investigation (A/B showed ΔD3 < threshold — deferred)

## Related

| Document | Purpose |
|----------|---------|
| [harness.md](harness.md) | Deterministic infrastructure: routing, budget, schema, control plane, improvement cycle |
| [agent-loop.md](agent-loop.md) | Investigation pipeline: stages, validation, quality gates, model strategy |
| [evaluation.md](evaluation.md) | D1–D6 rubrics, acceptance thresholds, run results |
| [experiment-context.md](experiment-context.md) | Research thesis, oracle isolation rationale |
| [datasets.md](datasets.md) | ApacheJIT ground truth chain, data splits |
