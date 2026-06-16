# Agent Loop — Bug Attribution (V4 Target)

The agent loop is the reasoning core: Stages 2–3–4 of the system pipeline. It receives a `CandidateSet` + `ProblemStatement` from the input pipeline and produces a `BugAttributionReport`. The loop is governed by the **investigation harness** — the LLM does not self-govern.

> **Architecture status:** This describes the V4 target. Current implementation (V3) runs a 7-stage advisory loop without explicit planning or governance. See [system-specification.md — Current Implementation](system-specification.md#current-implementation-v3--reference) for V3 details.

---

## State Machine

```
                    ┌─────────────────────────────────┐
                    │         AGENT RECEIVES           │
                    │  CandidateSet + ProblemStatement │
                    └──────────────┬──────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────────┐
                    │     STAGE 2: PLANNING            │
                    │  Produce InvestigationBrief      │
                    │  (hypotheses, plan, criteria)    │
                    └──────────────┬───────────────────┘
                                   │ brief valid?
                                   │ yes ──────────────────────────┐
                                   │                               │
                                   ▼                               │
                    ┌──────────────────────────────────┐           │
                    │     STAGE 3: EXAMINATION         │           │
                    │  Test hypotheses via tools       │           │
                    │  Collect evidence                │           │
                    └──────────────┬───────────────────┘           │
                                   │                               │
                         ┌─────────┴─────────┐                    │
                         │ Harness evaluates  │                    │
                         │ completion criteria│                    │
                         └─────────┬─────────┘                    │
                                   │                               │
                    ┌──────────────┼──────────────┐               │
                    │              │              │               │
                    ▼              ▼              ▼               │
              [satisfied]   [insufficient]  [exhausted]           │
                    │              │              │               │
                    │              │              │ re-plan       │
                    │              │              │ (max 2)       │
                    │              │              └───────────────┘
                    │              │
                    │              └──► continue Stage 3
                    ▼
                    ┌──────────────────────────────────┐
                    │     STAGE 4: ATTRIBUTION         │
                    │  Rank suspects, write report     │
                    │  + Evidence Scoring (script)     │
                    └──────────────┬───────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────────┐
                    │     OUTPUT                       │
                    │  BugAttributionReport            │
                    │  + InvestigationTrace            │
                    └─────────────────────────────────┘
```

---

## Stage 2: Planning

### Entry conditions
- Valid `CandidateSet` (>= 1 commit)
- Valid `ProblemStatement` (title + description non-empty)

### What happens
1. Harness assembles context: problem statement, candidate summaries, relevant skills
2. LLM produces structured `InvestigationBrief`
3. Harness validates brief structure (hypotheses present, plan non-empty, criteria defined)
4. If invalid → harness re-runs input pipeline Stage 1 with widened retrieval, then re-invokes Stage 2 (max 1 retry). See [mechanism-design ADR §Q6](../.harness/docs/mechanism-design.md#7-q6--brief-validation).

### Output: InvestigationBrief

| Field | Description |
|-------|-------------|
| `hypotheses` | Falsifiable statements: "The bug was introduced by commit X because it changed Y which caused Z" |
| `examination_plan` | Ordered list of commits/files to examine and what to look for in each |
| `success_criteria` | When this investigation is complete (evidence threshold, hypothesis coverage) |
| `strategy` | Overall approach rationale |
| `max_effort` | Tool call budget for this investigation cycle |

### Exit conditions
- Brief is structurally valid → advance to Stage 3
- Brief invalid after retry → advance to Stage 3 with default brief (examine top 10 candidates)

### Skills integration
If investigation skills are available, the harness retrieves top-3 skills by keyword overlap and injects them via `PromptAssembler`. See [mechanism-design ADR §Q2–Q3](../.harness/docs/mechanism-design.md#3-q2--skills-mechanism).

### Brief validation
Minimum 2 hypotheses; all `InvestigationBrief` fields required. Invalid brief → 1 retry → default brief (examine top 10 candidates). See [mechanism-design ADR §Q6](../.harness/docs/mechanism-design.md#7-q6--brief-validation).

---

## Stage 3: Examination

### Entry conditions
- Valid `InvestigationBrief` from Stage 2
- Budget not exceeded

### What happens
1. Harness provides LLM with brief + candidate data
2. LLM calls examination tools to test hypotheses
3. After each turn, harness evaluates completion criteria
4. LLM collects evidence (diff quotes, blame results, causal reasoning)

### Tools available

| Tool | Purpose |
|------|---------|
| `get_commit_diff` | Inspect what a candidate commit changed |
| `get_commit_message` | Read the author's stated intent |
| `get_file_at_commit` | See file state at a specific point |
| `get_blame` | Trace line-level authorship |

Tools are scoped to the `CandidateSet` — the LLM examines pre-retrieved candidates, not the full repo. All tools enforce the temporal bound.

### Completion criteria evaluation (after each turn)

Threshold values and degraded-mode behavior: [mechanism-design ADR §Q5](../.harness/docs/mechanism-design.md#6-q5--completion-threshold-values). The harness checks:

| Criterion | Check | Action if met |
|-----------|-------|---------------|
| Evidence threshold | >= 3 grounded quotes collected | Advance to Stage 4 |
| Hypothesis coverage | >= 2 hypotheses tested | Advance to Stage 4 |
| Confidence gate | Top suspect confidence >= 0.60 | Advance to Stage 4 |
| Brief satisfaction | All planned examinations done | Advance to Stage 4 |
| Budget exceeded | Tool calls or tokens at limit | Force advance to Stage 4 (degraded) |
| Hypotheses exhausted | All tested, none confirmed, brief unsatisfied | Loop back to Stage 2 |

### Loop-back to Planning

If all hypotheses are tested but the brief is not satisfied (insufficient evidence, no confident suspects), the harness can loop back to Stage 2 for re-planning:

- **Max re-plans:** 2 (to prevent infinite loops)
- **Re-plan context:** includes what was tried and what failed
- **Budget carries over:** re-planning does not reset the budget

### Exit conditions
- Completion criteria satisfied → advance to Stage 4
- Budget hard stop → force advance to Stage 4
- Re-plan limit reached → force advance to Stage 4

---

## Stage 4: Attribution

### Entry conditions
- Stage 3 complete (satisfied or forced)

### What happens
1. Harness provides LLM with all evidence collected in Stage 3
2. LLM produces ranked suspect list with mechanisms and quotes
3. Evidence Scoring (script) runs unconditionally on output
4. Report assembled with full metadata

### Rules enforced

Hard/soft rules from `data/governance/rules/`: [mechanism-design ADR §Q1](../.harness/docs/mechanism-design.md#2-q1--rules-mechanism).

- Minimum 3 suspects (if fewer than 3 candidates examined, include all)
- Each suspect must have a causal mechanism ("If X then Y")
- Each suspect should have at least 1 evidence quote from a diff

### Evidence Scoring (post-attribution script)
After the LLM produces suspects, `score_suspect_evidence()` runs for each:
1. Fetch commit diff
2. Check each evidence quote against diff (exact → normalized → fuzzy)
3. Compute `grounding_rate = grounded_quotes / total_quotes`
4. Attach scores to `metadata["evidence_scores"]`

Evidence scores are **metadata only** — they do not modify suspect rank or confidence.

### Output
- `BugAttributionReport` with ranked suspects, reasoning, tool trace, metadata
- `InvestigationTrace` with full structured investigation record

---

## Harness Governance

The investigation harness is the non-LLM controller. It makes all lifecycle decisions:

| Decision | Harness responsibility |
|----------|----------------------|
| When to invoke LLM | At each stage transition and turn |
| What context to provide | Problem, candidates, skills, progress status |
| When to stop | Completion criteria or budget |
| When to re-plan | Hypotheses exhausted without satisfaction |
| What to record | Every decision and its rationale in the trace |

The LLM's role is **execution within boundaries**:
- Generate hypotheses (Stage 2)
- Call tools and reason about evidence (Stage 3)
- Produce final attribution (Stage 4)

The LLM does NOT decide:
- When to stop investigating
- Whether to re-plan
- What stage to transition to
- Whether evidence is "good enough"

---

## Investigation Tracing

Trace schema and storage: [mechanism-design ADR §Q4](../.harness/docs/mechanism-design.md#5-q4--trace-schema). Every step of the agent loop produces trace data. The harness records:

### Per-stage trace data

| Stage | Recorded |
|-------|----------|
| 2 (Planning) | Hypotheses formed, strategy chosen, skills consulted |
| 3 (Examination) | Each tool call + result summary, hypothesis confirmed/rejected/abandoned, evidence quality |
| 4 (Attribution) | Final ranking rationale, confidence distribution, evidence grounding scores |

### Trace lifecycle

```
Investigation starts → trace initialized
    Stage 2 → hypotheses + plan recorded
    Stage 3 (turn N) → tool call + result + hypothesis update recorded
    Stage 3 (completion check) → criteria evaluation recorded
    Stage 4 → attribution rationale recorded
    Evidence scoring → grounding results recorded
Investigation ends → trace finalized with outcome
```

### What traces enable

| Use case | How |
|----------|-----|
| Failure forensics | Why did we miss? Retrieval failure (not in candidates) or reasoning failure (examined but not ranked)? |
| Retrieval diagnostics | Was ground truth in `CandidateSet`? At what rank? |
| Skill emergence | Which strategies led to hits? Extract patterns. |
| Cost optimization | Where is budget spent? Which stages are expensive? |
| Debugging | Reproduce the exact investigation path for any case. |

---

## Degradation Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| Normal | Brief satisfied | Full quality attribution |
| Degraded (budget) | Budget exceeded before satisfaction | Attribute with partial evidence, log in trace |
| Degraded (re-plan limit) | 2 re-plans without satisfaction | Attribute with best available suspects |
| Degraded (empty candidates) | CandidateSet has < 3 commits | Skip planning, examine all, attribute what's found; `outcome.degraded_reason="empty_candidates"` per [mechanism-design ADR §Q4](../.harness/docs/mechanism-design.md#5-q4--trace-schema) |

All degradation is logged in `InvestigationTrace` and reported in `metadata`.

---

## Resource Limits

| Resource | Limit | Enforced by |
|----------|-------|-------------|
| Tool calls | 30 | Harness (budget hard stop) |
| Tokens | 100,000 | Harness (budget hard stop) |
| Cost | $0.50 USD | Harness (budget hard stop) |
| Re-plans | 2 | Harness (loop-back counter) |
| Brief retries | 1 | Harness (planning validation) |

---

## Related

- [system-specification.md](system-specification.md) — full system (all three pipelines), data structures, LLM boundary
- [evaluation-framework.md](evaluation-framework.md) — metrics, stage-to-metric mapping
- [glossary.md](glossary.md) — term definitions
- [.harness/docs/topology-debate.md](../.harness/docs/topology-debate.md) — ADR for V4 architecture
- [.harness/docs/mechanism-design.md](../.harness/docs/mechanism-design.md) — ADR for governance mechanisms
