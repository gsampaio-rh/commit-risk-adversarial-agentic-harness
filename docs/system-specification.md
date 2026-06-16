# System Specification — Bug Attribution Agent (V4 Target Architecture)

Given a JIRA bug report (title + description) and a temporally-bounded git repository, this system identifies the commit that most likely **introduced** the bug. It produces a ranked list of suspect commits with causal mechanisms and evidence quotes, evaluated against SZZ-derived ground truth.

> **Architecture status:** This document describes the **V4 target architecture**. The current implementation (V3) is summarized at the end. See [.harness/docs/topology-debate.md](../.harness/docs/topology-debate.md) for the ADR that led to V4. Implementation decisions marked **TBD** will be resolved in the `mechanism-design` and `retrieval-spike` tasks.

---

## Three Pipelines

The system consists of three pipelines with distinct ownership and clear boundaries:

```
┌─────────────────────────────────────┐
│  INPUT PIPELINE (infrastructure)    │
│  Stage 0: Extraction                │
│  Stage 1: Candidate Retrieval       │
│  Owner: Scripts / eval harness      │
│  LLM cost: Zero                     │
└──────────────┬──────────────────────┘
               │ CandidateSet + ProblemStatement
               ▼
┌─────────────────────────────────────┐
│  AGENT PIPELINE (governed LLM)      │
│  Stage 2: Planning                  │
│  Stage 3: Examination               │
│  Stage 4: Attribution               │
│  Owner: Investigation Harness + LLM │
│  LLM cost: Full budget              │
└──────────────┬──────────────────────┘
               │ BugAttributionReport
               ▼
┌─────────────────────────────────────┐
│  EVALUATION PIPELINE (oracle)       │
│  Scoring: Hit@k, MRR, D3, D6       │
│  Owner: Eval harness                │
│  LLM cost: Zero (except D3 judge)  │
└─────────────────────────────────────┘
```

### Pipeline boundaries

| Pipeline | Stages | Owner | LLM involvement | The agent sees... |
|----------|--------|-------|-----------------|-------------------|
| Input | 0 (Extraction) + 1 (Retrieval) | Scripts / eval harness | Zero (optional LLM for Level 2 extraction) | Nothing — this is input preparation |
| Agent | 2 (Planning) + 3 (Examination) + 4 (Attribution) | Agent framework (harness governs LLM) | Full — all LLM budget spent here | CandidateSet + ProblemStatement + skills |
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

## Agent Pipeline (Stages 2–3–4)

The agent receives `CandidateSet` + `ProblemStatement` as input and produces `BugAttributionReport` as output. It is governed by the **investigation harness** — the LLM does not self-govern.

### Agent Framework

The agent operates inside a governed framework with three layers:

| Layer | Role | Mechanism |
|-------|------|-----------|
| **Investigation Harness** | Manages lifecycle, state, transitions, completion | Script-based orchestrator |
| **Investigation Rules** | Constrains behavior, enforces quality | **TBD**: hard gates / soft guidance / hybrid |
| **Investigation Skills** | Augments strategy with learned patterns from traces | **TBD**: RAG / rule extraction / hybrid |

### Stage 2 — Planning

| Aspect | Detail |
|--------|--------|
| **Owner** | LLM (structured output), governed by harness |
| **Input** | `CandidateSet` + `ProblemStatement` + relevant skills |
| **Output** | `InvestigationBrief` |
| **Contract** | Must state falsifiable hypotheses + completion criteria |

The LLM produces a structured `InvestigationBrief` that defines:
- Hypotheses to test (falsifiable statements about what caused the bug)
- Examination plan (which commits/files to inspect and what to look for)
- Success criteria (when the investigation is "done")
- Strategy rationale

The harness validates the brief structure before allowing transition to Stage 3. If the brief is invalid (no hypotheses, no plan), the harness re-invokes planning with broader context.

### Stage 3 — Examination

| Aspect | Detail |
|--------|--------|
| **Owner** | LLM + tools, governed by harness + rules |
| **Input** | `InvestigationBrief` + candidate diffs/blame |
| **Output** | Evidence collected, hypotheses confirmed/rejected |
| **Contract** | Brief satisfaction: evidence quality threshold met |

The LLM examines candidate commits according to the brief. Tools available:

| Tool | Use case |
|------|----------|
| `get_commit_diff` | Inspect candidate changes |
| `get_commit_message` | Read author intent |
| `get_file_at_commit` | Read file state at commit |
| `get_blame` | Trace line-level authorship |

Tools are **scoped to the candidate set** — the LLM examines commits from `CandidateSet`, not the entire repository history. All tools enforce the temporal bound.

After each examination turn, the harness evaluates completion criteria:
- Evidence threshold met? → advance to Stage 4
- Hypotheses exhausted but insufficient evidence? → loop back to Stage 2 (max 2 re-plans)
- Budget exceeded? → forced advance to Stage 4 (degraded mode)

### Stage 4 — Attribution

| Aspect | Detail |
|--------|--------|
| **Owner** | LLM (conclude), governed by harness |
| **Input** | Evidence collected + reasoning from Stage 3 |
| **Output** | `BugAttributionReport` with ranked suspects |
| **Contract** | Min 3 suspects, causal mechanism per suspect, grounded quotes |

The LLM produces the final attribution: ranked suspect commits with confidence scores, causal mechanisms ("If X then Y"), and evidence quotes from examined diffs.

After attribution, evidence scoring (script) runs unconditionally to attach grounding metadata.

---

## Agent Governance

### Investigation Harness

The harness is the non-LLM orchestration layer that controls the agent's lifecycle:

- **State management**: Tracks current stage, progress metrics, what's been examined
- **Transition enforcement**: Stage 3 requires valid brief; Stage 4 requires brief satisfaction or budget exhaustion
- **Progress tracking**: "examined 12/20 candidates, 3 hypotheses tested, 1 confirmed"
- **LLM control**: Decides when to invoke LLM, what context to provide, when to stop
- **Completion evaluation**: Checks criteria after each examination turn

### Investigation Rules

Rules encode quality constraints. Examples:
- "Never conclude with fewer than 3 suspects"
- "Always examine parent commits in a change chain"
- "For concurrency bugs, trace thread interactions across commits"
- "If top suspect confidence < 0.6, continue examining"

**TBD:** Enforcement mechanism (hard gates by harness, soft guidance via prompt, or hybrid). To be resolved in `mechanism-design` task.

### Investigation Skills

Strategies that improve over time from investigation traces. Examples:
- "For Spark serialization bugs, blame SerDe files first"
- "When JIRA mentions NPE, pickaxe for null-check removal"
- "Large repos: time-window filtering before file search"

**TBD:** Acquisition and application mechanism (RAG few-shot, rule extraction, or hybrid). To be resolved in `mechanism-design` task.

---

## Completion Criteria

The agent knows what "done" means before starting. The harness evaluates after each examination turn:

| Criterion | Description | Threshold |
|-----------|-------------|-----------|
| Evidence threshold | Grounded quotes across suspects | **TBD** (N >= 3?) |
| Hypothesis coverage | Alternative explanations tested | **TBD** (M >= 2?) |
| Confidence gate | Top suspect confidence sufficient | **TBD** (>= 0.6?) |
| Brief satisfaction | All planned examinations completed or abandoned with reason | Boolean |
| Budget (hard stop) | Total tool calls, tokens, or cost limit exceeded | 30 calls / 100K tokens / $0.50 |

Budget is a **safety net**, not the primary exit signal. The agent exits when the brief is satisfied, not when tokens run out.

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

**Schema TBD** — exact fields and storage format to be resolved in `mechanism-design` task.

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
    max_effort: int                         # max tool calls for examination
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
    evidence_threshold: int                 # min grounded quotes across suspects (TBD)
    hypothesis_coverage: int                # min alternative explanations tested (TBD)
    confidence_gate: float                  # min top suspect confidence (TBD)
    brief_satisfaction: bool = False        # all planned examinations done
    budget_hard_stop: bool = False          # budget exceeded (safety net)
```

### InvestigationTrace (structured investigation record)

```python
@dataclass
class InvestigationTrace:
    hypotheses: list[dict]                  # formed, confirmed, rejected, abandoned (schema TBD)
    candidates_examined: list[str]          # commit SHAs inspected
    candidates_eliminated: list[dict]       # SHA + elimination reason (schema TBD)
    evidence_collected: list[dict]          # quotes + grounding status (schema TBD)
    strategy_decisions: list[dict]          # decision + rationale (schema TBD)
    stage_timings: dict[str, float]         # stage → elapsed_ms
    outcome: dict                           # eval result if available (schema TBD)
```

> **Note:** Field schemas marked TBD will be defined in the `mechanism-design` task.

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

| Aspect | V3 (current) | V4 (target) |
|--------|--------------|-------------|
| Agent receives | `ProblemStatement` + `GitContextProvider` (full repo access) | `CandidateSet` + `ProblemStatement` (pre-filtered) |
| Search | LLM does search via tools (5-8 calls) | Scripts do retrieval (zero LLM) |
| Planning | None — implicit in "Problem Analysis" advisory phase | Explicit `InvestigationBrief` with hypotheses |
| Governance | Budget-only (30 calls, 100K tokens, 15 turns) | Harness + completion criteria + budget as safety net |
| Exit signal | Budget exhaustion | Brief satisfaction (or budget hard stop) |
| Tracing | `tool_trace` (partial, 500-char truncation) | Full `InvestigationTrace` |
| Learning | None | Skills from traces (**TBD**) |
| Best result | Hit@5=0.50, MRR=0.304 (n=20, prompt V2) | Target: Hit@5 >= 0.60 with lower cost |

V3 code lives in `src/commit_investigator/` with packages: `extraction/`, `agent/`, `eval/`, `infra/`. The V3 agentic loop runs 7 advisory stages (5 LLM + 2 script) inside `AgentOrchestrator.investigate()`. Full V3 details in the [exp19b retrospective](../.harness/docs/exp19b-retrospective.md).

---

## Related

| Document | Content |
|----------|---------|
| [agent-loop.md](agent-loop.md) | Detailed agent loop mechanics (stages 2-3-4) |
| [evaluation-framework.md](evaluation-framework.md) | Metrics, stage-to-metric mapping, baselines |
| [glossary.md](glossary.md) | Term definitions |
| [datasets.md](datasets.md) | ApacheJIT data, ground truth chain |
| [.harness/docs/topology-debate.md](../.harness/docs/topology-debate.md) | Architecture Decision Record for V4 |
| [.harness/docs/exp19b-retrospective.md](../.harness/docs/exp19b-retrospective.md) | V2/V3 retrospective with scores and lessons |
