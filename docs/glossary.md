# Glossary

## Pipeline

| Term | Definition |
|------|------------|
| **ProblemStatement** | Structured bug report (title + description). Input to the attribution agent. Only `title` and `description` are sent to the LLM; `project`, `issue_key`, and extraction fields are harness metadata. |
| **SuspectCommit** | Candidate bug-introducing commit with rank, confidence, mechanism, and evidence quotes. Produced by the LLM, parsed from ` ```suspects``` ` JSON. |
| **BugAttributionReport** | Final pipeline output: ranked suspects, reasoning summary, tool trace, and metadata (evidence scores, cost, model). |
| **Attribution Agent** | Multi-turn LLM agent that searches a temporally-bounded git repository to find the commit that introduced a reported bug. Implemented in `AgentOrchestrator`. |
| **Evidence Scorer** | Script that verifies evidence quotes against commit diffs via exact, normalized, and fuzzy matching. Runs post-loop inside `investigate()`. |
| **ProblemExtractor** | Eval infrastructure that builds `ProblemStatement` from JIRA tickets. Level 1: raw pass-through. Not part of the agent's runtime. |
| **GitContextProvider** | Temporally-bounded git access layer wrapping `git` CLI. All tools route through this. |
| **ToolRegistry** | Registry of 7 git tools available to the agent. Tool calls are text-based (markdown fences), not native function calling. |

## Temporal Model

| Term | Definition |
|------|------------|
| **Temporal Bound** | `COMMIT_B~1` — the parent of the earliest fix commit. The latest commit the agent may access. |
| **COMMIT_B** | The earliest fix commit SHA. Defines the forward edge of the investigation boundary. Its diff, message, and metadata are invisible to the agent. |
| **TemporalBoundViolation** | Exception raised when a tool attempts to access a commit beyond the temporal bound. Caught by the orchestrator and returned as error text to the LLM. |
| **Oracle isolation** | Ground truth data (bug_hash, fix_hash, chain linkage, eval metrics) never enters the investigation context. |

## Evaluation — System-Level Metrics

| Term | Definition |
|------|------------|
| **Hit@k** | Binary metric: is the ground truth `bug_hash` in the agent's top k suspects? Primary: Hit@5. |
| **MRR** | Mean Reciprocal Rank: average of `1/rank` across cases. 0 if `bug_hash` not found. |
| **Retrieval Recall** | Binary metric: did the agent ever fetch `bug_hash` via any tool call during its search? Measures search quality independent of ranking. |

## Evaluation — Output-Quality Metrics

| Term | Definition |
|------|------------|
| **D6 Evidence Grounding** | Script-computed metric: fraction of evidence quotes that are grounded in actual commit diffs. Measures hallucination rate. |
| **D3 Attribution Quality** | LLM-judge metric (0-4 scale): does the causal mechanism correctly explain how the suspect introduced the bug? Not yet implemented. Rubric defined in [evaluation-framework.md](evaluation-framework.md). |

## Dataset

| Term | Definition |
|------|------------|
| **ApacheJIT** | Labeled commit dataset from 14 Apache projects (Keshavarz & Nagappan, MSR 2022). ~58K bug commits, ~38K fix commits. |
| **bug_hash** | SHA of bug-introducing commit. SZZ-derived — has known noise (format changes, refactoring mislabeled as bugs). |
| **fix_hash** | SHA of the fixing commit. Sets the temporal bound. |
| **ground truth chain** | `bug_hash → fix_hash → issue_key → JIRA metadata`. Eval-only — never enters investigation context. |
| **SZZ** | Algorithm that traces fix commits back to bug-introducing commits via `git blame`. Foundational for ApacheJIT labeling but produces noisy labels. |
| **commit_links CSV** | Per-project files in the ApacheJIT replication zip mapping `fix_hash → bug_hash`. |
| **GroundTruthGraph** | In-memory graph loaded from the replication zip. Provides chain lookup, project enumeration, and bug/fix relationships. |

## Architecture

| Term | Definition |
|------|------------|
| **LLM reasons, scripts verify** | Design principle: the LLM drives search and attribution; scripts verify evidence grounding and compute metrics. |
| **Three-stage pipeline** | Stage 1: Eval Setup (harness). Stage 2: Investigation (LLM + scripts). Stage 3: Evaluation (oracle). |
| **Baselines** | Zero-LLM deterministic methods (git-blame-naive, file-history-recency) that establish the performance floor the agent must beat. |

## Related

- [system-specification.md](system-specification.md) — pipeline, LLM boundary, agent loop
- [evaluation-framework.md](evaluation-framework.md) — metrics, rubrics, thresholds
- [datasets.md](datasets.md) — ApacheJIT data, ground truth chain
