# Evaluation Framework — Bug Attribution Agent (V3)

This document defines how the bug attribution system is measured. It separates **system-level metrics** (does the pipeline find the right commit?) from **output-quality metrics** (is the LLM's reasoning good?), and specifies baselines, thresholds, and known limitations.

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
| **D3 Attribution Quality** | 0-4 scale per case | Does the causal mechanism explain how the suspect introduced the bug? | LLM judge | **Not yet implemented** |

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

### Retrieval Recall

**What it measures:** Whether the agent ever *looked at* the bug-introducing commit during its search, regardless of whether it ranked it as a suspect.

**Computation:**
```
all commit_ids referenced in tool_trace[].args
retrieval_recall = 1 if bug_hash[:12] in {traced_commit[:12] for all traced commits}
```

**Why it matters:** Separates search quality from ranking quality. If retrieval recall is high but Hit@5 is low, the agent sees the right commit but doesn't recognize it — a reasoning problem. If retrieval recall is low, the agent isn't searching in the right places — a search strategy problem.

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

### D3 Attribution Quality (design — not yet implemented)

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

**Implementation status:** Rubric defined. LLM judge not yet implemented (task `d3-llm-judge`, P7).

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

**Implementation status:** Not yet implemented (task `random-baseline`, P6).

---

## Acceptance Thresholds

These thresholds are **provisional** — they will be calibrated after the first live eval run (n=20) based on actual baseline and agent performance.

| Metric | GATE (minimum viable) | TARGET (good) |
|--------|----------------------|---------------|
| **Hit@5** | >= 0.30 | >= 0.50 |
| **MRR** | >= 0.15 | >= 0.30 |
| **D3 Attribution** | >= 0.20 | >= 0.40 |
| **D6 Evidence** | >= 0.60 | >= 0.60 |
| **Retrieval Recall** | >= 0.40 | >= 0.60 |

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
| SZZ noise in `bug_hash` labels | Format changes and refactoring labeled as bug-introducing. Hit@1 unreliable. | Hit@5 as primary metric; manual review planned (task `gt-noise-analysis`). |
| Tool output truncation (8K chars) | Large diffs or blame output may lose relevant lines | Truncation appends a notice; agent can request specific line ranges via `get_blame` |
| Tool trace truncation (500 chars) | Retrieval Recall may miss commits referenced only in truncated results | Full results still sent to LLM; only the trace record is truncated |
| No D3 judge | Cannot evaluate reasoning quality in V3 today | Rubric defined above; implementation planned (task `d3-llm-judge`) |
| Advisory phases | Agent may ignore the suggested 5-phase strategy entirely | Acceptable — the agent is measured on outcomes, not process |
| Single-LLM architecture | No multi-agent debate, no separate planning model | Simplicity first; reconsider if Hit@5 plateaus |
| No re-ranking | Evidence scores don't change suspect order | Phase B deferred until forensics justify it |

---

## Related

| Document | Content |
|----------|---------|
| [system-specification.md](system-specification.md) | Pipeline stages, LLM boundary, agent loop, tools |
| [datasets.md](datasets.md) | ApacheJIT data, ground truth chain |
| [glossary.md](glossary.md) | Term definitions |
