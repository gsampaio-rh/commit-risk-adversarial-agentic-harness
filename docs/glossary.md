# Glossary

## Architecture (V4.2 — Target)

| Term | Definition |
|------|------------|
| **Input Pipeline** | Phase 0 + Phase 1a: deterministic extraction, candidate retrieval, and script pre-scoring at zero LLM cost. Produces `ScoredShortlist` + `ProblemStatement` for the agent. |
| **Agent Pipeline (V4.2)** | Phases 1b-2-2b: LLM triage → scoped investigation → conditional watchlist expansion. Separates narrowing from deep investigation. |
| **Evaluation Pipeline** | Oracle scoring: compares suspect list against ground truth. The agent never sees these results. 5-stage funnel: Recall@100→@15→@7→Exam→Hit@5. |

## V4.2 Revised Hierarchical Pipeline

| Term | Definition |
|------|------------|
| **Phase 1a: Script Pre-Score** | Deterministic scoring of CandidateSet using file_overlap, signal_count, and retrieval_rank. Produces `ScoredShortlist` (top 15). Zero LLM cost. |
| **Phase 1b: LLM Triage** | One-shot LLM call that ranks 15 candidates into 3 must-examine + 4 watchlist. Top 3 by pre_score are harness-pinned (LLM cannot veto). |
| **Phase 2: Scoped Investigation** | Multi-turn ReAct loop with scoped tools. Examines must-examine candidates. Budget: 15 calls, 8 turns. |
| **Phase 2b: Watchlist Expansion** | Conditional phase triggered when Phase 2 produces no suspects, low confidence, or no evidence. Fresh context with watchlist candidates. Budget: 8 calls, 4 turns. |
| **ScoredShortlist** | Top 15 candidates from Phase 1a, sorted by composite pre_score. Includes temporal_bound and scoring_weights for reproducibility. |
| **ScoredCandidate** | Wrapper around `CandidateCommit` adding `pre_score` and `file_overlap` from Phase 1a scoring. |
| **TriageResult** | Output of Phase 1b: 3 `TriagedCandidate` in must_examine + 4 in watchlist. Fixed tier sizes. |
| **TriagedCandidate** | A `ScoredCandidate` with tier assignment (must_examine/watchlist), triage_rank, and 1-line LLM rationale. |
| **Script-Anchored Triage** | Design constraint: LLM triage cannot override retrieval's top 3 by pre_score. Harness pins them in must_examine regardless of LLM output. |
| **Nudge Ladder** | 4-tier state-based nudge system: idle turn 1 (gentle), idle turn 2 (budget warning), idle turn 3 (force conclude), suspects without diff (reject). |
| **InvestigationExitReason** | Enum tracking why an investigation ended: normal, budget_exhausted, max_turns, forced_conclude, stall, provider_error, empty_candidates, watchlist_expansion_exhausted, watchlist_skipped. |
| **Harness-Managed Context** | Context model where the harness maintains a rolling working summary (≤2K tokens) + last-turn tool results, rather than unbounded message accumulation. |

## V4.1 Scoped Tools (Current Implementation)

| Term | Definition |
|------|------------|
| **ScopedInvestigator** | Multi-turn loop in `harness/scoped_runner.py`. Assembles system prompt with 20 candidates + tool descriptions, dispatches tool calls, parses suspects. V4.1 architecture — predecessor to V4.2. |
| **Scoped Tools** | Examination tools registered via `build_scoped_tools()` in `agent/tools.py`. SHA-taking tools validate against `CandidateSet` before execution. Includes `get_commit_diff`, `get_commit_message`, `get_file_at_commit`, `get_blame`. |
| **SHA Validator** | `_build_sha_validator()` — builds a closure that checks 12-char SHA prefixes against the CandidateSet. Returns error message for out-of-set SHAs, `None` for valid ones. |
| **CandidateSet** | Ranked set of 50-100 commits produced by the input pipeline's retrieval stage. The agent's tools are scoped to this set — it does not search the full repo. |
| **CandidateCommit** | One entry in a `CandidateSet`: commit SHA, retrieval rank, retrieval signal (why retrieved), summary, files changed, optional `diff_summary`. |

## Pipeline Phases

| Term | Definition |
|------|------------|
| **Phase 0: Extraction** | Input pipeline. Transforms raw JIRA text into structured `ProblemStatement`. Regex-based (Level 1) or LLM-assisted (Level 2, TBD). |
| **Phase 1a: Retrieval + Pre-Score** | Input pipeline. Deterministic git commands assemble `CandidateSet`, then script pre-score produces `ScoredShortlist` (top 15). Zero LLM cost. |
| **Phase 1b: LLM Triage** | Agent pipeline. One-shot LLM ranking of 15 candidates into tiered list (3 must-examine + 4 watchlist). |
| **Phase 2: Scoped Investigation** | Agent pipeline. Multi-turn ReAct loop with scoped tools on must-examine candidates. |
| **Phase 2b: Watchlist Expansion** | Agent pipeline. Conditional expansion when Phase 2 confidence is low or no suspects found. |

## Data Structures

| Term | Definition |
|------|------------|
| **ProblemStatement** | Structured bug report (title + description + extraction signals). Input to the agent pipeline. Only `title` and `description` are sent to the LLM. |
| **Suspect** | Unified suspect type (replaces `SuspectCommit` + dict suspects). Includes commit_id, rank, confidence, mechanism, evidence_quotes, phase, tools_used. |
| **SuspectCommit** | Suspect with commit_id, rank, confidence, mechanism, evidence_quotes. Output of investigation. |
| **InvestigationResult** | V4.2 eval-facing result: issue_key, suspects, exit_reason, retrieval_recall, trace, elapsed_s. |
| **ProblemExtractor** | Input pipeline infrastructure that builds `ProblemStatement` from JIRA tickets. |
| **GitContextProvider** | Temporally-bounded git access layer wrapping `git` CLI. All tools and retrieval route through this. |
| **ToolRegistry** | Registry of examination tools. `build_scoped_tools()` creates registries scoped to CandidateSet. Tools are text-based (markdown fences), not native function calling. |

## Temporal Model

| Term | Definition |
|------|------------|
| **Temporal Bound** | `COMMIT_B~1` — the parent of the earliest fix commit. Constrains the entire system (input pipeline + agent tools). |
| **COMMIT_B** | The earliest fix commit SHA. Defines the forward edge of the investigation boundary. Its diff, message, and metadata are invisible. |
| **TemporalBoundViolation** | Exception raised when a tool or retrieval command attempts to access a commit beyond the temporal bound. |
| **Oracle isolation** | Ground truth data (bug_hash, fix_hash, chain linkage, eval metrics) never enters the investigation context. |

## Evaluation — System-Level Metrics

| Term | Definition |
|------|------------|
| **Hit@k** | Binary metric: is the ground truth `bug_hash` in the agent's top k suspects? Primary: Hit@5. |
| **MRR** | Mean Reciprocal Rank: average of `1/rank` across cases. 0 if `bug_hash` not found. |
| **Retrieval Recall@100** (input pipeline) | Is `bug_hash` in the `CandidateSet`? Measures input pipeline quality. |
| **Pre-score Recall@15** (Phase 1a) | Is `bug_hash` in the `ScoredShortlist`? Measures pre-score quality. V4.2 funnel metric. |
| **Triage Recall@7** (Phase 1b) | Is `bug_hash` in must_examine ∪ watchlist? Measures triage quality. V4.2 funnel metric. |
| **Examination Recall** (Phase 2) | Did the agent call `get_commit_diff` on `bug_hash`? Measures examination quality. |

## Evaluation — Output-Quality Metrics

| Term | Definition |
|------|------------|
| **D6 Evidence Grounding** | Script-computed: fraction of evidence quotes grounded in actual commit diffs. Measures hallucination rate. |
| **D3 Attribution Quality** | LLM-judge (0-4 scale): does the causal mechanism correctly explain how the suspect introduced the bug? |
| **5-Stage Funnel** | Recall@100 → Recall@15 → TriageRecall@7 → ExamRecall → Hit@5. Localizes failures to specific pipeline phases. V4.2. |

## Dataset

| Term | Definition |
|------|------------|
| **ApacheJIT** | Labeled commit dataset from 14 Apache projects (Keshavarz & Nagappan, MSR 2022). |
| **bug_hash** | SHA of bug-introducing commit. SZZ-derived — has known noise (35% estimated noise rate). |
| **fix_hash** | SHA of the fixing commit. Sets the temporal bound. |
| **ground truth chain** | `bug_hash → fix_hash → issue_key → JIRA metadata`. Eval-only. |
| **SZZ** | Algorithm tracing fix commits back to bug-introducing commits via `git blame`. |
| **GroundTruthGraph** | In-memory graph from the replication zip. Chain lookup, project enumeration, bug/fix relationships. |

## Architecture Principles

| Term | Definition |
|------|------------|
| **LLM reasons, scripts retrieve** | Scripts own retrieval and verification; LLM owns examination reasoning and attribution. |
| **Scoped, not unbounded** | Agent's tools restricted to CandidateSet SHAs. No full-repo search. |
| **Observable by design** | Every investigation produces a structured trace. No black boxes. |
| **Separate narrowing from investigation** | V4.2 principle: triage (Phase 1a/1b) and deep investigation (Phase 2) are separate phases with separate contexts. |
| **Script-anchored, not LLM-vetoed** | Harness pins retrieval's top candidates into must-examine regardless of LLM triage output. |
| **Baselines** | Zero-LLM deterministic methods (git-blame-naive, file-history-recency, random) that establish the performance floor. |

## Related

- [system-specification.md](system-specification.md) — three pipelines, data structures, LLM boundary
- [agent-loop.md](agent-loop.md) — V4.2 agent loop mechanics
- [evaluation-framework.md](evaluation-framework.md) — metrics, 5-stage funnel, baselines
- [datasets.md](datasets.md) — ApacheJIT data, ground truth chain
