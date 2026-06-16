# ADR: Architecture V4 — Mechanism Design

**Status:** Accepted  
**Date:** 2026-06-16  
**Deciders:** Builder + Evaluator (adversarial debate)  
**Context:** The [topology-debate ADR](topology-debate.md) established V4's three pipelines and governance layers but left seven implementation-level decisions open. This ADR resolves them with concrete, filesystem-based mechanisms suitable for a research project.

**Supersedes:** topology-debate ADR §§3 (rules/skills TBD), §5 (threshold TBD), §8 (trace schema TBD), §10 (open questions 1–4, 6)

---

## 1. Context

V3 achieved Hit@5=0.50, MRR=0.304, Evidence_Grounding=0.610 (n=20, task_subagent_eval_v2) using prompt guidance ("minimum 3 suspects," causal chain, 2-phase search) with avg 2.95–4.7 tool calls per case and a 30-call budget. V4 moves search to scripts and introduces harness governance — but the *mechanisms* for rules, skills, traces, prompt assembly, thresholds, brief validation, and artifact lifecycle were unspecified.

**Design constraints (research project):**
- Filesystem storage only (no database, no external services)
- No approval workflows
- Decisions must be implementable in Python without new infrastructure

**Explicitly out of scope:** CandidateSet ranking algorithm — deferred to `retrieval-spike` because ranking requires empirical recall@100 measurement, not design debate.

---

## 2. Q1 — Rules Mechanism

### Decision: Hybrid (hard gates + soft prompt guidance)

| Aspect | Choice |
|--------|--------|
| **Approach** | **Hybrid** — structural invariants enforced by harness scripts; strategy heuristics injected into LLM prompts |
| **Format** | YAML, one rule per file |
| **Storage** | `data/governance/rules/` |
| **Enforcement** | Hard rules: harness validates at stage transitions (blocks advance). Soft rules: loaded into prompt context via `PromptAssembler` |
| **Creator/maintainer** | Builder (human) via git commits — no approval gate |

### Rationale

V3 prompt v2 proved that "minimum 3 suspects" and causal-chain guidance improve Hit@5 (+43%) — but some constraints must be non-negotiable (min suspects at attribution) while others are advisory (examine parent commits). Hybrid matches V3's split between harness-enforced budget and prompt-enforced strategy.

### Rule file schema

```yaml
# data/governance/rules/min_suspects.yaml
id: min_suspects
enforcement: hard          # hard | soft
stage: attribution         # planning | examination | attribution | all
description: Never conclude with fewer than 3 suspects unless fewer than 3 candidates exist
check: suspect_count >= 3 OR candidates_examined < 3
prompt_text: ""            # empty for hard-only rules
```

### Concrete rule examples

**Rule 1 — `min_suspects` (hard, attribution)**
```yaml
id: min_suspects
enforcement: hard
stage: attribution
description: Minimum 3 suspects before attribution
check: len(suspects) >= 3 OR len(candidate_set) < 3
prompt_text: ""
```
Harness blocks Stage 4 completion until ≥3 suspects ranked, unless CandidateSet has <3 commits.

**Rule 2 — `parent_chain_examine` (soft, examination)**
```yaml
id: parent_chain_examine
enforcement: soft
stage: examination
description: When a candidate is flagged, examine its parent commit in the same file chain
check: null
prompt_text: |
  When a candidate commit modifies a file and a related bug is suspected,
  examine the parent commit that last touched the same file path before concluding.
```
Injected into Stage 3 prompt; harness does not block on non-compliance.

**Rule 3 — `continue_below_confidence` (hard, examination)**
```yaml
id: continue_below_confidence
enforcement: hard
stage: examination
description: Continue examining if top suspect confidence is below gate
check: top_confidence >= confidence_gate OR budget_hard_stop
prompt_text: |
  If your top suspect confidence is below 0.60, continue examining
  additional candidates before requesting attribution.
```
Harness prevents Stage 4 transition until confidence_gate met or budget exhausted.

### Conflict resolution (E1)

**Precedence order:**
1. **Budget hard stop** — always wins; forces degraded attribution
2. **Hard rules** — block stage transition until satisfied or budget triggers
3. **Completion criteria** (confidence gate) — evaluated by harness each turn
4. **Soft rules** — prompt guidance only; never block transitions

When hard rules conflict (e.g., `min_suspects` vs high-confidence early exit): **hard structural rules defer to budget**. At attribution, `min_suspects` wins over confidence — the agent must rank ≥3 suspects even if one has confidence >0.9, unless CandidateSet <3 or budget exhausted (degraded mode allows fewer with trace flag).

---

## 3. Q2 — Skills Mechanism

### Decision: Hybrid (manual curation + keyword retrieval, trace-sourced drafts)

| Aspect | Choice |
|--------|--------|
| **Approach** | **Hybrid** — manually authored skills plus harness-drafted skills from successful traces; keyword retrieval (no embeddings) |
| **Format** | Markdown with YAML frontmatter |
| **Storage** | Active: `data/governance/skills/` · Drafts: `data/governance/skills/drafts/` |
| **Retrieval** | Keyword overlap: match skill `triggers` against `ProblemStatement.extracted_keywords` + `project` tag |
| **Curation** | Harness writes drafts from traces with Hit@5 success; builder manually promotes drafts to active (git move) |

### Rationale

Embedding RAG adds infrastructure (vector store, embedding API) inappropriate for research scope. V3's prompt v2 strategies were hand-authored and effective. Keyword retrieval over extracted JIRA signals is sufficient at n=20 scale. Trace-sourced drafts capture learning without automatic promotion (avoids noise from SZZ mislabels).

### Skill file schema

```markdown
---
id: spark_serde_blame
scope: project          # project | general
project: SPARK          # required if scope=project
triggers: [serialization, serde, kryo, avro]
source: manual          # manual | trace-derived
trace_ref: ""           # issue_key if trace-derived
---

# Spark Serialization Bugs

When JIRA mentions serialization, Kryo, Avro, or SerDe errors, prioritize
examining commits that modify `*SerDe*.java` or `*Serializer*.java` in the
candidate set before unrelated candidates.
```

### Concrete skill examples

**Skill 1 — `spark_serde_blame` (manual)**
- **Trigger:** keywords `serialization`, `serde`, `kryo`, `avro` OR project `SPARK` + keyword `ClassCastException`
- **Action:** Prioritize SerDe/Serializer file commits in examination plan
- **Source:** manual (authored from V3 prompt v2 patterns)

**Skill 2 — `npe_null_check_removal` (trace-derived)**
- **Trigger:** keywords `NullPointerException`, `NPE`, `null pointer`
- **Action:** Prioritize CandidateSet commits whose diffs remove null-check guards in stack-trace file paths; rank those commits first in the examination plan (no repo-wide search — agent examines pre-retrieved candidates only)
- **Source:** trace-derived (draft from GROOVY-8298 hit trace, promoted by builder)

### Skill scope and generality (E2)

Each skill has a `scope` tag:
- **`general`** — retrieved for any project when triggers match
- **`project`** — retrieved only when `ProblemStatement.project` matches skill `project`

Retrieval filter: `score = len(triggers ∩ keywords)`; return top-3 skills with score > 0, preferring project-scoped over general on tie.

---

## 4. Q3 — Prompt Assembly

### Decision: Ordered section template assembled by `PromptAssembler`

The harness builds LLM prompts from fixed ordered sections. Same template for Stages 2–4; only `{{stage_instructions}}` and injected context differ.

### Section template (in order)

```
1. {{system_role}}           — fixed role description per stage
2. {{hard_rules}}            — hard rules for current stage (summary text)
3. {{soft_rules}}            — soft rule prompt_text blocks
4. {{skills}}                — top-3 retrieved skills (full markdown body)
5. {{stage_instructions}}    — stage-specific task instructions
6. {{problem_statement}}     — ProblemStatement.to_prompt_text()
7. {{candidate_summary}}     — top-N candidates formatted (rank, sha, summary, files)
8. {{investigation_state}}   — progress status (Stage 3+ only)
9. {{brief}}                 — InvestigationBrief JSON (Stage 3+ only)
10. {{evidence_so_far}}      — collected evidence summary (Stage 4 only)
```

### Assembly algorithm

1. Load rules: `load_rules(stage=current_stage)` → split hard/soft
2. Load skills: `retrieve_skills(problem_statement, top_k=3)`
3. Format candidates: top 20 for Stage 2; full examined set + next 5 unexamined for Stage 3
4. Concatenate sections in order; omit empty sections
5. **Truncation policy:** If total tokens > 80% of budget (80K), truncate in order: `evidence_so_far` → `candidate_summary` (keep top 10) → `skills` (keep top 1) → never truncate `problem_statement`, `hard_rules`, or `stage_instructions`

### Stage 2 Planning — concrete example (≥200 words)

```
## System Role
You are a bug attribution investigator in the Planning stage. Produce a
structured InvestigationBrief with falsifiable hypotheses and an examination plan.

## Hard Rules (Planning)
- Produce at least 2 falsifiable hypotheses
- Define success criteria using project defaults: 3 evidence quotes, 2 hypotheses tested, 0.60 confidence gate

## Soft Rules (Planning)
When the bug report mentions a specific file path, prioritize hypotheses that
target commits modifying that file or its parent chain.

## Relevant Skills
### Spark Serialization Bugs
When JIRA mentions serialization, Kryo, Avro, or SerDe errors, prioritize
examining commits that modify *SerDe*.java or *Serializer*.java in the candidate
set before broad blame searches.

## Stage Instructions
Analyze the bug report and candidate commits below. Output JSON matching
InvestigationBrief schema: hypotheses, examination_plan, success_criteria,
strategy, max_effort. Each hypothesis must state what would confirm or falsify it.

## Problem Statement
Title: SPARK-19033 — Kryo serialization fails for nested types
Description: When serializing UserDefinedType with nested StructType fields,
Kryo throws ClassCastException during task serialization. Affects pyspark SQL
execution path. Stack trace points to org.apache.spark.sql.catalyst.expressions.

Extracted files: sql/catalyst/src/main/scala/org/apache/spark/sql/catalyst/expressions/KryoSerializer.scala
Extracted keywords: serialization, Kryo, ClassCastException, UDT, nested types

## Candidate Summary (top 10 of 87)
1. a3f2c91 — "Fix Kryo registration for nested types" — sql/catalyst/.../KryoSerializer.scala
2. b8e1d04 — "Refactor SerializerHelper for UDT" — sql/catalyst/.../SerializerHelper.scala
3. c4f7a22 — "Add Avro support for nested structs" — sql/avro/.../AvroSerializer.scala
4. d1e8b33 — "Guard null nested fields in UDT serializer" — sql/catalyst/.../UserDefinedType.scala
5. e2f9c44 — "Kryo buffer resize for large structs" — sql/catalyst/.../KryoSerializer.scala
6. f3a0d55 — "Fix pyspark SQL execution serde path" — python/pyspark/sql/.../serializers.py
7. g4b1e66 — "Register StructType with Kryo for nested UDT" — sql/catalyst/.../StructType.scala
8. h5c2f77 — "Revert broken Kryo class registration" — sql/catalyst/.../KryoSerializer.scala
9. i6d3g88 — "Add test for nested UDT serialization" — sql/catalyst/src/test/.../KryoSerializerSuite.scala
10. j7e4h99 — "Optimize catalyst expression serialization" — sql/catalyst/.../Expression.scala

Output ONLY valid JSON for InvestigationBrief.
```

Stages 3 and 4 use the same section order; `stage_instructions` and conditional sections (`brief`, `evidence_so_far`) change per stage.

### Rationale

Fixed section order makes prompts testable and diffable across stages. Separating hard rules (harness-enforced summaries) from soft rules (full prompt text) matches Q1 hybrid enforcement. Truncation policy protects `problem_statement` and stage instructions while allowing candidate/skill trimming under token pressure — V3 showed large candidate lists dominate context.

---

## 5. Q4 — Trace Schema

### Decision: JSON file per investigation, per-turn granularity for examination

| Aspect | Choice |
|--------|--------|
| **Format** | JSON (one file per investigation; not JSONL — traces are written atomically per run) |
| **Storage** | `results/traces/{issue_key}/{run_id}.json` |
| **Granularity** | Per-stage events for Stages 2 and 4; **per-turn** events for Stage 3 examination |
| **Writer** | Harness appends events; finalizes on investigation end |

### Rationale

JSON per investigation (not JSONL) enables atomic write-once semantics suitable for research forensics. Per-turn Stage 3 granularity supports completion-criteria debugging; per-stage summaries for planning and attribution keep file size manageable. Storage under `results/traces/` colocates traces with eval outputs.

### Top-level InvestigationTrace fields

| Field | Type | Description |
|-------|------|-------------|
| `trace_id` | string | UUID |
| `issue_key` | string | e.g. `SPARK-19033` |
| `run_id` | string | ISO timestamp or eval run ID |
| `temporal_bound` | string | bound ref used |
| `candidate_set_size` | int | commits in CandidateSet |
| `retrieval_recall_100` | bool \| null | eval-only: bug_hash in CandidateSet |
| `hypotheses` | list[HypothesisRecord] | see nested schema |
| `candidates_examined` | list[string] | commit SHAs inspected |
| `candidates_eliminated` | list[EliminationRecord] | see nested schema |
| `evidence_collected` | list[EvidenceRecord] | see nested schema |
| `strategy_decisions` | list[StrategyRecord] | see nested schema |
| `examination_turns` | list[TurnRecord] | per-turn Stage 3 log |
| `stage_timings` | dict[str, float] | stage name → elapsed_ms |
| `outcome` | OutcomeRecord | see nested schema |

### Nested schemas

**HypothesisRecord**
```python
{ "id": str, "statement": str, "status": "formed"|"confirmed"|"rejected"|"abandoned",
  "reason": str, "stage": int, "turn": int | null }
```

**EliminationRecord**
```python
{ "commit_id": str, "reason": str, "turn": int, "hypothesis_id": str | null }
```

**EvidenceRecord**
```python
{ "commit_id": str, "quote": str, "grounded": bool | null,  # null until post-attribution scoring
  "hypothesis_id": str | null, "turn": int }
```

**StrategyRecord**
```python
{ "decision": str, "rationale": str, "stage": int, "turn": int | null,
  "alternatives_considered": list[str] }
```

**TurnRecord** (Stage 3 per-turn)
```python
{ "turn": int, "tool_calls": list[{ "tool": str, "args": dict, "summary": str }],
  "hypothesis_updates": list[str],  # hypothesis IDs updated this turn
  "completion_check": { "evidence_met": bool, "coverage_met": bool,
    "confidence_met": bool, "brief_satisfied": bool } }
```

**OutcomeRecord**
```python
{ "suspect_count": int, "top_confidence": float, "degraded": bool,
  "degraded_reason": str | null,  # "budget_exhausted" | "replan_limit" | "no_confirmed_hypotheses" | "empty_candidates" | null
  "hit_at_5": bool | null,  # eval-only
  "mrr": float | null }     # eval-only
```

### Minimal valid JSON example

```json
{
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "issue_key": "GROOVY-8298",
  "run_id": "2026-06-16T12:00:00Z",
  "temporal_bound": "abc123~1",
  "candidate_set_size": 72,
  "retrieval_recall_100": true,
  "hypotheses": [
    {"id": "h1", "statement": "NPE caused by removed null guard in Parser.java",
     "status": "confirmed", "reason": "Diff shows deleted if-block", "stage": 2, "turn": null}
  ],
  "candidates_examined": ["def456"],
  "candidates_eliminated": [],
  "evidence_collected": [
    {"commit_id": "def456", "quote": "- if (token != null) {",
     "grounded": true, "hypothesis_id": "h1", "turn": 1}
  ],
  "strategy_decisions": [
    {"decision": "examine rank-3 candidate first", "rationale": "file path match",
     "stage": 3, "turn": 1, "alternatives_considered": ["rank-1", "rank-2"]}
  ],
  "examination_turns": [
    {"turn": 1, "tool_calls": [{"tool": "get_commit_diff", "args": {"commit_id": "def456"},
      "summary": "Removed null check in parse()"}], "hypothesis_updates": ["h1"],
     "completion_check": {"evidence_met": true, "coverage_met": true,
       "confidence_met": true, "brief_satisfied": true}}
  ],
  "stage_timings": {"planning": 4200.0, "examination": 45000.0, "attribution": 8100.0},
  "outcome": {"suspect_count": 3, "top_confidence": 0.85, "degraded": false,
    "degraded_reason": null, "hit_at_5": true, "mrr": 1.0}
}
```

### Empty-result paths (E3)

**Zero suspects after examination** (all hypotheses rejected):
- Harness proceeds to Stage 4 in **degraded mode**
- Attribution prompt: "All hypotheses rejected. Rank best-effort suspects from examined candidates."
- `outcome.suspect_count` may be 0; `outcome.degraded=true`, `degraded_reason="no_confirmed_hypotheses"`
- Trace still written; skill extraction skips cases with `suspect_count=0`

**Sparse CandidateSet** (< 3 commits):
- Skip planning; examine all candidates; attribute what's found
- `outcome.degraded=true`, `degraded_reason="empty_candidates"` when suspect count < 3

---

## 6. Q5 — Completion Threshold Values

### Decision: Numeric defaults calibrated from V3 task_subagent_eval_v2

**V3 calibration baseline** (task_subagent_eval_v2, n=20, seed=42): Hit@5=**0.50** (10/20 hits), MRR=**0.304**, Evidence_Grounding=**0.610**, avg tool calls **2.95** (Ollama 8b) to **4.7** (32b), budget **30** tool calls / 100K tokens / $0.50.

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `evidence_threshold` | **3** | V3 prompt v2 enforced min 3 suspects; Hit@5=0.50 with 10/20 hits at that setting; Evidence_Grounding=0.610 implies ~2 quotes/suspect; 3 grounded quotes = one per suspect minimum |
| `hypothesis_coverage` | **2** | V3 hits tested multiple causal paths; 10/20 hit cases involved chain reasoning across ≥2 hypotheses; topology-debate proposed M≥2 |
| `confidence_gate` | **0.60** | Topology-debate draft; below this, V3 cases continued examining; aligns with rule `continue_below_confidence` |
| `max_effort` | **18** | V3 avg 2.95–4.7 tool calls included search (25–30% of budget); V4 allocates search to input pipeline, leaving ~30 for agent. Reserve ~12 for planning+attribution LLM turns → 18 examination tool calls (~6 per suspect for 3 suspects). MRR=0.304 suggests top ranks matter — 18 calls allows depth without hitting 30-call hard stop |

**Budget (unchanged):** 30 tool calls / 100K tokens / $0.50 — hard stop per `state.json cost_budget`.

**Retrieval Recall@100 targets:** Remain **`retrieval-spike` scope** — input pipeline recall thresholds are empirical, not design decisions.

### Rationale

Thresholds inherit V3 prompt v2 settings that achieved Hit@5=0.50: min 3 evidence quotes maps to min 3 suspects; 2 hypothesis coverage matches chain-reasoning hits; 0.60 confidence gate prevents premature exit; max_effort=18 allocates ~60% of the 30-call agent budget to examination after moving search to the input pipeline.

### Degraded mode when budget exhausted (E4)

If budget hard stop triggers before completion criteria met:
1. Harness forces Stage 4 transition
2. Sets `InvestigationState.budget_used.hard_stop = true`
3. Trace: `outcome.degraded=true`, `outcome.degraded_reason="budget_exhausted"`
4. Attribution proceeds with partial evidence; `min_suspects` hard rule relaxed to `suspect_count >= candidates_examined`
5. Metadata flag: `degraded_mode=true`

---

## 7. Q6 — Brief Validation

### Decision: Structural validation with retry + default fallback

| Rule | Value |
|------|-------|
| **Minimum hypotheses** | **2** falsifiable statements |
| **Required fields** | `hypotheses`, `examination_plan`, `success_criteria`, `strategy`, `max_effort` — all non-empty |
| **success_criteria** | Must include numeric `evidence_threshold`, `hypothesis_coverage`, `confidence_gate` (defaults applied if omitted) |
| **examination_plan** | ≥1 step with `commit_id` or `file_path` and `look_for` description |

### Invalid brief definition

A brief is **invalid** if any of:
- `len(hypotheses) < 2`
- Any hypothesis lacks a falsifiable claim (harness checks: contains "because" or "if" or "caused by")
- `examination_plan` empty
- `strategy` empty or < 20 characters
- `max_effort` > 25 (exceeds reasonable examination allocation)

### Harness responses

| Condition | Response |
|-----------|----------|
| Invalid brief, retry 0 | Re-invoke Stage 2 with validation error details (max **1 retry**). Harness re-runs **input pipeline Stage 1** with widened retrieval parameters (longer time window, looser keyword match), then re-plans with the expanded `CandidateSet` — per topology-debate §7 |
| Invalid after 1 retry (E5) | Advance to Stage 3 with **default brief**: examine top **10** candidates by retrieval rank, generic hypothesis "Bug introduced by a commit modifying extracted files", default success_criteria from Q5, `max_effort=18` |
| Examination exhausted, unsatisfied | Re-plan (max **2** re-plans), context includes failed hypotheses + trace summary |
| Re-plan limit reached | Force Stage 4 degraded, `degraded_reason="replan_limit"` |

Aligned with topology-debate §7 fallback table.

### Rationale

Minimum 2 hypotheses forces alternative explanations (V3 chain reasoning). Default brief after failed retry prevents agent stall without LLM self-governance. Re-running Stage 1 retrieval (not agent-side search) preserves the `llm_reasons_scripts_retrieve` invariant while honoring topology-debate's "wider retrieval" fallback.

---

## 8. Q7 — Artifact Ownership Lifecycle

| Artifact | Creator | Approval | Storage | Retention |
|----------|---------|----------|---------|-----------|
| **Rules** | Builder (human) | None — git commit is sufficient | `data/governance/rules/` | Permanent in repo |
| **Skills (active)** | Builder promotes from drafts | None — manual promotion | `data/governance/skills/` | Permanent in repo |
| **Skills (drafts)** | Harness script from successful traces | None — manual promotion via `git mv`; no approval gate | `data/governance/skills/drafts/` | Delete drafts older than 90 days (optional `scripts/prune_skill_drafts.py`) |
| **Traces** | Harness (automatic per investigation) | None | `results/traces/{issue_key}/` | Permanent; optional archive to `.harness/.archive/traces/` |
| **Briefs** | LLM per investigation | None — ephemeral | Embedded in trace only | Lifecycle tied to trace |

No approval workflows. No external services. All paths repo-relative.

### Rationale

Git is the sole source of truth for rules and promoted skills — appropriate for a research repo with a single builder. Harness-auto traces require no curation. Ephemeral briefs avoid stale plan artifacts; lifecycle is captured in `InvestigationTrace`.

---

## 9. Plan Quality Metric (AC12)

### Decision: Deterministic overlap metric (v1); LLM judge deferred

**Mechanism:** Script-computed **Plan Overlap Score** in eval mode:

```
plan_overlap = |examination_plan_files ∩ bug_hash_files| / |bug_hash_files|
```

Where `bug_hash_files` = files changed in ground-truth bug commit (eval-only oracle data). In the examination plan, extract file paths from each step's `file_path` or from `commit_id` → files via git.

**Threshold:** Plan Overlap ≥ 0.30 = "plan targets correct area" (binary per case).

**Deferred:** LLM judge for plan quality — rationale: overlap metric is zero LLM cost, sufficient for n=20 eval, and distinguishes "planned wrong files" from "planned right files but examined poorly." LLM judge adds cost without proven incremental value at current scale.

---

## 10. CandidateSet Ranking Deferral (AC11)

**Deferred to `retrieval-spike`.** Ranking algorithm (recency vs file overlap vs TF-IDF) requires empirical recall@100 measurement on n=20 eval cases — a design debate cannot select the optimal strategy without data.

---

## 11. Pragmatic Constraints (AC13)

**Explicitly rejected:**
- Database for rules, skills, or traces
- Approval workflows for rule/skill changes
- External service (S3, vector DB, REST API) for governance artifacts
- Embedding-based RAG (deferred; keyword retrieval sufficient at research scale)

**Storage:** All artifacts use repo-relative filesystem paths listed above.

---

## 12. Consequences

### Positive
- All seven mechanism questions resolved with implementable paths
- Hybrid rules/skills match V3 lessons (prompt guidance + structural gates)
- Trace schema enables forensics and skill draft generation
- Thresholds grounded in V3 Hit@5=0.50 baseline

### Negative
- Keyword skill retrieval may miss semantic matches (acceptable at n=20)
- Plan overlap metric requires eval-mode oracle (not available in production)
- Manual skill curation does not scale without future automation

### Next Steps
1. ~~Create `data/governance/rules/` and `data/governance/skills/` directories with seed rules/skills~~ **Done** — seed artifacts at `data/governance/`
2. Implement `PromptAssembler`, trace writer, brief validator in harness package
3. Update `system-specification.md`, `agent-loop.md`, `glossary.md`, `evaluation-framework.md`
4. Proceed to `retrieval-spike` for CandidateSet ranking and recall@100 targets

---

## Related

- [topology-debate.md](topology-debate.md) — V4 topology ADR
- [docs/system-specification.md](../../docs/system-specification.md) — data structures
- [docs/agent-loop.md](../../docs/agent-loop.md) — stage mechanics
- [docs/evaluation-framework.md](../../docs/evaluation-framework.md) — Plan Quality metric
