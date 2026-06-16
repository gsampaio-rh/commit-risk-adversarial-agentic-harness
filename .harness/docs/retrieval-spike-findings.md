# Retrieval Spike Findings

**Status:** Accepted  
**Date:** 2026-06-16  
**Supersedes:** None  
**Related:** [topology-debate](.harness/docs/topology-debate.md), [mechanism-design](.harness/docs/mechanism-design.md)

## Executive Summary

Empirically tested 7 git-based retrieval strategies against the frozen n=20 eval set (seed=42) to determine which strategy combination achieves the best recall@100 for V4's input pipeline. The **combined strategy** (file_log + keyword_grep + pickaxe + blame) with signal-count ranking achieves **recall@100 = 0.450 (9/20)** using Level 1 regex extraction. Blame is the strongest individual strategy at 0.350 (7/20). Two critical implementation requirements were discovered: file path resolution (bare filename → full repo path) and SHA normalization (blame short SHAs → full 40-char).

## Q1: Which Git Retrieval Strategies Achieve Best recall@100?

### Individual Strategy Results

| Strategy | recall@100 | Hits | Top Hit Cases |
|----------|-----------|------|---------------|
| blame | 0.350 | 7/20 | AMQ-4338@3, FLINK-3602@9, CASSANDRA-7570@9 |
| file_log | 0.200 | 4/20 | AMQ-4338@1, CASSANDRA-7570@9, SPARK-27907@22 |
| keyword_grep | 0.200 | 4/20 | AMQ-4338@12, SPARK-2583@16, SPARK-19033@89 |
| pickaxe | 0.200 | 4/20 | AMQ-4338@2, SPARK-2583@4, GROOVY-8416@33 |
| recency_fallback | 0.050 | 1/20 | GROOVY-8416@42 |
| time_window_90d | 0.050 | 1/20 | GROOVY-8416@42 |
| time_window_365d | 0.050 | 1/20 | GROOVY-8416@42 |

### Combined Strategy Results

| K | recall@K | Hits | Δ from K-1 |
|---|----------|------|------------|
| 50 | 0.300 | 6/20 | — |
| 100 | 0.450 | 9/20 | +50% |
| 200 | 0.500 | 10/20 | +11% |

Combined components: `file_log`, `keyword_grep`, `pickaxe`, `blame` (no recency — adds 200 noise candidates).  
Ranking: number of strategies finding the commit (desc), best rank across strategies (asc).  
Fallback: recency for cases with < 10 signal-driven candidates.

Union ceiling across individual strategies at K=100: 0.550 (11/20). Combined captures 9/11 — loses 2 edge cases where a commit is found by only one strategy at rank 87-89, getting pushed past K=100 by multi-strategy commits.

### Recommended Strategy for P16

**Combined (file_log + keyword_grep + pickaxe + blame)** with:
1. **File path resolution**: resolve bare filenames to full repo paths via `git ls-tree` (critical — without this, file_log and blame return 0 candidates)
2. **SHA normalization**: resolve blame output abbreviated SHAs to full 40-char SHAs (critical — without this, blame hits don't match other strategies' results)
3. **Signal-count ranking**: commits found by more strategies rank higher; ties broken by best individual rank
4. **Recency fallback**: only when signal-driven strategies produce < 10 candidates (zero-signal cases)

## Q2: Optimal Candidate Set Size

**Recommended: K=100.** The recall curve shows strong gains from K=50 to K=100 (+50%), but diminishing returns from K=100 to K=200 (+11%). K=100 provides the best tradeoff between recall and signal-to-noise ratio for the downstream LLM.

| K | recall | Hits | Median Candidates |
|---|--------|------|-------------------|
| 50 | 0.300 | 6/20 | ~120 |
| **100** | **0.450** | **9/20** | ~170 |
| 200 | 0.500 | 10/20 | ~180 |

The 10th hit (at K=200) is SPARK-19033 at rank 129 — marginal value.

## Q3: Does Level 2 Extraction Improve Retrieval?

**Level 2 is needed.** Level 1 combined recall@100 = 0.450, below the 0.70 skip threshold.

### Level 1 Extraction Coverage

- 20/20 cases have signals (keywords are universal)
- Only 7/20 cases have file path signals
- Only 12/20 cases have CamelCase symbol signals
- 5/9 perpetual misses have **zero file/symbol signals** (keywords only)

### Miss Pattern Analysis

| Pattern | Count | Cases | Level 2 Potential |
|---------|-------|-------|-------------------|
| Keywords only (no file/symbol signals) | 5 | SPARK-20123, GROOVY-5003, SPARK-23059, HBASE-4577, GROOVY-5775 | **High** — LLM could extract component/file names from natural language |
| Signals present but miss | 3 | HIVE-4113, GROOVY-7014, SPARK-946 | **Medium** — better signals might help |
| SZZ noise case | 1 | GROOVY-8298 | **None** — ground truth is noisy |

### Recommendation

Proceed with P8 (spike-level2-extractor). Target: LLM-synthesized extraction of file paths, class names, and component identifiers from JIRA text. Expected improvement: 3-5 additional hits from the 5 keyword-only miss cases, bringing combined recall@100 to ~0.55-0.65.

### Level 1 vs Level 2 Comparison (Combined Strategy)

Level 1 combined recall@100 = **0.450** — below the **0.70 skip threshold**. Level 2 (LLM-synthesized extraction) was **not run in this spike** — deferred to `spike-level2-extractor` (P8) to isolate git retrieval strategy variables from extraction quality.

| Metric | Level 1 (regex, measured) | Level 2 (LLM, deferred) | Notes |
|--------|---------------------------|-------------------------|-------|
| recall@50 | 0.300 (6/20) | Not measured — projected 0.35–0.40 | P8 follow-up |
| recall@100 | 0.450 (9/20) | Not measured — projected 0.55–0.65 | 5 keyword-only misses |
| recall@200 | 0.500 (10/20) | Not measured — projected 0.60–0.70 | Marginal K=200 gains |

**Deferral rationale:** This spike scope is git-based retrieval only (AC12: no embeddings). Level 2 requires LLM infrastructure not yet built. Running L2 here would conflate extraction and retrieval effects. Full comparison artifact: `results/retrieval-spike/level2_comparison.json`.

## Q4: Time-Window Bounding Effect

**No benefit.** Both 90-day and 365-day time windows achieve recall@100 = 0.050 (1/20) — identical to pure recency fallback. Time-window filtering without signal-based search is useless because:

1. Bug-inducing commits can be arbitrarily old relative to the fix
2. Date filtering alone provides no semantic signal
3. The temporal bound (`fix_hash~1`) already constrains the search space

**Recommendation:** Do not include time-window as a standalone strategy in V4. It may have marginal utility as a post-filter on combined results (prefer recent commits when signal counts are tied), but this was not empirically tested.

## Critical Implementation Requirements

### 1. File Path Resolution

Bare filenames extracted from JIRA text (e.g., `MyClass.java`) do not match git's path format (e.g., `src/main/java/org/project/MyClass.java`). Without resolution:
- file_log: recall@100 = 0.000
- blame: recall@100 = 0.000

With resolution via `git ls-tree -r --name-only <ref>`:
- file_log: recall@100 = 0.200
- blame: recall@100 = 0.350

Resolution also maps CamelCase symbols to file paths by trying common extensions (`.java`, `.groovy`, `.scala`, `.py`).

### 2. SHA Normalization

`git blame` outputs abbreviated SHAs (7-8 chars) that do not match full 40-char SHAs from `git log`. Without normalization, blame commits cannot be deduplicated or matched with other strategies' results.

Fix: resolve each abbreviated SHA via `git rev-parse`.

## Proposed Retrieval Recall@100 Gate/Target

Based on the spike results and V3 baseline calibration:

| Metric | Value | Rationale |
|--------|-------|-----------|
| **Gate** (minimum viable) | 0.35 | Current blame-alone achieves 0.35; combined must beat the best individual |
| **Target** (aspirational) | 0.60 | Requires Level 2 extraction improvements; 5 keyword-only misses are recoverable |
| **Stretch** | 0.80 | Requires both Level 2 extraction and strategy improvements; may not be achievable with SZZ noise |

Note: 1/20 eval cases (GROOVY-8298) is a confirmed SZZ noise case with zero file overlap between bug and fix commits. Adjusting for noise: effective recall@100 on clean cases is 9/19 = 0.474.

## P16 Implementation Notes

The v4-retrieval-module (P16) should implement:

1. **CandidateSet builder** from combined strategy (file_log + keyword_grep + pickaxe + blame)
2. **File path resolution** via cached `git ls-tree` (cache per repo per temporal bound)
3. **SHA normalization** for blame output
4. **Signal-count ranking** with recency tiebreaker
5. **Recency fallback** for sparse-signal cases (< 10 candidates)
6. **Retrieval metadata** on each CandidateCommit: which strategies found it, at what rank
7. Default `max_candidates=100` (configurable)

Do NOT implement:
- Time-window filtering (no empirical benefit)
- Embedding/vector retrieval (mechanism-design ADR constraint)
- Level 2 extraction (separate task P8)

## Reproducibility

Spike code was deleted after research completion (research artifacts, not production code). Results are frozen in `results/retrieval-spike/`. The original commands were:

```bash
# Historical — spike scripts no longer exist; results are frozen
python scripts/run_retrieval_spike.py --strategy all --manifest results/v3-subagent-eval-v2/manifest.json
python scripts/run_retrieval_spike.py --hint-coverage
```

Results: `results/retrieval-spike/summary.json`  
Permanent infrastructure: `git_context.py` (pickaxe, resolve_file_path), `problem_extractor.py` (extracted_keywords)
