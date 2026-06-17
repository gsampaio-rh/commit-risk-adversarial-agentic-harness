# System Specification — Bug Attribution Agent (V4.2 Target Architecture)

Given a JIRA bug report (title + description) and a temporally-bounded git repository, this system identifies the commit that most likely **introduced** the bug. It produces a ranked list of suspect commits with causal mechanisms and evidence quotes, evaluated against SZZ-derived ground truth.

> **Architecture status:** The **target architecture is V4.2** (Revised Hierarchical Pipeline) — separates narrowing from deep investigation via a 4-phase pipeline: retrieval → script pre-score → LLM triage → scoped investigation (+ conditional watchlist expansion). V4.1 (Scoped Tools) is the current implementation. See [.harness/docs/v42-architecture-adr.md](../.harness/docs/v42-architecture-adr.md) for the V4.2 decision, [.harness/docs/architecture-constraints.md](../.harness/docs/architecture-constraints.md) for codified NFRs, [.harness/docs/scoped-tools-adr.md](../.harness/docs/scoped-tools-adr.md) for the V4→V4.1 pivot.

---

## Three Pipelines

The system consists of three pipelines with distinct ownership and clear boundaries:

```
┌──────────────────────────────────────────┐
│  INPUT PIPELINE (infrastructure)         │
│  Phase 0:  Extraction                    │
│  Phase 1a: Retrieval + Script Pre-Score  │
│  Owner: Scripts / eval harness           │
│  LLM cost: Zero                          │
└──────────────┬───────────────────────────┘
               │ ScoredShortlist + ProblemStatement
               ▼
┌──────────────────────────────────────────┐
│  AGENT PIPELINE (governed LLM)           │
│  Phase 1b: LLM Triage (1 call)          │
│  Phase 2:  Scoped Investigation (ReAct)  │
│  Phase 2b: Watchlist Expansion (cond.)   │
│  Owner: Investigation Harness + LLM      │
│  LLM cost: Full budget                   │
└──────────────┬───────────────────────────┘
               │ InvestigationResult
               ▼
┌──────────────────────────────────────────┐
│  EVALUATION PIPELINE (oracle)            │
│  Scoring: Hit@k, MRR, D3, D6            │
│  Funnel: Recall@100→@15→@7→Exam→Hit@5   │
│  Owner: Eval harness                     │
│  LLM cost: Zero (except D3 judge)       │
└──────────────────────────────────────────┘
```

### Pipeline boundaries

| Pipeline | Phases | Owner | LLM involvement | The agent sees... |
|----------|--------|-------|-----------------|-------------------|
| Input | 0 (Extraction) + 1a (Retrieval + Pre-Score) | Scripts / eval harness | Zero (optional LLM for Level 2 extraction) | Nothing — this is input preparation |
| Agent | 1b (Triage) + 2 (Investigation) + 2b (Watchlist expansion) | Investigation harness (harness governs LLM) | Full — all LLM budget spent here | ScoredShortlist + ProblemStatement |
| Evaluation | Scoring against ground truth | Oracle (eval harness) | Zero (except D3 LLM judge) | Nothing — scores are oracle-only |

---

## Input Pipeline (Stages 0–1)

The input pipeline prepares everything the agent needs to begin reasoning. It is infrastructure — not part of the agent's cognitive loop.

### Stage 0 — Extraction

| Aspect | Detail |
|--------|--------|
| **Owner** | Script (regex + optional LLM for Level 2) |
| **Input** | Raw JIRA text (title + description) |
| **Output** | `ProblemStatement` with extracted files, symbols, keywords, time hints |
| **Contract** | Must produce at least 1 search signal |
| **Module** | `extraction/problem_extractor.py` |

Extraction is NOT part of the agent. It transforms raw text into structured signals that the retrieval stage can use. Level 1 is regex-based (current). Level 2 adds LLM-assisted extraction (**TBD**: `spike-level2-extractor` task).

### Stage 1 — Candidate Retrieval

| Aspect | Detail |
|--------|--------|
| **Owner** | Script (git commands, zero LLM) |
| **Input** | `ProblemStatement` + repo + temporal bound |
| **Output** | `CandidateSet` (50-100 ranked commits) |
| **Contract** | Retrieval Recall target: ground truth in top 100 |
| **Module** | **TBD** — to be built in `retrieval-spike` task |

Retrieval uses deterministic git operations to assemble a candidate set:
- File-based: `git log --all -- <extracted_files>` bounded by temporal ref
- Keyword-based: `git log --all --grep="<keyword>"` bounded by temporal ref
- Pickaxe: `git log --all -S "<symbol>"` for code-level search
- Time-window: recent commits within N months of bug report
- Blame-based: `git blame <bound> -- <file>` to find line-level authorship

**Fallback:** If extraction yields zero signals, retrieval widens to broad heuristics (recent commits, large diffs). If candidate set is too small (< 10), retrieval parameters are loosened.

All retrieval respects the temporal bound — only commits reachable from `COMMIT_B~1`.

---

## Agent Pipeline — V4.2 Revised Hierarchical (Target)

The agent receives a `ScoredShortlist` + `ProblemStatement` from the input pipeline and produces a ranked suspect list. It uses a **4-phase pipeline** that separates narrowing (triage) from deep investigation (scoped ReAct loop). See [V4.2 ADR](../.harness/docs/v42-architecture-adr.md).

### Phase 1b: LLM Triage (1 call, one-shot)

| Aspect | Detail |
|--------|--------|
| **Owner** | Harness (prompt assembly, output parsing, validation) |
| **Input** | Bug report + 15 candidates with metadata + diff_summary |
| **Output** | `TriageResult`: 3 must-examine + 4 watchlist |
| **Budget** | 1 LLM call, 0 tools |
| **Constraint** | Top 3 by pre_score MUST appear in must_examine (harness-enforced) |
| **Fallback** | Invalid LLM output → must_examine = top 3 by pre_score, watchlist = next 4 |

### Phase 2: Scoped Investigation (multi-turn ReAct)

| Aspect | Detail |
|--------|--------|
| **Owner** | `ScopedInvestigator` (harness) + LLM |
| **Input** | Bug report + must-examine candidates (SHA + 1-line triage rationale) + scoped tools |
| **Output** | Ranked suspects with confidence, mechanism, evidence quotes |
| **Budget** | 15 tool calls (soft), 8 turns |
| **Context** | Harness-managed: rolling summary (≤2K tokens) + last-turn tool results |

### Phase 2b: Watchlist Expansion (conditional)

| Aspect | Detail |
|--------|--------|
| **Trigger** | No suspects OR max_confidence < 0.6 OR no evidence_quotes on top suspect |
| **Input** | Fresh context: bug report + watchlist candidates + Phase 2 best suspect summary |
| **Budget** | 8 tool calls, 4 turns (separate from Phase 2) |
| **Merge** | Dedup by SHA, confidence = max, evidence_quotes = union, re-rank by grounded quotes |

### Scoped Tools

Tools are registered via `build_scoped_tools()` in `agent/tools.py`. SHA-taking tools validate that the commit exists in the CandidateSet before execution.

| Tool | Scope | SHA validation |
|------|-------|---------------|
| `get_commit_diff` | CandidateSet only | 12-char prefix match |
| `get_commit_message` | CandidateSet only | 12-char prefix match |
| `get_blame` | Any file | None (temporal bound handles safety) |
| `get_file_at_commit` | CandidateSet only | 12-char prefix match |

Search tools (`search_commits_by_file`, `search_commits_by_keyword`, `list_recent_commits`) are **not registered** — retrieval is done by the input pipeline.

### V4.1 → V4.2 evolution

V4.1 passed 20 candidates into a single system prompt, forcing simultaneous triage and investigation. V4.2 separates these: Phase 1a/1b narrows to 7 candidates, Phase 2 investigates deeply with fresh context. See [V4.2 ADR](../.harness/docs/v42-architecture-adr.md) for the full decision and [scoped-tools ADR](../.harness/docs/scoped-tools-adr.md) for the V4→V4.1 pivot.

## Agent Pipeline — V4 3-Stage Harness (Historical)

> **Note:** The V4 3-stage harness below is superseded by V4.1. It is preserved for reference. Code: `harness/harness.py`.

The V4 agent operated inside a governed framework with three layers:

| Layer | Role | Mechanism |
|-------|------|-----------|
| **Investigation Harness** | Manages lifecycle, state, transitions, completion | Script-based orchestrator |
| **Investigation Rules** | Constrains behavior, enforces quality | **Hybrid** — hard gates + soft guidance. YAML in `data/governance/rules/`. |
| **Investigation Skills** | Augments strategy with learned patterns | **Hybrid** — keyword retrieval + manual curation. Markdown in `data/governance/skills/`. |

Stages: Planning (InvestigationBrief) → Examination (tool dispatch with completion criteria) → Attribution (ranked suspects). See [mechanism-design ADR](../.harness/docs/mechanism-design.md) for full details.

---

## Agent Governance (V4.2)

V4.2 governance is phase-aware with explicit harness control:

- **Tool scoping:** `build_scoped_tools()` restricts SHA-taking tools to CandidateSet commits
- **Temporal bound:** All tools go through `GitContextProvider._enforce_bound()`
- **Script-anchored triage:** Top 3 by pre_score are harness-pinned into must_examine (LLM cannot veto)
- **Budget limits:** Global cap ~23 (Phase 2: 15 soft, Phase 2b: 8 overflow)
- **Minimum examination:** Must call `get_commit_diff` on each must_examine SHA before normal exit
- **4-tier nudge ladder:** State-based nudges escalate from gentle to harness force-conclude
- **Context compression:** Cache deduplication + formatted output + structured extraction (8000-char baseline)
- **Fail-fast eval:** No silent degradation to weaker providers during gated runs

See [architecture-constraints.md](../.harness/docs/architecture-constraints.md) for the full invariant and NFR list.

---

## Investigation Traces

Every investigation produces a structured `InvestigationTrace`:

| Field | Description |
|-------|-------------|
| `hypotheses` | Formed, confirmed, rejected, abandoned — with reasons |
| `candidates_examined` | Which commits were inspected and what was found |
| `candidates_eliminated` | Which were rejected and why |
| `evidence_collected` | Quotes, grounding status, quality |
| `strategy_decisions` | Why this path was chosen over alternatives |
| `stage_timings` | Time and cost per stage |
| `outcome` | Final report + eval result (in eval mode) |

**Schema:** JSON per investigation at `results/traces/{issue_key}/{run_id}.json`. Per-turn granularity for Stage 3. Full field definitions in [mechanism-design ADR §Q4](../.harness/docs/mechanism-design.md#5-q4--trace-schema).

Traces enable:
- Skill emergence (learn from successes and failures)
- Failure forensics (why did we miss case X?)
- Retrieval diagnostics (did the candidate set contain ground truth?)
- Cost analysis (where is budget spent?)

---

## Data Structures

### ProblemStatement (input to agent)

```python
@dataclass(frozen=True)
class ProblemStatement:
    title: str                              # JIRA summary
    description: str                        # JIRA description (raw)
    project: str                            # e.g. "CAMEL" (harness metadata)
    issue_key: str = ""                     # e.g. "CAMEL-1234" (harness metadata)
    extracted_files: list[str] = []         # from extraction stage
    extracted_symbols: list[str] = []       # from extraction stage
    extracted_keywords: list[str] = []      # from extraction stage
    time_hints: dict = field(default_factory=dict)  # temporal context
```

### CandidateSet (input to agent, from retrieval)

```python
@dataclass
class CandidateSet:
    commits: list[CandidateCommit]          # ranked, 50-100 entries
    retrieval_metadata: dict                # strategy used, time window, recall estimate
    temporal_bound: str                     # the bound ref applied during retrieval

@dataclass
class CandidateCommit:
    commit_id: str                          # full 40-char SHA
    rank: int                               # 1-based retrieval rank
    retrieval_signal: str                   # why this was retrieved (file match, keyword, blame)
    summary: str                            # one-line commit message
    files_changed: list[str]               # stat output
    date: str                              # author date
```

### InvestigationBrief (Stage 2 output)

```python
@dataclass
class InvestigationBrief:
    hypotheses: list[Hypothesis]            # falsifiable statements
    examination_plan: list[ExaminationStep] # what to check and why
    success_criteria: CompletionCriteria    # when "done"
    strategy: str                           # overall approach description
    max_effort: int = 18                     # max tool calls for examination (default)
```

### InvestigationState (harness-managed)

```python
@dataclass
class InvestigationState:
    current_stage: int                      # 2, 3, or 4
    candidates_examined: int
    candidates_total: int
    hypotheses_tested: int
    hypotheses_confirmed: int
    evidence_quotes_collected: int
    re_plan_count: int                      # max 2
    budget_used: BudgetState
    brief: InvestigationBrief | None
```

### CompletionCriteria (brief-driven exit)

```python
@dataclass
class CompletionCriteria:
    evidence_threshold: int = 3           # min grounded quotes across suspects
    hypothesis_coverage: int = 2          # min alternative explanations tested
    confidence_gate: float = 0.60         # min top suspect confidence
    brief_satisfaction: bool = False        # all planned examinations done
    budget_hard_stop: bool = False          # budget exceeded (safety net)
```

### InvestigationTrace (structured investigation record)

```python
@dataclass
class InvestigationTrace:
    trace_id: str
    issue_key: str
    run_id: str
    temporal_bound: str
    candidate_set_size: int
    retrieval_recall_100: bool | None       # eval-only
    hypotheses: list[HypothesisRecord]      # id, statement, status, reason, stage, turn
    candidates_examined: list[str]            # commit SHAs inspected
    candidates_eliminated: list[EliminationRecord]  # commit_id, reason, turn, hypothesis_id
    evidence_collected: list[EvidenceRecord]  # commit_id, quote, grounded, hypothesis_id, turn
    strategy_decisions: list[StrategyRecord]  # decision, rationale, stage, turn, alternatives
    examination_turns: list[TurnRecord]      # per-turn Stage 3 log
    stage_timings: dict[str, float]           # stage → elapsed_ms
    outcome: OutcomeRecord                    # suspect_count, degraded, hit_at_5 (eval-only)
```

> **Note:** Nested record types defined in [mechanism-design ADR §Q4](../.harness/docs/mechanism-design.md#5-q4--trace-schema).

### BugAttributionReport (output)

```python
@dataclass
class BugAttributionReport:
    problem_title: str
    problem_description: str
    suspects: list[SuspectCommit]           # rank-ordered
    reasoning_summary: str
    tool_trace: list[ToolCallRecord]
    metadata: dict[str, Any]                # evidence_scores, cost, model, etc.
    investigation_trace: InvestigationTrace | None  # full structured trace
```

### SuspectCommit

```python
@dataclass
class SuspectCommit:
    commit_id: str                          # full SHA
    rank: int                               # 1-based
    confidence: float                       # 0.0-1.0
    mechanism: str                          # "If <change> then <consequence>"
    evidence_quotes: list[str]              # exact text from diffs
```

---

## The LLM Boundary

The LLM operates inside a strict information boundary:

### What the LLM sees (V4)

| Source | Content | Delivered via |
|--------|---------|---------------|
| Bug report | JIRA title + description | `ProblemStatement.to_prompt_text()` |
| Candidate set | Pre-filtered commits with summaries | `CandidateSet` formatted by harness |
| Skills | Relevant strategies from past investigations | Injected by harness into planning context |
| Examination results | Diffs, blame, file contents of candidates | Tool results from Stage 3 |
| Investigation state | Progress, remaining work | Harness status messages |

### What the LLM never sees

| Data | Why forbidden |
|------|---------------|
| `bug_hash` (ground truth answer) | Would trivialize the task |
| `fix_hash`, fix commit diff/message | Beyond temporal bound |
| Ground truth chain (bug→fix→issue) | Eval-only retrospective data |
| Evidence scores | Computed post-attribution; never fed back |
| Eval metrics (Hit@k, MRR) | Oracle-only |
| Raw repo history (unbounded) | Agent works on CandidateSet, not full repo |

---

## Temporal Model

The agent simulates an engineer at **bug-report time** — after the defect exists but before any fix lands.

```
[bug introduced] --> [bug reported] --> agent investigates HERE --> [fix commit(s)]
                        ^ input              ^ bound = COMMIT_B~1       ^ invisible
```

### Rules

| Rule | Detail |
|------|--------|
| Bound definition | `COMMIT_B~1` = parent of the earliest fix commit |
| Scope | Constrains the ENTIRE system (input pipeline + agent tools) |
| Eval mode source | `fix_hash` from ground truth chain |
| Production mode source | Bug report creation date or HEAD (**TBD**) |
| Fix commit invisibility | `COMMIT_B`'s diff, message, metadata never accessible |

### Enforcement

- **Input Pipeline (Stage 1):** All `git log` commands append the bound ref
- **Agent Tools (Stage 3):** `_enforce_bound()` per-commit guard + search pre-filtering
- **Violation handling:** `TemporalBoundViolation` → error text returned to LLM

---

## Fallback and Degradation

| Failure mode | Detection | Fallback |
|--------------|-----------|----------|
| Extraction yields zero signals | `extracted_files` empty, no keywords | Retrieval widens: recent commits, large diffs, broad time window |
| Retrieval < 10 candidates | `CandidateSet` size below threshold | Widen parameters: longer time window, looser matching |
| Retrieval recall = 0 | Only measurable in eval mode | Log as retrieval failure. Agent proceeds best-effort. |
| Planning produces no hypotheses | Harness validates brief structure | Re-invoke planning with broader prompt |
| Examination exhausts brief | All hypotheses tested, none confirmed | Loop back to planning (max 2 re-plans) |
| Budget exceeded | Hard stop by harness | Proceed to attribution with available evidence (degraded) |

---

## Tool Catalog (Stage 3)

All tools wrap `GitContextProvider` methods. Available during Stage 3 (Examination) only.

| Tool | Required args | Optional args | Use case |
|------|--------------|---------------|----------|
| `get_commit_diff` | `commit_id` | — | Inspect candidate changes |
| `get_commit_message` | `commit_id` | — | Read author intent |
| `get_file_at_commit` | `commit_id`, `path` | — | Read file state at commit |
| `get_blame` | `path` | `line_start`, `line_end` | Trace line-level authorship |

**Scoping:** In V4, search tools (`search_commits_by_file`, `search_commits_by_keyword`, `list_recent_commits`) move to the input pipeline (Stage 1). The agent examines candidates from `CandidateSet` rather than searching the full repo.

**Temporal enforcement:** All tools enforce the temporal bound via `_enforce_bound()`. Violations return error text to the LLM.

---

## Current Implementation (V3) — Reference

The V3 implementation (current code) uses a different architecture:

| Aspect | V3 | V4.1 (current) | V4.2 (target) |
|--------|--------------|-------------|---------------|
| Agent receives | Full repo access | 20 candidates + scoped tools | 7 triaged candidates + scoped tools |
| Search | LLM via tools (5-8 calls) | Scripts (zero LLM) | Scripts (zero LLM) |
| Narrowing | None (LLM searches) | None (all 20 in prompt) | Script pre-score + LLM triage |
| Examination | Ad hoc tools | Scoped tools, single loop | Scoped tools, per-candidate focus |
| Governance | Budget-only (30/15) | Budget (15/8) + SHA validation | Phase-aware budget + nudge ladder + script-anchored triage |
| Exit signal | Budget exhaustion | Budget or suspects | Exit reason enum, must-examine gate |
| Tracing | `tool_trace` (500-char) | `InvestigationTrace` JSON | 5-stage funnel metrics |
| Best result | Hit@5=0.50, MRR=0.304 | TBD (eval blocked by SDK) | Target: Hit@5 ≥ 0.40 |

V3 code lives in `src/commit_investigator/` with packages: `extraction/`, `agent/`, `eval/`, `infra/`. The V3 agentic loop runs 7 advisory stages (5 LLM + 2 script) inside `AgentOrchestrator.investigate()`. Full V3 details in the [exp19b retrospective](../.harness/docs/exp19b-retrospective.md).

---

## Related

| Document | Content |
|----------|---------|
| [agent-loop.md](agent-loop.md) | V4.2 agent loop mechanics (phases 1b-2-2b) |
| [evaluation-framework.md](evaluation-framework.md) | Metrics, 5-stage funnel, baselines |
| [glossary.md](glossary.md) | Term definitions |
| [datasets.md](datasets.md) | ApacheJIT data, ground truth chain |
| [.harness/docs/v42-architecture-adr.md](../.harness/docs/v42-architecture-adr.md) | V4.2 architecture decision record |
| [.harness/docs/architecture-constraints.md](../.harness/docs/architecture-constraints.md) | NFRs and invariants |
| [.harness/docs/scoped-tools-adr.md](../.harness/docs/scoped-tools-adr.md) | V4→V4.1 pivot |
| [.harness/docs/topology-debate.md](../.harness/docs/topology-debate.md) | V4 topology ADR (historical) |
| [.harness/docs/mechanism-design.md](../.harness/docs/mechanism-design.md) | Mechanism ADR: rules, skills, traces, thresholds |
