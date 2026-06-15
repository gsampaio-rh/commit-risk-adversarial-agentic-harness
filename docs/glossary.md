# Glossary

Terms used across the codebase, harness, evaluation, and agent sessions. Organized by domain.

## Dataset

| Term | Definition |
|------|------------|
| **ApacheJIT** | Labeled commit dataset from 15 Apache projects (Keshavarz & Nagappan, MSR 2022, [Zenodo 5907847](https://zenodo.org/records/5907847)). Provides numeric change metrics, buggy labels, and a replication package linking bug-inducing commits to fixes and JIRA issues. |
| **buggy** | CSV column. `True` means the commit **introduced** a defect later fixed by another commit. Assigned retrospectively via SZZ-style linkage. See [temporal-model.md](temporal-model.md). |
| **fix** (CSV column) | `True` means the commit is itself a fix for a previously introduced defect. Excluded from agent context (oracle field). |
| **bug_hash** | SHA of the bug-introducing commit in the replication linkage files (`commit_links_{PROJECT}.csv`). |
| **fix_hash** | SHA of the fixing commit in the replication linkage files. Maps back to `bug_hash` via the ground truth chain. |
| **issue_key** | JIRA issue identifier (e.g., `CAMEL-12978`) linked to a commit in `{PROJECT}.csv`. Used at eval time to fetch JIRA metadata for D3/D4/D5. |
| **SZZ** | Algorithm family (Sliwerski-Zimmermann-Zeller) that traces fix commits back to the commits that introduced the bug. ApacheJIT uses SZZ with GumTree filtering. |
| **ground truth chain** | The full linkage: `bug_hash → fix_hash → issue_key → JIRA metadata`. Resolved by `GroundTruthGraph` at eval time only. |

## Pipeline

| Term | Definition |
|------|------------|
| **SUPPORTED** | Evidence tier assigned by `evidence_tagger.py`. The hypothesis includes an `evidence_quote` that verifiably appears in the diff (exact, normalized, or fuzzy match). |
| **SPECULATIVE** | Evidence tier assigned by `evidence_tagger.py`. The hypothesis has no verifiable quote in the diff, or the quote doesn't match. |
| **clean_archetype** | Pattern detected by `archetype.py` indicating the commit is likely innocuous (e.g., pure whitespace, import-only, version bump). Causes `risk_policy.py` to cap risk at `MEDIUM`. |
| **defect_signals** | Archetype-level indicators of likely defects (e.g., concurrency patterns, error-handling removal). Detected by `archetype.py`; bias risk verdict toward `HIGH`. |
| **router_probability** | XGBoost-predicted probability that a commit is buggy. Used as a prior signal in hypothesis generation and confidence scoring. Not a risk verdict — `risk_policy.py` owns the final risk. |
| **confidence tier** | `HIGH` (score >= 0.65), `MEDIUM` (0.40–0.65), or `LOW` (< 0.40). Computed by `confidence_model.py` from 7 weighted signals. `LOW` tier caps risk at `MEDIUM` in `risk_policy.py`. |
| **historical defect context** | Optional KNN-based retrieval of defect-category priors from train-split buggy commit messages. Enabled via `--enable-historical-defect-context`. Never uses fix-commit content. Module: `hypothesis/historical_rag.py`. |
| **contrastive hypotheses** | Secondary hypotheses generated to challenge the primary. Prompt-enforced diversity; post-hoc reordered by `select_primary_by_evidence`. |

## Evaluation

| Term | Definition |
|------|------------|
| **D1** (Prediction) | Agent risk level vs `buggy`/`clean` label. Deterministic. `HIGH`/`CRITICAL` on buggy = correct; `MEDIUM` on buggy = fail. |
| **D2** (Localization) | Jaccard overlap of agent-cited files vs fix-commit diff files. Deterministic. Test/doc files filtered out. |
| **D3** (Diagnosis) | LLM-as-judge comparing agent reasoning to JIRA issue description (or fix-diff fallback). Rubric 0–4. |
| **D4** (Severity) | Agent risk level vs JIRA priority (normalized mapping). Deterministic. |
| **D5** (Recommendations) | LLM-as-judge comparing agent recommendations to actual fix pattern. Rubric 0–3. |
| **D6** (Evidence grounding) | Automated check that agent cites real artifacts from the diff. Zero LLM cost, no ground truth chain needed. |
| **judge_oracle** | Tag in D3/D5 `DimensionScore.details` recording which oracle was used: `jira` (JIRA description), `fix-diff-fallback` (fix commit files), or `unavailable` (no oracle). |
| **fix-diff-fallback** | When JIRA description is absent, D3/D5 judge uses the set of files touched by the fix commit as oracle context instead. Same rubric, different grounding source. |
| **hard panel** | Subset of commits with known difficulty — used for targeted forensics and regression tracking across eval runs. |
| **FP rate** | False positive rate: proportion of `clean` commits the agent labels as `HIGH` risk. |
| **wrong-mechanism** | D3 failure mode where the agent identifies the right area of concern but the wrong underlying mechanism (e.g., "thread safety" when the real bug is "classloader isolation"). ~57% of D3 partial-score failures. |
| **missing-context** | D3 failure mode where the agent lacks information needed to identify the root cause (e.g., JIRA context, downstream dependencies). ~9% of D3 failures. |

## Architecture

| Term | Definition |
|------|------------|
| **script-first** | Design principle: deterministic scripts (archetype detection, evidence tagging, risk policy, quality gates, confidence scoring) own all classification decisions. The LLM generates hypotheses; scripts grade them. Frozen in V2 — not subject to iteration. |
| **oracle isolation** | The rule that ground truth data (buggy label, fix commits, JIRA metadata, chain linkage) must **never** enter investigation context. Enforced by allowlist + dedicated tests. See [temporal-model.md](temporal-model.md). |
| **allowlist** | The set of 12 numeric CSV features (`_NUMERIC_FEATURES` in `context_builder.py`) permitted in agent context. Everything else from the CSV row is excluded. |
| **trust boundary** | The architectural split between INVESTIGATION (commit-time only) and EVALUATION (ground truth access). Visualized in [architecture.md §6](architecture.md). |
| **quality gate** | Deterministic checks (`quality_gate.py`) on hypothesis quality. Triggers T1–T4 control follow-up requests and report annotations. |

## Related

- [temporal-model.md](temporal-model.md) — temporal boundary, worked example, SZZ definition
- [evaluation.md](evaluation.md) — D1–D6 rubrics, thresholds, sampling strategy
- [architecture.md](architecture.md) — system design, pipeline stages, trust boundary diagram
- [datasets.md](datasets.md) — ApacheJIT splits, CSV features, ground truth chain files
