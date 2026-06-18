# Evaluation Framework — Bug Attribution Agent

This document defines how the bug attribution system is measured. It separates **system-level metrics** (does the pipeline find the right commit?) from **output-quality metrics** (is the LLM's reasoning good?), and specifies baselines, thresholds, and known limitations.

> **Architecture status:** Updated for V4.2 (Revised Hierarchical Pipeline). Includes the 5-stage evaluation funnel. See [V4.2 ADR](../.harness/docs/v42-architecture-adr.md) for the architecture, [architecture-constraints.md](../.harness/docs/architecture-constraints.md) for NFRs.

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
| **D3 Attribution Quality** | 0-4 scale per case | Does the causal mechanism explain how the suspect introduced the bug? | LLM judge | Planned — `eval/metrics.py` implements funnel metrics; D3 judge TBD |

---

## 5-Stage Evaluation Funnel (V4.2)

V4.2 introduces a 5-stage funnel that localizes failures to specific pipeline phases. Each stage measures whether ground truth survives that phase's narrowing.

```
Recall@100 → Recall@15 → TriageRecall@7 → ExamRecall → Hit@5
  (input)     (pre-score)    (triage)       (investigation)  (attribution)
```

### Funnel Metrics

| Phase | Metric | Question | Computation | Status |
|-------|--------|----------|-------------|--------|
| 0+1a | **Retrieval Recall@100** | Is `bug_hash` in CandidateSet? | `1 if bug_hash in candidate_set` | Calibrated (gate ≥ 0.35) |
| 1a | **Pre-score Recall@15** | Is `bug_hash` in ScoredShortlist? | `1 if bug_hash in top_15_by_pre_score` | New — V4.2 |
| 1b | **Triage Recall@7** | Is `bug_hash` in must_examine ∪ watchlist? | `1 if bug_hash in top_7_by_pre_score` | Deterministic (zero LLM dropout) |
| 2 | **Examination Recall** | Did agent call `get_commit_diff` on `bug_hash`? | `1 if bug_hash in diff_args` | Renamed from "Retrieval Recall (agent)" |
| Final | **Hit@k**, **MRR** | Is `bug_hash` in top k suspects? | Standard ranking metrics | Implemented |

All funnel metrics are **eval-only** (require `ground_truth_sha`). They must never leak into investigation prompts (oracle isolation invariant).

### Failure localization examples

| Funnel state | Diagnosis |
|--------------|-----------|
| Recall@100=0 | Retrieval failure — GT not in candidate set |
| Recall@100=1, Recall@15=0 | Pre-score dropped GT — weight calibration needed |
| Recall@15=1, TriageRecall@7=0 | Triage vetoed GT — LLM ranking failure or script-anchor bypass |
| TriageRecall@7=1, ExamRecall=0 | Agent didn't examine GT despite it being in triaged set — strategy problem |
| ExamRecall=1, Hit@5=0 | Agent examined GT but didn't rank it as suspect — reasoning problem |

### Legacy metric disambiguation

| V4.1 Metric | V4.2 Equivalent | Notes |
|-------------|-----------------|-------|
| Retrieval Recall@100 (input) | Retrieval Recall@100 | Unchanged |
| Retrieval Recall (agent) | Examination Recall | Renamed for clarity in funnel context |

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

**V4.1 pipeline stage:** Scoped investigation. The agent examines candidates from the `CandidateSet` via scoped tools.

**Computation:**
```
commit_ids = {tool_trace[i].args.commit_id for all tool calls where commit_id present}
retrieval_recall = 1 if bug_hash[:12] in {cid[:12] for cid in commit_ids}, else 0
```

**Not counted:** SHAs appearing only in tool **result text** — only `args.commit_id` on tool trace records matters.

**Why it matters:** Separates examination quality from ranking quality. If agent retrieval recall is high but Hit@5 is low, the agent examined the right commit but did not rank it — a reasoning problem. If agent retrieval recall is low but Retrieval Recall@100 is high, the candidate set contained ground truth but the agent never examined it — a planning/strategy problem.

### Retrieval Recall@100 (input pipeline)

**What it measures:** Whether the input pipeline's `CandidateSet` contains the ground truth `bug_hash`. Measures retrieval quality independently of the agent.

**Pipeline stage:** Stage 1 (Candidate Retrieval).

**Computation:**
```
retrieval_recall_100 = 1 if bug_hash[:12] in {c.commit_id[:12] for c in candidate_set.commits}, else 0
```

**Why it matters:** Foundational metric for V4. If `bug_hash` is not in the candidate set, the agent cannot succeed regardless of reasoning quality. A low Retrieval Recall@100 indicates an input pipeline failure, not an agent failure. Gate: **>= 0.35**, Target: **>= 0.60** — calibrated from [retrieval-spike findings](../.harness/docs/retrieval-spike-findings.md). Level 1 extraction achieves 0.45; Level 2 (LLM-synthesized) expected to reach 0.55-0.65.

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

**Implementation status:** `eval/metrics.py` implements the 5-stage funnel (Hit@k, MRR, FunnelMetrics). D3 LLM-as-judge scoring is planned but not yet implemented in the eval module.

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

**Implementation status:** Baseline methods documented above. `eval/metrics.py` implements the core scoring (Hit@k, MRR, FunnelMetrics). Baseline runners not yet re-implemented.

### V4.2 Results

| Configuration | Hit@5 | MRR | n | Notes |
|---------------|-------|-----|---|-------|
| V4.2 Cursor SDK (claude-sonnet-4-6) | 0.800 | 0.600 | 5 | Cloud model via CursorSDKProvider |
| V4.2 local gemma3:12b (q8_0) | 0.250 | 0.225 | 20 | Local Ollama, full eval set |
| V3 fully agentic (historical) | 0.500 | 0.304 | 20 | Full repo tools, deleted |

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
| **Retrieval Recall@100 (input)** | >= 0.35 | >= 0.60 |

**GATE:** The agent must exceed all gates to be considered minimally functional.

**TARGET:** Performance goals for the optimization phase (prompt engineering, early exit, etc.).

**Key constraint:** The agent must exceed **both** baselines on Hit@5 to justify its LLM cost. If git-blame-naive achieves Hit@5 = 0.25 and the agent achieves 0.26, the marginal gain does not justify $4-6 per eval run.

---

## Cost Budget

### Per investigation

| Resource | V3 | V4.1 | V4.2 | On exceed |
|----------|-----|------|------|-----------|
| Tool calls | 30 | 15 | ~23 (15 + 8 overflow) | Force conclude |
| Turns | 15 | 8 | 8 + 4 (2b) | Loop exits |
| Tokens | 100,000 | — | ~70-120K est. | — |
| Cost | $0.50 USD | — | ~$0.10-0.15 | — |

### Per eval run (n=20)

| Resource | V4.2 Estimate |
|----------|---------------|
| Agent cost | $2-3 (20 cases × $0.10-0.15 avg) |
| Baseline cost | $0.00 (zero LLM) |
| JIRA API calls | 0 (pre-cached) |
| Wall-clock time | 40-60 minutes |
| Disk | ~8 GB (10 repos + ApacheJIT data) |

---

## Known Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| SZZ noise in `bug_hash` labels | Format changes and refactoring labeled as bug-introducing. Hit@1 unreliable. | Hit@5 as primary metric; gt-noise-analysis done (35% noise rate). |
| Retrieval Recall@100 ceiling | If input pipeline can't get `bug_hash` in top 100, agent fails regardless of reasoning | Current: 0.40 (8/20). Level 2 extraction expected to reach 0.60. |
| Tool output truncation (8K chars) | Large diffs or blame output may lose relevant lines | Truncation appends a notice; agent can request specific line ranges via `get_blame` |
| Pre-score may drop GT | If GT ranks 16+ by pre_score, Recall@15 = 0 | Pre-implementation gate: measure Recall@15 on n=20 oracle before locking weights |
| Triage may veto GT | Deterministic triage could exclude GT from must-examine + watchlist if GT ranks 8+ by pre_score | Script-anchored triage: top 3 by pre_score are must-examine, next 4 are watchlist — fully deterministic |
| Provider-dependent tool format | ```tool block compliance varies by model | Compliance spike required before n=20 eval |
| V4.2 eval data limited | Cursor SDK eval n=5 only; local model n=20 shows lower performance | Expand Cursor SDK eval to n=20 for statistical significance |
| Skills mechanism not integrated | Prompts don't inject investigation skills yet | Deferred to V4.2 — skills integration TBD |

---

## Related

| Document | Content |
|----------|---------|
| [system-specification.md](system-specification.md) | Three pipelines, data structures, LLM boundary, tools |
| [agent-loop.md](agent-loop.md) | V4.2 agent loop (phases 1b-2-2b) |
| [datasets.md](datasets.md) | ApacheJIT data, ground truth chain |
| [glossary.md](glossary.md) | Term definitions |
| [.harness/docs/v42-architecture-adr.md](../.harness/docs/v42-architecture-adr.md) | V4.2 architecture decision |
| [.harness/docs/architecture-constraints.md](../.harness/docs/architecture-constraints.md) | NFRs and invariants |
