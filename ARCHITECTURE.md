# Architecture — Commit Risk Investigation Harness

A closed-loop commit investigation system: a deterministic harness controls what the agent sees and how it runs; the LLM performs mechanism-level reasoning; a six-dimension adversarial eval scores output against oracle; failures drive method iteration.

## 1. What This System Is

This is a **verifiable commit investigation system** — not an LLM wrapper, not a classifier, not a pipeline that calls GPT and parses JSON. Its core is an investigation harness (production) plus a separate evaluation framework and improvement cycle. The agent is one controlled subsystem inside the harness.

The thesis: harness engineering (routing, context assembly, schema enforcement, cost governance) and a separate evaluation framework (six-dimension adversarial scoring against ground truth) together produce better investigations than raw model quality alone. ApacheJIT's ground truth chain — buggy commits linked to fixing commits linked to JIRA issues — enables evaluation dimensions that no score-only model can satisfy. A model can predict "buggy" but cannot prove it investigated the *mechanism*. This system can.

**Current phase:** V1 infrastructure is built and committed. The system is in the *investigation-quality* phase — improving D1/D2/D3 through method and prompt iteration while holding D6 (evidence grounding) as a hard regression constraint. The harness (infrastructure) is stable; the investigation method (configuring the agent loop) is the variable.

**Baseline (post data-leak fix, n=5 clean):** D1=0.40, D2=0.08, D3=0.13 (FAIL gates) | D4=0.84, D5=0.50, D6=0.85 (PASS). The agent describes diffs accurately and grounds its output in real artifacts, but it won't commit to risk classifications or articulate failure mechanisms without explicit architectural pressure.

## 2. Design Philosophy

### Adversarial verification

Evaluation is designed to *catch bad reasoning*, not confirm good accuracy. A D1=0.90 score is meaningless if D3=0.15 — the agent classified based on surface features ("big diff = risky") without understanding the actual bug mechanism. The six-dimension panel exists to expose exactly this pattern.

### Oracle isolation

The agent sees only commit-time context — what a human reviewer would have at the moment of review. Ground truth (buggy labels, fix commits, JIRA tickets) is reserved exclusively for evaluation. Proven critical: removing the leaked `buggy` label dropped D1 from 0.86 to 0.40 on identical commits.

### Grounding before guessing

Empty or boilerplate output is structurally rejected. The schema requires at least one evidence item. D6 (automated, zero LLM cost) checks whether the agent cites real files and diff content. D6=0.85 on the clean baseline proves the agent grounds its work in actual artifacts even when its classification fails.

### Harness provides infrastructure; agent loop produces investigation

The harness is deterministic infrastructure (routing, budget, schema, errors). The agent loop is the investigation process running inside. The LLM reasons within the agent loop but does not control it.

| Responsibility | Harness / Orchestrator (deterministic) | Agent (LLM) |
|---------------|------------------------|-------------|
| Which commits to investigate | Routing | — |
| What context to provide | Context builder | — |
| When to stop / follow-up | Turn cap, budget, follow-up triggers | Signals uncertainty via output fields |
| What format to output | Schema validation | Fills the schema |
| How much to spend | Budget enforcement | — |
| Whether investigation meets quality gates | Schema + follow-up triggers (no GT) | — |
| **Reasoning over evidence** | — | **Core LLM value** |

*Note: whether output matches ground truth is Evaluation's job (separate process, post-hoc). See [docs/evaluation.md](docs/evaluation.md).*

The LLM does one thing: reason over assembled context and produce structured output. Everything else is deterministic infrastructure.

## 3. The Investigation Method

### 3.1 Intent

Investigation means: classify risk *and* articulate *what could break*, with diff-grounded evidence. The output must be actionable for a human reviewer — not "this is a large change" but "if `getAmazonAWSHost()` returns a value without a leading dot, the URL will be malformed."

### 3.2 Four-stage reasoning model

The investigation method follows four conceptual stages regardless of prompt implementation:

1. **Change summary** — scope, touched files, stated intent from commit message. Establishes what was attempted.
2. **Defect hypotheses** — 2–3 mechanistic candidates, each structured as "If ⟨condition⟩ then ⟨failure⟩ in ⟨location⟩". Not "could contain bugs" — specific failure modes.
3. **Evidence triage** — each hypothesis marked SUPPORTED / REFUTED / UNVERIFIABLE with diff citations. Agent must cite specific lines or hunks, not entire files.
4. **Verdict** — risk level tied to rubric tier based on supported hypotheses. No hedging when mechanism is identified.

This model is the *architectural requirement*. Concrete prompt implementation for iter-1: see [spike research §5.2](docs/spike-investigation-harness.md).

### 3.3 Classification rules (design-level)

Four constraints define correct investigation at the architecture level. Operational enforcement — prompt gates and schema validation — is in [docs/harness.md §Investigation Method](docs/harness.md#investigation-method-operational). Dimension scoring (post-hoc, against GT) is in [docs/evaluation.md](docs/evaluation.md).

- **Mechanism floor:** SUPPORTED mechanistic hypothesis → risk ≥ HIGH (b4c933b7: D3=1.0 but D1=0.0 without this rule).
- **No hedge downgrade:** Additive/blast-radius/backward-compatible language must not lower risk when a mechanism is identified ([spike §1.5 hedging catalog](docs/spike-investigation-harness.md)).
- **Localization semantics:** `localization[]` = defect locus, not all touched files (D2 Jaccard penalizes extras).
- **ML prior, not oracle:** Router probability is injected context, not the buggy label.

### 3.4 Output contract

The `CommitInvestigationReport` (Pydantic-validated) is the enforcement surface for the method:

- `risk_assessment` — level + confidence (0–1)
- `evidence[]` — at least one required; empty reports rejected
- `localization[]` — file + lines + rationale (defect locus, not analysis scope)
- `findings[]`, `recommendations[]`, `reasoning_summary`

Schema validation ensures the agent cannot produce freeform text that evades evaluation.

## 4. Evaluation Framework as Feedback Loop

Evaluation is not a report card — it's the mechanism by which the system learns whether the investigation method works.

### 4.1 Six dimensions as a diagnostic panel

| ID | Question answered | Method | Cost |
|----|-------------------|--------|------|
| D1 | Did the agent predict risk correctly? | Risk level vs buggy label | None |
| D2 | Did it point to the right files? | Agent files vs fix-commit files (Jaccard) | None |
| D3 | Does the reasoning match the actual root cause? | LLM-as-judge rubric 0–4 | LLM |
| D4 | Is risk severity calibrated? | Risk vs JIRA priority | None |
| D5 | Are recommendations aligned with the actual fix? | LLM-as-judge rubric 0–3 | LLM |
| D6 | Does the agent cite real artifacts? | Claims vs actual diff/files | None |

Full rubrics and thresholds: [docs/evaluation.md](docs/evaluation.md).

### 4.2 Dimension coupling

Cross-dimension reads expose failure modes that no single metric catches:

| Pattern | Meaning | Example |
|---------|---------|---------|
| D6 high + D3 low | Describes structure, not failure mechanism | Current baseline (D6=0.85, D3=0.13) |
| D3 high + D1 low | Identifies mechanism but won't commit to classification | b4c933b7: D3=1.0, D1=0.0 |
| D1 high + D6 low | Guessing — correct prediction with no evidence | Regression guard for prompt changes |
| D2 low + D3 low | Lists analyzed files, not defect site | "files touched" ≠ "files containing the bug" |

These couplings define what the improvement cycle targets: a change that lifts D1 but drops D6 is a *regression*, not progress.

### 4.3 Gates and baselines

- **GATE:** All six must pass simultaneously on n≥50 stratified (50/50 buggy/clean). Any single failure blocks delivery.
- **Soft baseline rule:** Agent D1 must beat always-predict-clean and router-only. Violation emits WARNING.
- **Regression guard:** D6 ≥ 0.70 is a hard constraint in every eval run. Drop below = revert.

Gate/target/stretch thresholds: [docs/evaluation.md](docs/evaluation.md) and [`.harness/state.json`](.harness/state.json).

## 5. Improvement Cycle

The system improves through eval-driven iteration: change the method → measure against oracle → analyze failures → repeat.

### 5.1 Cycle steps

| Step | Action | Artifact |
|------|--------|----------|
| 1. Hypothesize | Which dimension, which method change | Breadcrumb |
| 2. Implement | Prompt/context change (not infrastructure) | Code diff |
| 3. Smoke | n=5 stratified, catch regressions fast | `output/runs/..._real_n5/` |
| 4. Validate | n=20 stratified, measure dimension scores | `eval-report.json` |
| 5. Compare | vs baseline + gates, per-commit JSON review | Updated baseline |
| 6. Decide | If trending → n=50 for confidence; if flat → pivot | State update |

### 5.2 Hard constraints (every iteration)

- Oracle isolation holds — agent never sees buggy/fix/year/JIRA
- 76+ tests pass after every change
- D6 ≥ 0.70 (grounding regression = revert)
- No infrastructure changes unless they directly unblock a metric

### 5.3 Phase roadmap

| Phase | Focus | Status |
|-------|-------|--------|
| spike-0 | Define investigation harness design (this document) | Complete |
| iter-1 | A+B hybrid prompt (rubric + staged CoT + router probability) | Pending |
| iter-2 | Validate at n=50 | Pending |
| iter-3 | Multi-turn investigation for low-confidence commits | Pending |
| iter-4 | D2 localization focus | Pending |

Research backing for iter-1 approach: [docs/spike-investigation-harness.md](docs/spike-investigation-harness.md).

## 6. Trust Boundaries and Data Flow

```
                    ┌─────────────────────────────────────────────────────────┐
                    │               INVESTIGATION (commit-time only)           │
                    │                                                          │
 ApacheJIT CSVs ───┤──► CommitContextBuilder ──► AgentOrchestrator (≤3 turns) │
 Local git clones ─┤       (allowlist: numeric features only)                 │
 XGBoost router ───┤──► router_probability (ML prior)                         │
                    │                                     │                    │
                    │                                     ▼                    │
                    │                        CommitInvestigationReport          │
                    └─────────────────────────────────────┬────────────────────┘
                                                          │
                    ┌─────────────────────────────────────┼────────────────────┐
                    │               EVALUATION (ground truth access)            │
                    │                                     │                    │
                    │  GroundTruthGraph ──────────────────┤                    │
                    │  (bug→fix→issue linkage)            │                    │
                    │                                     ▼                    │
                    │  JiraClient (cached) ──────► EvalHarness (D1–D6)         │
                    │                              ReasoningJudge (D3/D5)      │
                    │                                     │                    │
                    │                                     ▼                    │
                    │                        Timestamped run folder             │
                    │                        (config, log, investigations/,     │
                    │                         evaluations/, eval-report)        │
                    └──────────────────────────────────────────────────────────┘
```

**What never enters investigation context:** `buggy`, `fix`, `year`, `author_date`, JIRA metadata, fix-commit diff, ground truth linkage. Enforced by allowlist in `CommitContextBuilder` + 7 oracle isolation tests.

**Router train/eval split:** XGBoost trained on train split only. Router probability is a feature derived from the same numeric metrics the agent sees — correlated with bugginess but not the label itself.

## 7. V1 Implementation Map

V1 build is complete (see [`.harness/archive/phases/v1-build/`](.harness/archive/phases/v1-build/)). Current work is investigation-quality iteration.

### Components

| Component | Role |
|-----------|------|
| `CursorSDKProvider` | LLM calls (investigation + judge). Fallback: OpenAI → Mock |
| `AgentOrchestrator` | Turn governance, tool dispatch, budget tracking, report assembly |
| `XGBoostRouter` | Zero-cost routing on numeric features (AUC=0.855) |
| `CommitContextBuilder` | Deterministic context bundle from git + CSV + author stats |
| `GitContextProvider` | Git CLI wrapper (diff, message, files, history) |
| `GroundTruthGraph` | Bug→fix→issue index from replication package |
| `EvalHarness` | Six-dimension scoring, stratified sampling, aggregate reports |
| `ReasoningJudge` | LLM-as-judge for D3 (rubric 0–4) and D5 (rubric 0–3) |
| `CommitInvestigationReport` | Pydantic schema — enforcement surface for investigation method |

### V1 scope

- Two Apache projects: Camel and Hadoop
- Local full clones under `data/repos/`
- Default eval budget: $50 (~300 investigations)
- 76 tests (unit + integration)

### Deferred

- All 15 project clones
- Line-level localization (GumTree mappings)
- Live JIRA during investigation
- Agent framework selection (LangGraph, CrewAI)
- Production deployment

## Related Documents

| Document | Purpose |
|----------|---------|
| [docs/harness.md](docs/harness.md) | Operational design: control plane, investigation method, eval loop, improvement cycle |
| [docs/spike-investigation-harness.md](docs/spike-investigation-harness.md) | Research spike: failure analysis, external approaches, iter-1 prompt recommendation |
| [docs/evaluation.md](docs/evaluation.md) | Dimension rubrics, acceptance thresholds, run results |
| [docs/experiment-context.md](docs/experiment-context.md) | Research thesis, experiment lineage, oracle isolation rationale |
| [docs/datasets.md](docs/datasets.md) | ApacheJIT ground truth chain, data splits, download instructions |
