# System Specification — Bug Attribution Agent (V3)

Given a JIRA bug report (title + description) and a temporally-bounded git repository, this system identifies the commit that most likely **introduced** the bug. It produces a ranked list of suspect commits with causal mechanisms and evidence quotes, evaluated against SZZ-derived ground truth.

## Pipeline Overview

The system has three stages with distinct ownership:

```
Stage 1: EVAL SETUP (harness)        Stage 2: INVESTIGATION (LLM + scripts)       Stage 3: EVALUATION (oracle)
┌─────────────────────────┐          ┌─────────────────────────────────────┐       ┌────────────────────────┐
│ JIRA ticket              │          │ ProblemStatement                     │       │ BugAttributionReport    │
│   → ProblemExtractor     │   ───►   │   → 7-stage Agentic Loop            │  ───►  │   → Hit@k / MRR        │
│   → ProblemStatement     │          │     (stages 1-5 LLM, 6-7 script)     │       │   → Retrieval Recall   │
│                          │          │   → BugAttributionReport            │       │   → Evidence Grounding │
│ GroundTruthGraph         │          │                                     │       │                        │
│   → fix_hash → bound     │          │ GitContextProvider                  │       │ Ground truth: bug_hash │
│   → GitContextProvider   │          │   (bounded at COMMIT_B~1)           │       │                        │
└─────────────────────────┘          └─────────────────────────────────────┘       └────────────────────────┘
```

### Stage 1: Eval Setup (harness-owned)

Prepares the investigation context. The agent never sees this stage.

| Step | Module | Input | Output |
|------|--------|-------|--------|
| Load ground truth | `infra/ground_truth.py` | Replication zip (commit_links CSVs) | `GroundTruthGraph` |
| Select eval case | `runners/run_eval.py` | `GroundTruthGraph` + `JiraClient` | `EvalCase` (bug_hash, fix_hash, project, issue_key) |
| Build problem | `context/problem_extractor.py` | `JiraIssue` (summary + description) | `ProblemStatement` |
| Set temporal bound | `context/git_context.py` | fix_hash from eval case | `GitContextProvider` bounded at `fix_hash~1` |

### Stage 2: Investigation (LLM + scripts)

The agent searches the repository and produces a report inside `AgentOrchestrator.investigate()`. This is the only pipeline stage involving an LLM. The **agentic loop** runs from `ProblemStatement` to `BugAttributionReport` as seven stages (stages 1–5 are advisory LLM phases; stages 6–7 are unconditional script steps):

| Stage | Name | Owner | Input | Output |
|-------|------|-------|-------|--------|
| 1 | Problem Analysis | LLM | `ProblemStatement` via `to_prompt_text()` | Implicit LLM reasoning (not a structured artifact) |
| 2 | Search | LLM via tools | Problem understanding + tool catalog | Candidate commit SHAs from search/blame/log tools |
| 3 | Examine | LLM via tools | Candidate commits | Diff content + commit messages |
| 4 | Refine | LLM via tools | Examined diffs + prior reasoning | Narrowed suspects, blame chains |
| 5 | Conclude | LLM | Full investigation context | ` ```suspects``` ` JSON → `list[SuspectCommit]` |
| 6 | Evidence Scoring | Script (`_attach_evidence_scores`) | Suspects + diffs | Per-suspect grounding scores in `metadata` |
| 7 | Report Assembly | Script (inline in `investigate()`) | Suspects + scores + trace + timing | `BugAttributionReport` |

Stages 2–4 share one code path — the multi-turn tool loop in `investigate()`. They are distinguished by typical tool types, not separate enforcement. Stages 6–7 always run after the LLM loop exits, regardless of exit condition. Suspect rank and confidence are **not modified** in stages 6–7 — evidence scores are metadata only.

### Stage 3: Evaluation (oracle-owned)

Compares the report against ground truth. The agent never sees these results.

| Step | Module | Input | Output |
|------|--------|-------|--------|
| Score attribution | `runners/eval_metrics.py` | `BugAttributionReport` + `bug_hash` | `AttributionEvalResult` (Hit@k, MRR, etc.) |
| Run baselines | `runners/baselines.py` | `ProblemStatement` + `GitContextProvider` | Baseline `BugAttributionReport`s |
| Aggregate + compare | `runners/run_eval.py` | All results | `ComparisonReport` (agent vs baselines) |

---

## The LLM Boundary

The LLM operates inside a strict information boundary. Everything outside this boundary is either harness infrastructure or eval oracle data.

### What the LLM sees

| Source | Content | Delivered via |
|--------|---------|---------------|
| Bug report | JIRA title + description (raw text) | `ProblemStatement.to_prompt_text()` as the first user message |
| Git history | Commit log, blame, diffs, file contents — all pre-fix only | Tool results injected as user messages |
| Budget status | Tool calls used / remaining, tokens used | `TURN_PROMPT` template each turn |
| Its own prior responses | Full conversation history | Message list accumulation |

### What the LLM never sees

| Data | Why forbidden |
|------|---------------|
| `bug_hash` (ground truth answer) | Would trivialize the task |
| `fix_hash`, fix commit diff/message | Beyond temporal bound; reveals the fix |
| Ground truth chain (bug→fix→issue linkage) | Retrospective SZZ assignment |
| JIRA `priority`, `components`, `resolution`, `status` | Not part of investigation context |
| `project`, `issue_key`, `extracted_files`, `extracted_symbols` | Stored on `ProblemStatement` but excluded from `to_prompt_text()` |
| Evidence scores | Computed in stages 6–7 inside `investigate()`; never fed back to LLM |
| Eval metrics (Hit@k, MRR) | Oracle-only |

### Prompt structure

The LLM receives exactly this message sequence:

**Turn 1:**
```
messages = [
    {"role": "system",  "content": SYSTEM_PROMPT + tool_descriptions},
    {"role": "user",    "content": "## Bug Report: {title}\n\n{description}"},
]
```

**Turn N (after tool execution):**
```
messages += [
    {"role": "assistant", "content": <LLM's previous response>},
    {"role": "user",      "content": TURN_PROMPT with tool results},
]
```

Tool results are plain text in the user message — **not** `role="tool"` messages. The orchestrator does not use native function-calling APIs; tool dispatch is entirely text-based via markdown fences.

---

## Agentic Loop

The **agentic loop** is the entire investigation inside `AgentOrchestrator.investigate()` — from `ProblemStatement` to `BugAttributionReport`. It is a text-based multi-turn conversation (stages 1–5) followed by unconditional script steps (stages 6–7). Stages 1–5 mirror the advisory strategy in `_SYSTEM_PROMPT_TEMPLATE` (Understand → Search → Examine → Refine → Conclude); they are **not** enforced as sequential gates — the LLM may interleave search, examine, and refine across turns.

### Seven stages

| # | Name | Owner | Input | Output | Evaluation dimension |
|---|------|-------|-------|--------|---------------------|
| 1 | Problem Analysis | LLM | `ProblemStatement` via `to_prompt_text()` | Implicit LLM reasoning (not a structured artifact) | N/A |
| 2 | Search | LLM via tools | Problem understanding + tool catalog | Candidate commit SHAs from search/blame/log tools | N/A (see Retrieval Recall for examine tools) |
| 3 | Examine | LLM via tools | Candidate commits | Diff content + commit messages | Retrieval Recall |
| 4 | Refine | LLM via tools | Examined diffs + prior reasoning | Narrowed suspects, blame chains | Retrieval Recall (continued) |
| 5 | Conclude | LLM | Full investigation context | ` ```suspects``` ` JSON → `list[SuspectCommit]` | Hit@k, MRR, D3 (not yet implemented) |
| 6 | Evidence Scoring | Script | Suspects + diffs | `SuspectEvidenceScore` per suspect in `metadata` | D6 Evidence Grounding |
| 7 | Report Assembly | Script | Suspects + scores + trace + timing | `BugAttributionReport` | Structural correctness (schema tests) |

#### Stage 1 — Problem Analysis

**Code:** `investigate()` builds initial messages with `_SYSTEM_PROMPT_TEMPLATE` and `problem.to_prompt_text()` (`orchestrator.py`).

The LLM reads the bug report and forms an internal understanding (file names, class names, error messages). No structured output is produced — this is implicit reasoning that propagates into later tool calls.

#### Stages 2–4 — Search, Examine, Refine (shared tool loop)

**Code:** `investigate()` loop — `_parse_tool_calls()` → `ToolRegistry.execute()` (`orchestrator.py`).

All three stages run in the **same** multi-turn loop. They are distinguished by typical tool usage, not separate code paths:

| Stage | Typical tools |
|-------|---------------|
| 2 Search | `search_commits_by_file`, `search_commits_by_keyword`, `list_recent_commits` |
| 3 Examine | `get_commit_diff`, `get_commit_message`, `get_file_at_commit` |
| 4 Refine | `get_blame`, `search_commits_by_keyword` (cross-reference) |

The LLM may call any tool in any order within resource limits. There are no stage gates.

#### Stage 5 — Conclude

**Code:** `_parse_suspects(response.content)` — if suspects block found, loop breaks (`orchestrator.py`).

The LLM emits a ` ```suspects``` ` JSON array. `_parse_suspects()` assigns `rank` from array order (1-based). Stage 5 may **not** happen — if budget is exceeded, `max_turns` is reached, or the LLM emits neither tools nor suspects, the loop exits with an empty suspect list. Stages 6–7 still run.

**Precedence:** If a response contains both ` ```suspects``` ` and ` ```tool``` ` blocks, suspects are parsed first — tools in that turn are not executed.

#### Stage 6 — Evidence Scoring

**Code:** `_attach_evidence_scores()` → `score_suspect_evidence()` (`orchestrator.py`, `evidence_tagger.py`).

Always runs after the LLM loop exits, regardless of exit condition (including empty suspects):

1. For each suspect, fetch the commit diff via `git_provider.get_diff()`
2. Call `score_suspect_evidence(commit_id, evidence_quotes, diff)`
3. For each quote, check presence via `quote_in_diff()` / `tag_hypothesis()` cascade:
   - Exact substring match
   - Normalized match (strip `+/-` prefixes, collapse whitespace)
   - Token-set fuzzy match (≥80% of tokens ≥3 chars found in order within 200-char windows)
4. A quote is **grounded** if the cascade finds a match (tier = `SUPPORTED`)
5. `grounding_rate` = grounded_quotes / total_quotes

Scores serialize to `metadata["evidence_scores"]`:
```json
[{"commit_id": "abc...", "total_quotes": 3, "grounded_quotes": 2, "grounding_rate": 0.667}]
```

**Special cases:**
- Diff unavailable → all quotes `UNVERIFIABLE`, rate = 0.0
- Diff truncated (>16K chars) and quote not found → `UNVERIFIABLE`
- Quote < 8 chars → not grounded
- No quotes → rate = 0.0

Evidence scores are **metadata only** — they do **not** modify suspect rank, confidence, or mechanism.

#### Stage 7 — Report Assembly

**Code:** `return BugAttributionReport(...)` (`orchestrator.py`).

Always runs after stage 6. Assembles `problem_title`, `problem_description`, `suspects`, `reasoning_summary` (first 2000 chars of all LLM responses), `tool_trace` (result truncated to 500 chars per record), and `metadata` including `evidence_scoring_applied=True` and `post_processing_applied=False`.

### Loop mechanics

```
for turn in range(max_turns):         # default: 15 (orchestrator counter)
    if budget.budget_exceeded: break  # tokens, cost, or tool_calls

    response = llm.complete(messages)
    budget.record(response)

    if response contains ```suspects```:
        suspects = _parse_suspects(...) → break   # EXIT: stage 5

    tool_calls = _parse_tool_calls(...)
    if not tool_calls:
        break                                   # EXIT: no tools, no suspects

    for call in tool_calls:                      # stages 2-4
        if budget.total_tool_calls >= max_tool_calls:
            append budget-limit message; break  # mid-batch cutoff
        result = registry.execute(...)
        tool_trace.append(...)
    append TURN_PROMPT with tool results
```

### Three exit conditions

| Condition | Result |
|-----------|--------|
| LLM emits a ` ```suspects``` ` block | Suspects parsed from JSON array (stage 5) |
| LLM emits neither tools nor suspects | Empty suspect list |
| Budget exceeded, max_turns reached, or mid-batch tool cutoff | Whatever suspects were last parsed (usually empty) |

Stages 6–7 run unconditionally after any exit.

### Tool calling format

The LLM must emit tool calls as markdown-fenced JSON:

```
I'll search for commits touching the RouteBuilder file.

` ` `tool
{"tool": "search_commits_by_file", "args": {"path": "src/main/java/RouteBuilder.java"}}
` ` `
```

The orchestrator extracts all ` ```tool``` ` blocks via `_TOOL_CALL_PATTERN`, executes them sequentially via `ToolRegistry.execute()`, and returns results as plain text in the next user message.

### Suspect output format

When the LLM concludes (stage 5), it emits:

```
` ` `suspects
[
  {"commit_id": "abc123...", "confidence": 0.8,
   "mechanism": "If <change> then <consequence>",
   "evidence_quotes": ["exact text from diff"]}
]
` ` `
```

Parsing: array order determines `rank` (1-based). `confidence` and `mechanism` are LLM-provided. Missing fields get defaults via `_parse_suspects()`.

### Resource limits (stages 1–5 collectively)

Resource limits apply to the entire LLM loop — not per advisory stage. Budget check runs at the **start** of each turn.

| Resource | Limit | Enforced by |
|----------|-------|-------------|
| Tool calls | 30 | `BudgetState.total_tool_calls`; mid-batch cutoff when limit reached mid-turn |
| Tokens | 100,000 | `BudgetState.budget_exceeded` (from `LLMResponse.tokens_used`) |
| Cost | $0.50 USD | `BudgetState.budget_exceeded` (from `LLMResponse.estimated_cost`) |
| Turns | 15 | Orchestrator `for turn in range(self._max_turns)` — separate from `budget_exceeded` |

When `budget_exceeded` is true at turn start, or `max_turns` is exhausted, the loop exits and stages 6–7 proceed with whatever suspects were collected (often none).

---

## Tool Catalog

All 7 tools wrap `GitContextProvider` methods. Large outputs are truncated at 8,000 characters.

| Tool | Required args | Optional args | What it does |
|------|--------------|---------------|--------------|
| `search_commits_by_file` | `path` | `max_results` (10) | Commits touching a file path. Most recent first. |
| `search_commits_by_keyword` | `keyword` | `max_results` (10) | Case-insensitive search of commit messages. |
| `list_recent_commits` | — | `max_results` (20), `path` | Recent commits, optionally filtered by file. |
| `get_commit_diff` | `commit_id` | — | Unified diff (patch) for a commit. |
| `get_commit_message` | `commit_id` | — | Full commit message text. |
| `get_blame` | `path` | `line_start` (1), `line_end` | Line-level blame at the temporal bound. |
| `get_file_at_commit` | `commit_id`, `path` | — | File contents at a specific commit. |

### Temporal enforcement per tool type

- **Per-commit reads** (`get_commit_diff`, `get_commit_message`, `get_file_at_commit`): `_enforce_bound()` called before returning data. Violation raises `TemporalBoundViolation`, which the orchestrator catches and returns as `"Error: ..."` text to the LLM.
- **History searches** (`search_commits_by_file`, `search_commits_by_keyword`, `list_recent_commits`): temporal bound ref appended to `git log` command, so results are pre-filtered to commits reachable from `COMMIT_B~1`.
- **Blame** (`get_blame`): uses the temporal bound ref (or HEAD if unbounded) as the blame target.

---

## Data Structures

All V3 data structures are defined in `pipeline/orchestrator.py` (not in the V2 `analysis/report.py`).

### ProblemStatement

```python
@dataclass(frozen=True)
class ProblemStatement:
    title: str                              # JIRA summary
    description: str                        # JIRA description (raw)
    project: str                            # e.g. "CAMEL" (not sent to LLM)
    issue_key: str = ""                     # e.g. "CAMEL-1234" (not sent to LLM)
    extracted_files: list[str] = []         # future: Level 2 extractor
    extracted_symbols: list[str] = []       # future: Level 2 extractor
```

`to_prompt_text()` emits only `title` and `description`. All other fields are metadata for the harness.

### SuspectCommit

```python
@dataclass
class SuspectCommit:
    commit_id: str                          # full SHA from LLM output
    rank: int                               # 1-based, from array position
    confidence: float                       # LLM-provided (0.0-1.0)
    mechanism: str                          # "If <change> then <consequence>"
    evidence_quotes: list[str] = []         # exact text from diffs
```

### BugAttributionReport

```python
@dataclass
class BugAttributionReport:
    problem_title: str
    problem_description: str
    suspects: list[SuspectCommit]           # rank-ordered, not modified post-LLM
    reasoning_summary: str                  # first 2000 chars of all LLM responses
    tool_trace: list[ToolCallRecord]        # result field truncated to 500 chars
    metadata: dict[str, Any]                # see below
```

**Metadata keys** (set by `investigate()`):

| Key | Type | Description |
|-----|------|-------------|
| `turns_used` | int | LLM turns consumed |
| `tool_calls` | int | Total tool executions |
| `tokens_used` | int | Total tokens (prompt + completion) |
| `total_cost_usd` | float | Estimated cost |
| `elapsed_ms` | float | Wall-clock time |
| `temporal_bound` | str | The bound ref (e.g. "abc123~1") |
| `model` | str | LLM model name |
| `budget_exceeded` | bool | Whether any limit was hit |
| `evidence_scores` | list[dict] | Per-suspect grounding scores |
| `evidence_scoring_applied` | bool | Always `True` in V3 |
| `post_processing_applied` | bool | Always `False` (Phase B deferred) |

### BudgetState

```python
@dataclass
class BudgetState:
    total_tokens: int = 0
    total_cost: float = 0.0
    total_tool_calls: int = 0
    max_tokens: int = 100_000
    max_cost: float = 0.50
    max_tool_calls: int = 30
    turns_used: int = 0
```

---

## Temporal Model

The agent simulates an engineer at **bug-report time** — after the defect exists but before any fix lands.

```
[bug introduced] --> [bug reported in JIRA] --> agent investigates HERE --> [fix commit(s)]
                         ^ input                    ^ bound = COMMIT_B~1       ^ invisible
```

### Rules

| Rule | Detail |
|------|--------|
| Bound definition | `COMMIT_B~1` = parent of the earliest fix commit |
| Multi-fix policy | Use the **earliest** fix commit SHA as `COMMIT_B` |
| Bound source | `fix_hash` from the eval case sets the bound; this is eval-only data |
| Fix commit invisibility | `COMMIT_B`'s diff, message, and metadata are never accessible |

### Enforcement implementation

Two mechanisms in `GitContextProvider`:

1. **Per-commit guard** (`_enforce_bound`): resolves the requested commit SHA, then checks `git merge-base --is-ancestor <commit> <bound>`. Violation raises `TemporalBoundViolation`. Results cached via `@lru_cache(maxsize=512)`.

2. **Search pre-filtering**: all `git log` commands append the bound ref as a positional argument, limiting traversal to commits reachable from `COMMIT_B~1`.

Edge case: if a commit SHA cannot be resolved (e.g. short hash ambiguity), the bound check is silently skipped rather than raising.

---

## LLM Provider

Factory priority (`get_provider()`):

| Priority | Provider | Model | Trigger |
|----------|----------|-------|---------|
| 1 | CursorSDKProvider | claude-sonnet-4-6 | `CURSOR_API_KEY` set |
| 2 | OpenAIProvider | gpt-4o-mini (or `OPENAI_MODEL`) | `OPENAI_API_KEY` set |
| 3 | OllamaProvider | llama3.1:8b | Local Ollama running |
| 4 | MockLLMProvider | mock-investigator-v1 | Fallback (tests) |

CursorSDKProvider flattens the conversation into a single prompt string. OpenAIProvider supports native function calling but the orchestrator does not use it — all tool dispatch is text-based.

---

## Related

| Document | Content |
|----------|---------|
| [evaluation-framework.md](evaluation-framework.md) | Metrics, rubrics, baselines, thresholds |
| [datasets.md](datasets.md) | ApacheJIT data, ground truth chain, git clones |
| [glossary.md](glossary.md) | Term definitions |
