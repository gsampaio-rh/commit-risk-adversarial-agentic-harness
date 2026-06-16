# Glossary

## Three Pipelines (V4 Architecture)

| Term | Definition |
|------|------------|
| **Input Pipeline** | Stages 0-1: deterministic extraction and candidate retrieval at zero LLM cost. Produces `CandidateSet` + `ProblemStatement` for the agent. Not part of the agent's reasoning loop. |
| **Agent Pipeline** | Stages 2-3-4: the LLM plans, examines, and attributes, governed by the investigation harness. Receives `CandidateSet` as input, produces `BugAttributionReport`. |
| **Evaluation Pipeline** | Oracle scoring: compares `BugAttributionReport` against ground truth. The agent never sees these results. |

## Agent Framework

| Term | Definition |
|------|------------|
| **Investigation Harness** | The non-LLM orchestration layer that governs the agent's lifecycle. Manages state, enforces transitions, evaluates completion criteria, and controls when the LLM is invoked. The LLM does not self-govern. |
| **Investigation Rules** | Codified knowledge about investigation quality (e.g., "minimum 3 suspects," "always check parent commits in a chain"). Mechanism TBD: hard gates, soft guidance, or hybrid. |
| **Investigation Skills** | Learned strategies from past investigations that augment the agent's planning. Mechanism TBD: RAG few-shot, rule extraction from traces, or hybrid. |
| **InvestigationBrief** | Structured output of Stage 2 (Planning). Contains hypotheses, examination plan, success criteria, and strategy. Defines what "done" means for this investigation. Named "brief" (not "contract") to avoid collision with `.harness/contract.json`. |
| **InvestigationState** | Harness-managed state tracking: current stage, candidates examined, hypotheses tested, evidence collected, re-plan count, budget usage. |
| **InvestigationTrace** | Full structured record of one investigation: hypotheses formed/tested, candidates examined/eliminated, evidence collected, strategy decisions, outcome. The substrate from which skills emerge. |
| **CompletionCriteria** | Conditions that define when an investigation is "done": evidence threshold, hypothesis coverage, confidence gate, brief satisfaction. Budget is a hard stop, not the primary criterion. |
| **CandidateSet** | Ranked set of 50-100 commits produced by the input pipeline's retrieval stage. The agent's examination is scoped to this set — it does not search the full repo. |
| **CandidateCommit** | One entry in a `CandidateSet`: commit SHA, retrieval rank, retrieval signal (why retrieved), summary, files changed. |

## Pipeline Stages

| Term | Definition |
|------|------------|
| **Stage 0: Extraction** | Input pipeline. Transforms raw JIRA text into structured `ProblemStatement` with extracted files, symbols, keywords. Regex-based (Level 1) or LLM-assisted (Level 2, TBD). |
| **Stage 1: Retrieval** | Input pipeline. Deterministic git commands that assemble `CandidateSet` from `ProblemStatement` signals. Zero LLM cost. Respects temporal bound. |
| **Stage 2: Planning** | Agent pipeline. LLM produces `InvestigationBrief` with hypotheses and examination plan. Governed by harness. |
| **Stage 3: Examination** | Agent pipeline. LLM examines candidates via tools, testing hypotheses from the brief. Governed by harness + rules. |
| **Stage 4: Attribution** | Agent pipeline. LLM produces final ranked suspect list with causal mechanisms. Evidence scoring (script) runs post-attribution. |

## Data Structures

| Term | Definition |
|------|------------|
| **ProblemStatement** | Structured bug report (title + description + extraction signals). Input to the agent pipeline. Only `title` and `description` are sent to the LLM; extraction fields and metadata are used by the input pipeline and harness. |
| **SuspectCommit** | Candidate bug-introducing commit with rank, confidence, mechanism, and evidence quotes. Produced by the LLM in Stage 4. |
| **BugAttributionReport** | Final pipeline output: ranked suspects, reasoning summary, tool trace, metadata (evidence scores, cost, model), and investigation trace. |
| **Attribution Agent** | The governed LLM system that plans, examines, and attributes (Stages 2-3-4). Operates within an `InvestigationBrief`, governed by the investigation harness. |
| **Evidence Scorer** | Script that verifies evidence quotes against commit diffs via exact, normalized, and fuzzy matching. Runs post-attribution in Stage 4. |
| **ProblemExtractor** | Input pipeline infrastructure that builds `ProblemStatement` from JIRA tickets. Level 1: regex pass-through. Level 2: LLM-assisted extraction (TBD). |
| **GitContextProvider** | Temporally-bounded git access layer wrapping `git` CLI. All tools and retrieval route through this. |
| **ToolRegistry** | Registry of examination tools available in Stage 3. Tools are text-based (markdown fences), not native function calling. |

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
| **Retrieval Recall@100** (input pipeline) | Is `bug_hash` in the `CandidateSet`? Measures input pipeline quality independently of the agent. |
| **Retrieval Recall** (agent) | Did the agent examine `bug_hash` via a tool call during Stage 3? Measures agent examination quality. |

## Evaluation — Output-Quality Metrics

| Term | Definition |
|------|------------|
| **D6 Evidence Grounding** | Script-computed: fraction of evidence quotes grounded in actual commit diffs. Measures hallucination rate. |
| **D3 Attribution Quality** | LLM-judge (0-4 scale): does the causal mechanism correctly explain how the suspect introduced the bug? Implemented in `eval/d3_judge.py`. |
| **Plan Quality** | Concept (TBD): does the `InvestigationBrief` target the right area of the codebase? Mechanism to be resolved. |

## Dataset

| Term | Definition |
|------|------------|
| **ApacheJIT** | Labeled commit dataset from 14 Apache projects (Keshavarz & Nagappan, MSR 2022). ~58K bug commits, ~38K fix commits. |
| **bug_hash** | SHA of bug-introducing commit. SZZ-derived — has known noise (35% estimated noise rate). |
| **fix_hash** | SHA of the fixing commit. Sets the temporal bound. |
| **ground truth chain** | `bug_hash → fix_hash → issue_key → JIRA metadata`. Eval-only — never enters investigation context. |
| **SZZ** | Algorithm that traces fix commits back to bug-introducing commits via `git blame`. Foundational for ApacheJIT but produces noisy labels. |
| **commit_links CSV** | Per-project files in the ApacheJIT replication zip mapping `fix_hash → bug_hash`. |
| **GroundTruthGraph** | In-memory graph loaded from the replication zip. Provides chain lookup, project enumeration, and bug/fix relationships. |

## Architecture

| Term | Definition |
|------|------------|
| **LLM reasons, scripts retrieve** | V4 design principle: scripts own retrieval (candidate assembly) and verification; LLM owns planning, reasoning, and attribution. |
| **Plan-driven, not budget-driven** | The agent exits when the `InvestigationBrief` is satisfied, not when budget runs out. Budget is a hard stop safety net. |
| **Harness governs LLM** | The investigation harness controls lifecycle, transitions, and completion. The LLM executes within boundaries set by the harness. |
| **Observable by design** | Every investigation produces a structured trace. No investigation is a black box. |
| **Baselines** | Zero-LLM deterministic methods (git-blame-naive, file-history-recency, random) that establish the performance floor. |

## Related

- [system-specification.md](system-specification.md) — three pipelines, agent framework, data structures, LLM boundary
- [agent-loop.md](agent-loop.md) — agent loop stages 2-3-4, completion criteria, tracing
- [evaluation-framework.md](evaluation-framework.md) — metrics, rubrics, thresholds
- [datasets.md](datasets.md) — ApacheJIT data, ground truth chain
