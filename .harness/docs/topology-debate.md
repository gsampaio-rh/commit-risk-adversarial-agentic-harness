# ADR: Architecture V4 — Topology and Boundaries

> **SUPERSEDED** by [V4.2 Architecture ADR](v42-architecture-adr.md) (2026-06-17). Retained for historical context only.

**Status:** Superseded  
**Date:** 2026-06-16  
**Deciders:** Builder + Evaluator (adversarial debate)  
**Context:** V3 achieved Hit@5=0.50, MRR=0.304 with a fully-agentic approach where the LLM performs both search and reasoning within a budget-limited loop. This ADR proposes the V4 architecture that separates retrieval from reasoning and introduces agent governance.

---

## 1. Context

### What V3 does today

The V3 agent receives a `ProblemStatement` (JIRA title + description) and a temporally-bounded `GitContextProvider`. It executes a multi-turn tool loop where the LLM:
- Decides what to search for (5-8 tool calls on search)
- Examines candidate commits (10-15 tool calls on diffs/blame)
- Concludes with a ranked suspect list

Budget is the only exit signal: 30 tool calls, 100K tokens, $0.50, 15 turns.

### Why V3 hits a ceiling

1. **Search is wasted LLM budget** — The LLM spends 25-30% of its tool budget on mechanical search that deterministic scripts could do better and faster.
2. **No planning** — The agent goes from "read bug report" to "start searching" with no explicit investigation plan. It cannot self-assess whether it's making progress.
3. **Budget-driven exit** — The agent doesn't know what "done" means. It stops when tokens run out, not when it has sufficient evidence.
4. **No learning** — Investigations are black boxes. No structured record enables improvement over time.
5. **No governance** — The LLM self-governs. It can ignore the "advisory phases" entirely with no consequence.

### What we want

An architecture where:
- Retrieval is deterministic and fast (zero LLM cost)
- The LLM receives a curated candidate set and focuses 100% on reasoning
- An investigation brief defines what "done" means before the LLM starts
- A harness governs the LLM's lifecycle and transitions
- Every investigation produces a structured trace for learning

---

## 2. Decision: Three Pipelines with Clear Boundaries

### Architecture

The system consists of three pipelines with distinct ownership:

| Pipeline | Responsibility | Owner | LLM cost |
|----------|---------------|-------|----------|
| **Input Pipeline** | Prepare investigation inputs (extract signals, retrieve candidates) | Scripts / eval harness | Zero |
| **Agent Pipeline** | Plan, examine, and attribute (the intellectual work) | Agent framework (harness + LLM) | Full budget |
| **Evaluation Pipeline** | Score output against ground truth | Oracle (eval harness) | Zero (except D3 judge) |

### Agent boundary

The **agent** begins at Stage 2 (Planning). It receives:
- `CandidateSet` — 50-100 ranked commits from the input pipeline
- `ProblemStatement` — structured bug report

It produces:
- `BugAttributionReport` — ranked suspects with evidence

Everything before Stage 2 is input preparation. Everything after Stage 4 is evaluation. The agent owns only the reasoning loop.

### Stage breakdown

| Stage | Pipeline | Owner | Input | Output |
|-------|----------|-------|-------|--------|
| 0: Extraction | Input | Script (regex + optional LLM) | Raw JIRA text | `ProblemStatement` with search signals |
| 1: Retrieval | Input | Script (git commands) | `ProblemStatement` + repo + temporal bound | `CandidateSet` |
| 2: Planning | Agent | LLM, governed by harness | `CandidateSet` + `ProblemStatement` + skills | `InvestigationBrief` |
| 3: Examination | Agent | LLM + tools, governed by harness + rules | `InvestigationBrief` + candidate data | Evidence collected, hypotheses tested |
| 4: Attribution | Agent | LLM, governed by harness | Evidence + reasoning | `BugAttributionReport` |
| — Scoring | Evaluation | Oracle | Report + ground truth | Hit@k, MRR, D3, D6 |

---

## 3. Decision: Agent Governance (Harness, Rules, Skills)

The LLM is the executor, not the governor. The agent framework provides three governance layers:

### Investigation Harness (lifecycle owner)

- Manages state: tracks current stage, progress, remaining work
- Enforces transitions: Stage 4 requires brief satisfaction; Stage 2 requires valid `CandidateSet`
- Controls the LLM: decides when to invoke, what context to provide, when to stop
- Evaluates completion criteria after each examination turn

### Investigation Rules (quality constraints)

Codified knowledge about what constitutes a good investigation. Examples:
- "Minimum 3 suspects before attribution"
- "Always examine parent commits in a change chain"
- "For concurrency bugs, trace thread interactions"

**Mechanism:** Hybrid — hard gates + soft prompt guidance. See [mechanism-design ADR §Q1](mechanism-design.md#2-q1--rules-mechanism).

### Investigation Skills (learned strategies)

Strategies that improve over time from investigation traces. Examples:
- "For Spark serialization bugs, blame SerDe files first"
- "When JIRA mentions NPE, pickaxe for null-check removal"

**Mechanism:** Hybrid — keyword retrieval + manual curation. See [mechanism-design ADR §Q2](mechanism-design.md#3-q2--skills-mechanism).

---

## 4. Decision: InvestigationBrief (not "contract")

The agent's planning output is named **InvestigationBrief** to avoid collision with `.harness/contract.json` (the builder/evaluator development contract).

A brief is what you hand an investigator: what to look for, what hypotheses to test, how to know you're done.

| Field | Type | Description |
|-------|------|-------------|
| `hypotheses` | list[Hypothesis] | Falsifiable statements about what might have caused the bug |
| `examination_plan` | list[ExaminationStep] | Specific commits/files to examine and what to look for |
| `success_criteria` | CompletionCriteria | When the investigation is "done" |
| `strategy` | str | Overall approach (e.g., "blame-chain analysis on file X") |
| `max_effort` | int | Maximum examination tool calls before forced conclusion |

---

## 5. Decision: Completion Criteria (replaces budget-only exit)

The agent knows what "done" means before starting. The harness evaluates these after each examination turn:

| Criterion | Description | Threshold |
|-----------|-------------|-----------|
| Evidence threshold | Grounded quotes across suspects | **3** |
| Hypothesis coverage | Alternative explanations tested | **2** |
| Confidence gate | Top suspect confidence | **0.60** |
| Default max_effort | Examination tool calls per brief | **18** |
| Brief satisfaction | All planned examinations completed or explicitly abandoned | Boolean |

**Budget remains as HARD STOP.** If criteria are not met but budget is exhausted, the agent proceeds to attribution in degraded mode. This is logged in the trace.

Threshold values decided in [mechanism-design ADR §Q5](mechanism-design.md#6-q5--completion-threshold-values). Retrieval Recall@100 targets remain **`retrieval-spike` scope**.

---

## 6. Decision: Temporal Model Expansion

The temporal bound (COMMIT_B~1) constrains the ENTIRE system:

| Context | Bound source | Applies to |
|---------|-------------|------------|
| Eval mode | `fix_hash` from ground truth | All stages (0-1-2-3-4) |
| Production mode (future) | Bug report date or HEAD | All stages |

Stage 1 (Retrieval) must respect the temporal bound — all `git log` commands use the bound as a ref. This is unchanged in principle from V3 but now explicitly scoped to the input pipeline.

---

## 7. Decision: Fallback and Degradation Paths

| Failure mode | Detection | Fallback |
|--------------|-----------|----------|
| Extraction yields zero signals | Empty `extracted_files`, no keywords | Widen retrieval: recent commits, large diffs, broader time window |
| Retrieval produces < 10 candidates | `CandidateSet` size below threshold | Widen parameters: longer time window, looser matching |
| Retrieval recall = 0 | Only measurable in eval mode | Log as retrieval failure. Agent proceeds best-effort. |
| Planning produces no hypotheses | Harness validates brief structure | Re-invoke with broader prompt or wider retrieval |
| Examination exhausts brief | All hypotheses tested, none confirmed | Loop back to planning (max 2 re-plans) |
| Budget exceeded | Hard stop by harness | Attribute with current evidence (degraded mode) |

---

## 8. Decision: Investigation Traces

Every investigation produces a structured trace. Schema defined in [mechanism-design ADR §Q4](mechanism-design.md#5-q4--trace-schema). Conceptually includes:

- Hypotheses formed and their outcomes (confirmed/rejected/abandoned)
- Candidates examined and elimination reasons
- Evidence collected with quality assessment
- Strategy decisions and rationale
- Timing and cost per stage
- Final outcome (Hit@k result in eval mode)

Traces are the substrate for skill emergence. They also enable failure forensics and retrieval diagnostics.

---

## 9. Naming Summary

| Concept | V3 name | V4 name | Rationale |
|---------|---------|---------|-----------|
| Agent's investigation plan | (none — implicit) | **InvestigationBrief** | Distinct from `.harness/contract.json` |
| Agent's progress tracking | (none — budget only) | **InvestigationState** | Explicit state machine |
| Investigation record | `tool_trace` (partial) | **InvestigationTrace** | Full structured record |
| Input preparation | "Eval Setup" (stage 1) | **Input Pipeline** | Clarifies it's not the agent |
| Agent reasoning | "Investigation" (stage 2) | **Agent Pipeline** | Clarifies governance |
| Scoring | "Evaluation" (stage 3) | **Evaluation Pipeline** | Unchanged in purpose |
| Candidate commits | (none — agent found them) | **CandidateSet** | Explicit input to agent |
| When to stop | Budget exceeded | **CompletionCriteria** | Brief-driven, not budget-driven |

---

## 10. Open Questions (for mechanism-design task)

**Resolved** in [mechanism-design ADR](mechanism-design.md): rule mechanism, skill mechanism, trace schema, completion thresholds, brief validation.

**Remaining open questions** (this ADR):

5. **CandidateSet ranking:** How to rank candidates within the set? Recency? File overlap? TF-IDF? → **`retrieval-spike` task**

---

## 11. Open Questions (for retrieval-spike task)

These require empirical investigation:

1. **Retrieval strategies:** Which combination of git commands achieves best recall@100?
2. **Candidate set size:** 50? 100? 200? Trade-off between recall and noise.
3. **Extraction quality:** How much does Level 2 extraction (LLM-assisted) improve retrieval over Level 1 (regex)?
4. **Temporal window:** Does bounding retrieval to N months before the fix improve precision?

---

## 12. Consequences

### Positive
- LLM budget focused 100% on reasoning (no wasted search)
- Agent knows what "done" means (brief-driven, not budget-driven)
- Every investigation is observable (traces)
- System can improve over time (skills from traces)
- Retrieval quality is measurable independently (retrieval recall on input pipeline)
- Clear boundaries make testing easier (each pipeline testable in isolation)

### Negative
- More infrastructure to build (harness, retrieval stage, trace storage)
- Retrieval stage is a new failure mode (if recall@100 is low, agent can't succeed regardless)
- Complexity of governance may be over-engineered for a research project — need to stay pragmatic
- Skills/rules system is aspirational — may never reach the "learning from traces" vision if the project scope is limited

### Risks
- Retrieval recall ceiling: if deterministic retrieval can't get ground truth in top 100, the architecture fails at its foundation
- Over-governance: if the harness is too rigid, it may prevent the LLM from creative investigation paths that V3 currently allows
- Premature optimization: the current V3 may still have headroom via prompt engineering alone

---

## 13. Next Steps

1. **This ADR** is reviewed and accepted (or debated by evaluator)
2. **`mechanism-design` task:** Debate rule/skill/trace mechanisms
3. **`retrieval-spike` task:** Empirically test retrieval strategies on n=20 eval cases
4. **Doc rewrite:** system-specification.md, agent-loop.md, evaluation-framework.md, glossary.md updated to describe V4
5. **Implementation:** After docs + spike settle, build incrementally (retrieval → harness → planning → examination → traces)
