# Evaluation Framework — Bug Attribution Agent (V4 Target)

This document defines how the bug attribution system is measured. It separates **system-level metrics** (does the pipeline find the right commit?) from **output-quality metrics** (is the LLM's reasoning good?), and specifies baselines, thresholds, and known limitations.

> **Architecture status:** This describes evaluation for the V4 target architecture (three pipelines: Input / Agent / Evaluation). Metric definitions and computation remain the same as V3 — what changes is the stage-to-metric mapping and the addition of input pipeline metrics.

## Two Levels of Evaluation

### Level 1: System-Level Metrics — "Did we find the right commit?"

These metrics evaluate the full pipeline end-to-end. A correct answer means the ground truth `bug_hash` appears in the agent's ranked suspect list. Both the LLM agent and zero-LLM baselines produce the same output format (`BugAttributionReport`) and are scored identically.

| Metric | Type | Question answered |
|--------|------|-------------------|
| **Hit@1** | Binary per case, averaged | Is `bug_hash` the #1 suspect? |
| **Hit@3** | Binary per case, averaged | Is `bug_hash` in the top 3? |
| **Hit@5** | Binary per case, averaged | Is `bug_hash` in the top 5? **Primary metric.** |
| **MRR** | Continuous [0, 1] | What is the mean reciprocal rank of `bug_hash`? |
| **Retrieval Recall** | Binary per case, averaged | Did the agent ever *fetch* `bug_hash` during its search? |

### Level 2: Output-Quality Metrics — "Is the reasoning good?"

These metrics evaluate the quality of the LLM's output independent of whether it found the correct commit. A system could find the right commit by luck but produce poor reasoning — or miss the right commit but demonstrate sound investigative logic.

| Metric | Type | Question answered | Owner | Status |
|--------|------|-------------------|-------|--------|
| **D6 Evidence Grounding** | Continuous [0, 1] per case | Are the evidence quotes from real diffs? | Script | Implemented |
| **D3 Attribution Quality** | 0-4 scale per case | Does the causal mechanism explain how the suspect introduced the bug? | LLM judge | Implemented (`eval/d3_judge.py`) |

---

## Stage-to-Metric Mapping (V4 — Three Pipelines)

This table maps the V4 pipeline stages to the metrics that measure them. See [system-specification.md](system-specification.md) for full stage details.

### Input Pipeline Metrics

| Stage | Metric | What is measured | Status |
|-------|--------|------------------|--------|
| 0 Extraction | **Extraction Signal Count** | Number of search signals produced (files, symbols, keywords) | Concept — not yet implemented |
| 1 Retrieval | **Retrieval Recall@100** | Is `bug_hash` in the `CandidateSet`? | Concept — to be built in `retrieval-spike` |

**Retrieval Recall@100** is the foundational metric for V4. If the input pipeline does not include `bug_hash` in the candidate set, the agent cannot succeed regardless of reasoning quality. This metric measures input pipeline quality independently of the agent.

### Agent Pipeline Metrics

| Stage | Metric(s) | What is measured | Status |
|-------|-----------|------------------|--------|
| 2 Planning | **Plan Quality** | Does the brief cover the right area? Are hypotheses relevant? | Plan Overlap Score (eval-only) — see [mechanism-design ADR §9](../.harness/docs/mechanism-design.md#9-plan-quality-metric-ac12) |
| 3 Examination | **Retrieval Recall** (agent-level) | Whether `bug_hash` appears in `tool_trace[].args.commit_id` from examine tools | Implemented (V3 compatible) |
| 4 Attribution | **Hit@k**, **MRR**, **D3 Attribution Quality** | Final ranked suspect list vs ground truth; causal mechanism quality | Implemented |
| 4 Attribution (post) | **D6 Evidence Grounding** | Whether evidence quotes appear in suspect diffs | Implemented |

### Metric disambiguation: two levels of Retrieval Recall

| Metric | Pipeline | Question | Computation |
|--------|----------|----------|-------------|
| **Retrieval Recall@100** (input) | Input Pipeline | Is `bug_hash` in the `CandidateSet`? | `1 if bug_hash in candidate_set.commit_ids else 0` |
| **Retrieval Recall** (agent) | Agent Pipeline | Did the agent examine `bug_hash` via a tool call? | `1 if bug_hash in tool_trace[].args.commit_id else 0` |

A case can have Retrieval Recall@100 = 1 (bug was in candidates) but agent Retrieval Recall = 0 (agent never examined it). This distinguishes input quality from agent examination quality.

### Plan Quality (Plan Overlap Score)

Plan Quality measures whether the `InvestigationBrief` targets the right area of the codebase using a deterministic overlap metric (zero LLM cost).

**Mechanism:** Script-computed Plan Overlap Score in eval mode:

```
plan_overlap = |examination_plan_files ∩ bug_hash_files| / |bug_hash_files|
```

Where `bug_hash_files` = files changed in the ground-truth bug commit (eval-only oracle). Extract file paths from each examination plan step's `file_path` or from `commit_id` → files via git.

**Threshold:** Plan Overlap ≥ 0.30 = plan targets correct area (binary per case).

**Deferred:** LLM judge for plan quality — overlap metric sufficient at n=20 research scale. See [mechanism-design ADR §9](../.harness/docs/mechanism-design.md#9-plan-quality-metric-ac12).

### V3 compatibility note

The V3 stage-to-metric mapping (7 advisory stages) is superseded by the above. Agent-level Retrieval Recall computation (`tool_trace[].args.commit_id`) remains identical in V4 — only the stage numbering changes.

---

## Metric Definitions

### Hit@k (k = 1, 3, 5)

**What it measures:** Whether the ground truth bug-introducing commit appears in the agent's top k suspects.

**Computation:**
```
rank = position of bug_hash in suspect list (1-based)
hit_at_k = 1 if rank is not None and rank <= k, else 0
aggregated = mean(hit_at_k for all cases)
```

**Matching:** Exact SHA match or 12-character prefix match (handles short vs full SHAs).

**Limitation:** SZZ-derived `bug_hash` labels have known noise — format changes, refactoring, and multi-commit fixes can produce incorrect labels. Hit@5 is the primary metric because it tolerates this noise better than Hit@1.

### MRR (Mean Reciprocal Rank)

**What it measures:** Average of `1/rank` across all cases. Rewards finding the right commit higher in the list.

**Computation:**
```
mrr_case = 1.0 / rank if bug_hash found, else 0.0
mrr = mean(mrr_case for all cases)
```

**Range:** [0, 1]. MRR = 1.0 means every case had `bug_hash` at rank 1.

### Retrieval Recall (agent-level)

**What it measures:** Whether the agent ever *examined* the bug-introducing commit via a tool call (commit in `tool_trace[].args`), regardless of whether it ranked it as a suspect.

**V4 pipeline stage:** Stage 3 (Examination). The agent examines candidates from the `CandidateSet` provided by the input pipeline.

**Computation:**
```
commit_ids = {tool_trace[i].args.commit_id for all tool calls where commit_id present}
retrieval_recall = 1 if bug_hash[:12] in {cid[:12] for cid in commit_ids}, else 0
```

**Not counted:** SHAs appearing only in tool **result text** — only `args.commit_id` on tool trace records matters.

**Why it matters:** Separates examination quality from ranking quality. If agent retrieval recall is high but Hit@5 is low, the agent examined the right commit but did not rank it — a reasoning problem. If agent retrieval recall is low but Retrieval Recall@100 is high, the candidate set contained ground truth but the agent never examined it — a planning/strategy problem.

### Retrieval Recall@100 (input pipeline)

**What it measures:** Whether the input pipeline's `CandidateSet` contains the ground truth `bug_hash`. Measures retrieval quality independently of the agent.

**V4 pipeline stage:** Stage 1 (Candidate Retrieval).

**Computation:**
```
retrieval_recall_100 = 1 if bug_hash[:12] in {c.commit_id[:12] for c in candidate_set.commits}, else 0
```

**Why it matters:** Foundational metric for V4. If `bug_hash` is not in the candidate set, the agent cannot succeed regardless of reasoning quality. A low Retrieval Recall@100 indicates an input pipeline failure, not an agent failure. Target: **TBD** — to be calibrated in `retrieval-spike` task.

### D6 Evidence Grounding

**What it measures:** Are the evidence quotes in the agent's suspect output actually present in the corresponding commit diffs?

**Computation:**
```
for each suspect:
    for each evidence_quote:
        check if quote appears in commit diff via:
            1. exact substring
            2. normalized (strip +/-, collapse whitespace)
            3. token-set fuzzy (>=80% of tokens >=3 chars, in order, within 200-char windows)
        grounded = True if any check passes (tier = SUPPORTED)
    grounding_rate = grounded_quotes / total_quotes

case_score = mean(grounding_rate for all suspects)
aggregated = mean(case_score for all cases)
```

**Special cases:**
- No diff available: all quotes `UNVERIFIABLE`, rate = 0.0
- Quote < 8 characters: not grounded
- No evidence quotes: rate = 0.0

**Why it matters:** Measures whether the LLM is hallucinating evidence. A high grounding rate means the agent is citing real code changes. This is computed by script — no LLM judge needed.

### D3 Attribution Quality

**What it measures:** Does the causal mechanism ("If X then Y") correctly explain how the suspect commit introduced the bug?

**Rubric (0-4 scale):**

| Score | Label | Criteria |
|-------|-------|----------|
| 0 | Absent/wrong | No mechanism provided, or completely incorrect (wrong commit, wrong files, wrong behavior) |
| 1 | Vague | Generic explanation ("this commit changed something related to the issue") with no specific causal chain |
| 2 | Partial | Identifies the right area of code but wrong or incomplete causal chain (e.g. right file, wrong function; right symptom, wrong cause) |
| 3 | Sound | Correct causal chain — links the specific change to the reported symptom — but missing precise details (e.g. "removed null check" without specifying which method) |
| 4 | Precise | Complete "If \<specific change\> then \<specific consequence matching symptom\>" with code-level specificity |

**Evaluation method:** LLM-as-judge. A separate LLM call receives the bug report, the suspect's mechanism + evidence, and the commit diff, then scores 0-4 against the rubric. The judge does NOT receive the ground truth `bug_hash` — it evaluates reasoning quality, not correctness.

**Why separate from Hit@k:** An agent can find the right commit (Hit@1 = 1) with a bad explanation (D3 = 1), or miss the right commit (Hit@1 = 0) but produce excellent reasoning about a plausible alternative (D3 = 3). Both cases are informative.

**Implementation status:** Implemented in `eval/d3_judge.py`. Scores top-3 suspects per case using any `LLMProvider`. Integrated into `evaluate_attribution()` via optional `d3_llm` parameter.

---

## Baselines

Baselines establish the performance floor. The agent must beat these to justify its LLM cost.

### Baseline 1: git-blame-naive

**Method:**
1. Extract file paths and CamelCase class names from the problem text via regex
2. Run `git blame` on extracted files at the temporal bound
3. Count commit SHA appearances in blame output
4. Search commit messages for extracted class names, add to counts
5. Return top 5 commits by frequency as suspects

**Confidence:** `count / sum(top 5 counts)` — frequency-based, not evidence-based.

**Strengths:** Free (zero LLM cost), fast, often finds commits that touched relevant code.

**Weaknesses:** No understanding of causation, no diff inspection, biased toward large commits with many blame lines.

### Baseline 2: file-history-recency

**Method:**
1. Extract file paths from problem text via regex
2. Find most recent commits touching those files (`search_commits_by_file`)
3. Deduplicate by commit ID, keep latest date per commit
4. Return top 5 most recent commits

**Confidence:** Rank-based (1.0, 0.8, 0.6, 0.4, 0.2) — not evidence-based.

**Strengths:** Exploits the prior that recent changes are more likely to introduce regressions.

**Weaknesses:** Biased toward active files; misses old bugs. No causation reasoning.

### Baseline 3: Random (planned)

**Method:** Sample 5 commits uniformly at random from the pre-bound history.

**Expected Hit@5:** ~0.01 (1 in ~10K commits). Useful only as an absolute floor reference.

**Implementation status:** Implemented in `eval/baselines.py`. Random baseline selects 5 commits from a pool of 500.

---

## Acceptance Thresholds

These thresholds are **provisional** — they will be calibrated after the first live eval run (n=20) based on actual baseline and agent performance.

| Metric | GATE (minimum viable) | TARGET (good) |
|--------|----------------------|---------------|
| **Hit@5** | >= 0.30 | >= 0.50 |
| **MRR** | >= 0.15 | >= 0.30 |
| **D3 Attribution** | >= 0.20 | >= 0.40 |
| **D6 Evidence** | >= 0.60 | >= 0.60 |
| **Retrieval Recall (agent)** | >= 0.40 | >= 0.60 |
| **Retrieval Recall@100 (input)** | **TBD** — after `retrieval-spike` | **TBD** — target >= 0.80 |

**GATE:** The agent must exceed all gates to be considered minimally functional.

**TARGET:** Performance goals for the optimization phase (prompt engineering, early exit, etc.).

**Key constraint:** The agent must exceed **both** baselines on Hit@5 to justify its LLM cost. If git-blame-naive achieves Hit@5 = 0.25 and the agent achieves 0.26, the marginal gain does not justify $4-6 per eval run.

---

## Cost Budget

### Per investigation

| Resource | Limit | On exceed |
|----------|-------|-----------|
| Tool calls | 30 | Force conclude (mid-batch cutoff) |
| Tokens | 100,000 | Force conclude (checked per turn) |
| Cost | $0.50 USD | Force conclude (checked per turn) |
| Turns | 15 | Loop exits |

### Per eval run (n=20)

| Resource | Estimate |
|----------|----------|
| Agent cost | $4-6 (20 cases x $0.20-0.30 avg) |
| Baseline cost | $0.00 (zero LLM) |
| JIRA API calls | 0 (pre-cached) |
| Wall-clock time | 60-90 minutes |
| Disk | ~8 GB (10 repos + ApacheJIT data) |

---

## Known Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| SZZ noise in `bug_hash` labels | Format changes and refactoring labeled as bug-introducing. Hit@1 unreliable. | Hit@5 as primary metric; gt-noise-analysis done (35% noise rate). |
| Retrieval Recall@100 ceiling | If input pipeline can't get `bug_hash` in top 100, agent fails at foundation | `retrieval-spike` task will empirically test strategies |
| Tool output truncation (8K chars) | Large diffs or blame output may lose relevant lines | Truncation appends a notice; agent can request specific line ranges via `get_blame` |
| Agent Retrieval Recall args-only | Only `tool_trace[].args.commit_id` counts; search result SHAs ignored | Agent examines from CandidateSet — V4 reduces this gap |
| Plan Quality (eval-only) | Plan Overlap Score requires eval-mode oracle (`bug_hash` files) | Metric defined in [mechanism-design ADR §9](../.harness/docs/mechanism-design.md#9-plan-quality-metric-ac12); not available in production mode |
| Skills mechanism undefined | Cannot leverage past investigations for improvement yet | Resolved in [mechanism-design ADR §Q2](../.harness/docs/mechanism-design.md#3-q2--skills-mechanism) |
| Single-LLM architecture | No multi-agent debate, no separate planning model | Harness governance provides structure; multi-agent TBD if needed |
| No re-ranking | Evidence scores don't change suspect order | Phase B deferred until forensics justify it |

---

## Related

| Document | Content |
|----------|---------|
| [system-specification.md](system-specification.md) | Three pipelines, agent framework, LLM boundary, tools |
| [agent-loop.md](agent-loop.md) | Agent loop stages 2-3-4, completion criteria, tracing |
| [datasets.md](datasets.md) | ApacheJIT data, ground truth chain |
| [glossary.md](glossary.md) | Term definitions |
| [.harness/docs/topology-debate.md](../.harness/docs/topology-debate.md) | V4 architecture decision record |
