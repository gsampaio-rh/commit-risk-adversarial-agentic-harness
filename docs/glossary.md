# Glossary

## Core V3 Concepts

| Term | Definition |
|------|------------|
| **ProblemStatement** | Structured bug report from JIRA (title, description). Input to attribution agent. |
| **SuspectCommit** | Candidate bug-introducing commit with rank, mechanism, and evidence. |
| **BugAttributionReport** | Ranked list of suspects with evidence and reasoning trace. |
| **Attribution Agent** | Multi-turn LLM agent that searches git to find the guilty commit. |
| **Problem Extractor** | Eval infrastructure that builds ProblemStatements from JIRA tickets. Not part of the agent runtime — connects problem descriptions with ground truth for evaluation. |
| **Evidence Scorer** | Script verifying evidence quotes against suspect diffs. |
| **GitContextProvider** | Temporally-bounded git access layer. |
| **Temporal Bound** | `COMMIT_B~1` — latest commit the agent may access. |

## Evaluation Metrics

| Term | Definition |
|------|------------|
| **Hit@k** | Is `bug_hash` in agent's top k suspects? Primary: Hit@5. |
| **MRR** | Mean Reciprocal Rank of `bug_hash` in suspect list. |
| **Retrieval Recall** | Did agent fetch `bug_hash` during search? |
| **D3 Attribution** | LLM judge score for mechanism quality (0-4). |
| **D6 Evidence** | Script score for evidence grounding (0-4). |

## Dataset

| Term | Definition |
|------|------------|
| **ApacheJIT** | Labeled commit dataset from Apache projects. |
| **bug_hash** | SHA of bug-introducing commit (SZZ-derived, has noise). |
| **fix_hash** | SHA of fixing commit. Sets temporal bound. |
| **COMMIT_B** | Earliest fix commit. Defines forward edge of bound. |
| **SZZ** | Algorithm tracing fixes to bug-introducing commits. |
| **ground truth chain** | `bug_hash -> fix_hash -> issue_key`. Eval-only. |

## Architecture

| Term | Definition |
|------|------------|
| **oracle isolation** | Ground truth never enters investigation context. |
| **LLM reasons, scripts verify** | Agent searches; scripts verify evidence. |

## Related

- [temporal-model.md](temporal-model.md) — boundary rules
- [evaluation.md](evaluation.md) — metric rubrics
- [agent-loop.md](agent-loop.md) — search phases
