# System Specification — Bug Attribution Agent (V4.2 Architecture)

Given a JIRA bug report (title + description) and a temporally-bounded git repository, this system identifies the commit that most likely **introduced** the bug. It produces a ranked list of suspect commits with causal mechanisms and evidence quotes, evaluated against SZZ-derived ground truth.

> **Architecture status:** **V4.2** (Revised Hierarchical Pipeline) is the current, proven architecture — separates narrowing from deep investigation via a 4-phase pipeline: retrieval → script pre-score → deterministic triage → scoped investigation (+ conditional watchlist expansion). Proven at Hit@5=0.800, MRR=0.600 with Cursor SDK (n=5). See [.harness/docs/v42-architecture-adr.md](../.harness/docs/v42-architecture-adr.md) for the V4.2 decision, [.harness/docs/architecture-constraints.md](../.harness/docs/architecture-constraints.md) for codified NFRs, [.harness/docs/scoped-tools-adr.md](../.harness/docs/scoped-tools-adr.md) for the V4→V4.1 pivot.

---

## Three Pipelines

The system consists of three pipelines with distinct ownership and clear boundaries:

```
┌──────────────────────────────────────────┐
│  INPUT PIPELINE (infrastructure)         │
│  Phase 0:  Extraction                    │
│  Phase 1a: Retrieval + Script Pre-Score  │
│  Phase 1b: Deterministic Triage          │
│  Owner: Scripts / eval harness           │
│  LLM cost: Zero                          │
└──────────────┬───────────────────────────┘
               │ TriageResult + ProblemStatement
               ▼
┌──────────────────────────────────────────┐
│  AGENT PIPELINE (governed LLM)           │
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
| Input | 0 (Extraction) + 1a (Pre-Score) + 1b (Deterministic Triage) | Scripts / eval harness | Zero (optional LLM for Level 2 extraction) | Nothing — this is input preparation |
| Agent | 2 (Investigation) + 2b (Watchlist expansion) | Investigation harness (harness governs LLM) | Full — all LLM budget spent here | TriageResult + ProblemStatement |
| Evaluation | Scoring against ground truth | Oracle (eval harness) | Zero (except D3 LLM judge) | Nothing — scores are oracle-only |

---

## Input Pipeline (Stages 0–1b)

The input pipeline prepares everything the agent needs to begin reasoning. It is infrastructure — not part of the agent's cognitive loop. All stages are deterministic (zero LLM cost).

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
| **Module** | `retrieval/pipeline.py` + `retrieval/retriever.py` + `retrieval/strategies.py` |

Retrieval uses deterministic git operations to assemble a candidate set:
- File-based: `git log --all -- <extracted_files>` bounded by temporal ref
- Keyword-based: `git log --all --grep="<keyword>"` bounded by temporal ref
- Pickaxe: `git log --all -S "<symbol>"` for code-level search
- Time-window: recent commits within N months of bug report
- Blame-based: `git blame <bound> -- <file>` to find line-level authorship

**Fallback:** If extraction yields zero signals, retrieval widens to broad heuristics (recent commits, large diffs). If candidate set is too small (< 10), retrieval parameters are loosened.

All retrieval respects the temporal bound — only commits reachable from `COMMIT_B~1`.

### Stage 1b — Deterministic Triage

| Aspect | Detail |
|--------|--------|
| **Owner** | Script (deterministic, zero LLM) |
| **Input** | `ScoredShortlist` (top 15 from Phase 1a) |
| **Output** | `TriageResult`: 3 must-examine + 4 watchlist |
| **Rule** | must_examine = top 3 by pre_score; watchlist = next 4 |
| **Module** | `narrowing/triage.py` |

Triage assigns fixed tiers by pre-score rank. No LLM call — the triage smoke test showed deterministic top-7 achieves TriageRecall@7 = 1.00 on all retrievable cases. See [V4.2 ADR](../.harness/docs/v42-architecture-adr.md) for reintroduction trigger if a larger dataset requires LLM-assisted triage.

---

## Agent Pipeline — V4.2 Revised Hierarchical (Current)

The agent receives a `TriageResult` + `ProblemStatement` from the input pipeline and produces a ranked suspect list. It uses scoped investigation with conditional watchlist expansion. See [V4.2 ADR](../.harness/docs/v42-architecture-adr.md).

> **Note:** Phase 1b (triage) is deterministic and lives in the Input Pipeline. The Agent Pipeline starts at Phase 2.

### Phase 2: Scoped Investigation (multi-turn ReAct)

| Aspect | Detail |
|--------|--------|
| **Owner** | `RevisedScopedInvestigator` (harness) + LLM |
| **Input** | Bug report + must-examine candidates (SHA + pre-score rank) + scoped tools |
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

### Suspect (output)

```python
@dataclass
class Suspect:
    """Ranked attribution suspect from Phase 2 investigation.
    Replaces historical SuspectCommit. Defined in harness/result.py.
    """
    commit_id: str                          # full SHA
    rank: int = 0                           # 1-based
    confidence: float = 0.0                 # 0.0-1.0
    mechanism: str = ""                     # "If <change> then <consequence>"
    evidence_quotes: list[str] = field(default_factory=list)  # exact text from diffs
    phase: str = "investigation"            # phase that produced this suspect
    tools_used: list[str] = field(default_factory=list)       # tools invoked during examination
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

## Architecture Evolution (Reference)

| Aspect | V3 (baseline, deleted) | V4.1 (historical baseline) | V4.2 (current, proven) |
|--------|------------------------|----------------------|---------------|
| Agent receives | Full repo access | 20 candidates + scoped tools | 7 triaged candidates + scoped tools |
| Search | LLM via tools (5-8 calls) | Scripts (zero LLM) | Scripts (zero LLM) |
| Narrowing | None (LLM searches) | None (all 20 in prompt) | Script pre-score + deterministic triage |
| Governance | Budget-only (30/15) | Budget (15/8) + SHA validation | Phase-aware budget + nudge ladder |
| Best result | Hit@5=0.50, MRR=0.304 | TBD | Hit@5=0.800 (Cursor SDK n=5), 0.250 (local gemma3:12b n=20) |

V3 code was deleted during cleanup. V3 details preserved in the [exp19b retrospective](../.harness/docs/exp19b-retrospective.md).

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
