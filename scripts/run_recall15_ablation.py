#!/usr/bin/env python3
"""V4.2 Gate: Recall@15 ablation with pre-score formula.

Tests whether the pre-score formula can narrow 100 candidates to 15
while keeping ground truth in the top 15.

Formula: w_fo·file_overlap + w_sc·norm(signal_count) + w_rk·(1 - norm(rank))

Gate: GT in top 15 on >=80% of retrievable cases (>=6/8 where Recall@100=true).
Tests weight sensitivity with 5 weight variants.

Usage:
    python scripts/run_recall15_ablation.py
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commit_investigator.eval.ground_truth import GroundTruthGraph
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.models.candidates import CandidateCommit
from commit_investigator.retrieval import (
    RetrievalConfig,
    compute_recall_at_k,
    prepare_investigation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JIRA_CACHE_DIR = PROJECT_ROOT / "data" / "jira_cache"
REPOS_DIR = PROJECT_ROOT / "data" / "repos"
ZIP_PATH = PROJECT_ROOT / "data" / "apachejit" / "apachejit_dataset_replication.zip"
RESULTS_PATH = PROJECT_ROOT / "results" / "v4-checkpoints" / "recall15-ablation.json"
RECALL100_PATH = PROJECT_ROOT / "results" / "v4-checkpoints" / "retrieval-recall.json"

GATE = 0.80

WEIGHT_VARIANTS = [
    {"name": "default", "file_overlap": 0.5, "signal_count": 0.3, "rank": 0.2},
    {"name": "file_heavy", "file_overlap": 0.7, "signal_count": 0.2, "rank": 0.1},
    {"name": "signal_heavy", "file_overlap": 0.3, "signal_count": 0.5, "rank": 0.2},
    {"name": "rank_heavy", "file_overlap": 0.3, "signal_count": 0.2, "rank": 0.5},
    {"name": "equal", "file_overlap": 0.33, "signal_count": 0.34, "rank": 0.33},
]


@dataclass
class EvalCase:
    issue_key: str
    project: str
    bug_hashes: list[str]
    fix_hash: str
    repo_path: Path
    temporal_bound: str


@dataclass(frozen=True)
class ScoredCandidate:
    commit: CandidateCommit
    pre_score: float
    file_overlap: float
    signal_count: int


def load_jira_text(issue_key: str) -> dict[str, str] | None:
    path = JIRA_CACHE_DIR / f"{issue_key}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    return {
        "title": raw["fields"]["summary"],
        "description": raw["fields"].get("description") or "",
    }


def build_eval_cases(gt: GroundTruthGraph) -> list[EvalCase]:
    """Reconstruct eval cases from retrieval-recall.json + ground truth graph."""
    recall_data = json.loads(RECALL100_PATH.read_text(encoding="utf-8"))

    cases: list[EvalCase] = []
    for case_data in recall_data["cases"]:
        issue_key = case_data["issue_key"]
        project = case_data["project"]

        fix_hash, bug_hashes = _find_hashes(gt, issue_key)
        if not fix_hash or not bug_hashes:
            print(f"  WARN {issue_key}: could not resolve fix/bug chain in GT")
            continue

        repo_path = REPOS_DIR / project.lower()
        cases.append(EvalCase(
            issue_key=issue_key,
            project=project,
            bug_hashes=bug_hashes,
            fix_hash=fix_hash,
            repo_path=repo_path,
            temporal_bound=f"{fix_hash}~1",
        ))
    return cases


def _find_hashes(gt: GroundTruthGraph, issue_key: str) -> tuple[str, list[str]]:
    """Find (fix_hash, all_bug_hashes) for an issue key via GT graph."""
    commits = gt._issue_to_commits.get(issue_key, [])
    for commit_id in commits:
        if gt.has_fix(commit_id):
            bug_hashes = gt.get_bug_commits(commit_id)
            if bug_hashes:
                return commit_id, bug_hashes
    return "", []


def _file_matches(changed_file: str, extracted_hint: str) -> bool:
    """Check if a changed file path matches an extracted file hint (suffix match)."""
    cf = changed_file.lower()
    eh = extracted_hint.lower()
    return cf == eh or cf.endswith("/" + eh)


def compute_file_overlap(
    files_changed: list[str],
    extracted_files: list[str],
) -> float:
    """Fraction of extracted files matched by at least one changed file."""
    if not extracted_files:
        return 0.0
    matches = sum(
        1 for ef in extracted_files
        if any(_file_matches(fc, ef) for fc in files_changed)
    )
    return matches / len(extracted_files)


def get_signal_count(candidate: CandidateCommit) -> int:
    parts = [s for s in candidate.retrieval_signal.split(",")
             if s and s != "recency_fallback"]
    return len(parts)


def compute_pre_scores(
    candidates: list[CandidateCommit],
    problem: ProblemStatement,
    weights: dict[str, float],
) -> list[ScoredCandidate]:
    """Score all candidates and return sorted by pre_score descending."""
    if not candidates:
        return []

    max_signals = max(get_signal_count(c) for c in candidates) or 1
    n = len(candidates)

    scored: list[ScoredCandidate] = []
    for c in candidates:
        fo = compute_file_overlap(c.files_changed, problem.extracted_files)
        sc = get_signal_count(c)

        norm_sc = sc / max_signals
        norm_rank = (c.rank - 1) / (n - 1) if n > 1 else 0.0

        pre_score = (
            weights["file_overlap"] * fo
            + weights["signal_count"] * norm_sc
            + weights["rank"] * (1 - norm_rank)
        )
        scored.append(ScoredCandidate(
            commit=c, pre_score=pre_score,
            file_overlap=fo, signal_count=sc,
        ))

    return sorted(scored, key=lambda s: (-s.pre_score, s.commit.commit_id))


def find_gt_position(
    scored: list[ScoredCandidate],
    bug_hashes: list[str],
) -> dict:
    """Find best GT commit position across all bug hashes."""
    targets = {bh.lower() for bh in bug_hashes}
    best: dict | None = None
    for i, sc in enumerate(scored, start=1):
        if sc.commit.commit_id.lower() in targets:
            entry = {
                "in_top15": i <= 15,
                "rank": i,
                "pre_score": round(sc.pre_score, 4),
                "file_overlap": round(sc.file_overlap, 4),
                "signal_count": sc.signal_count,
                "original_rank": sc.commit.rank,
                "matched_hash": sc.commit.commit_id,
            }
            if best is None or i < best["rank"]:
                best = entry
    if best is not None:
        return best
    return {"in_top15": False, "rank": None, "pre_score": None,
            "file_overlap": None, "signal_count": None,
            "original_rank": None, "matched_hash": None}


def main() -> None:
    print("Loading ground truth graph...")
    gt = GroundTruthGraph.from_replication_zip(str(ZIP_PATH))

    print("Building eval cases from retrieval-recall.json...")
    cases = build_eval_cases(gt)
    print(f"  Resolved {len(cases)} cases\n")

    results_per_case: list[dict] = []
    start_time = time.time()

    for case in cases:
        jira = load_jira_text(case.issue_key)
        if jira is None:
            print(f"  SKIP {case.issue_key}: JIRA cache missing")
            results_per_case.append({
                "issue_key": case.issue_key, "project": case.project,
                "status": "skipped", "reason": "jira_cache_missing",
            })
            continue

        if not (case.repo_path / ".git").exists():
            print(f"  SKIP {case.issue_key}: repo not found at {case.repo_path}")
            results_per_case.append({
                "issue_key": case.issue_key, "project": case.project,
                "status": "skipped", "reason": "repo_not_found",
            })
            continue

        try:
            result = prepare_investigation(
                source=(jira["title"], jira["description"]),
                repo_path=case.repo_path,
                temporal_bound=case.temporal_bound,
                project=case.project,
                issue_key=case.issue_key,
            )
        except Exception as e:
            print(f"  ERR  {case.issue_key}: {e}")
            results_per_case.append({
                "issue_key": case.issue_key, "project": case.project,
                "status": "error", "reason": str(e),
            })
            continue

        best_r100 = None
        for bh in case.bug_hashes:
            diag = compute_recall_at_k(result.candidate_set, bh, k=100)
            if diag.found and (best_r100 is None or diag.rank < best_r100.rank):
                best_r100 = diag

        retrievable = best_r100 is not None and best_r100.found
        problem = result.problem_statement

        variant_results: dict[str, dict] = {}
        for variant in WEIGHT_VARIANTS:
            weights = {k: variant[k] for k in ("file_overlap", "signal_count", "rank")}
            scored = compute_pre_scores(result.candidate_set.commits, problem, weights)
            variant_results[variant["name"]] = find_gt_position(scored, case.bug_hashes)

        r100_rank = best_r100.rank if best_r100 else None
        r100_strats = best_r100.strategies_that_found if best_r100 else []
        case_result = {
            "issue_key": case.issue_key,
            "project": case.project,
            "status": "retrievable" if retrievable else "not_retrievable",
            "recall_100_rank": r100_rank,
            "total_candidates": len(result.candidate_set.commits),
            "n_bug_hashes": len(case.bug_hashes),
            "strategies": r100_strats,
            "extracted_files": problem.extracted_files[:10],
            "n_extracted_files": len(problem.extracted_files),
            "variants": variant_results,
        }
        results_per_case.append(case_result)

        if retrievable:
            default = variant_results["default"]
            marker = "HIT" if default["in_top15"] else "MISS"
            print(
                f"  {marker:4s} {case.issue_key:16s} "
                f"R@100={r100_rank:>3d}  →  pre-score rank={default['rank']:>3d}  "
                f"file_overlap={default['file_overlap']:.2f}  "
                f"signals={default['signal_count']}  "
                f"({len(case.bug_hashes)} bug hashes)"
            )
        else:
            print(f"  ---  {case.issue_key:16s}  not in top 100 "
                  f"({len(case.bug_hashes)} bug hashes)")

    elapsed = time.time() - start_time

    retrievable_cases = [c for c in results_per_case if c.get("status") == "retrievable"]
    n_retrievable = len(retrievable_cases)

    print(f"\n{'='*72}")
    print(f"RECALL@15 ABLATION  (n_retrievable={n_retrievable}, gate={GATE:.0%})")
    print(f"{'='*72}")

    variant_summaries: dict[str, dict] = {}
    best_variant = ""
    best_recall = -1.0

    for variant in WEIGHT_VARIANTS:
        name = variant["name"]
        hits = sum(
            1 for c in retrievable_cases
            if c["variants"][name]["in_top15"]
        )
        recall = hits / n_retrievable if n_retrievable > 0 else 0.0
        passed = recall >= GATE

        variant_summaries[name] = {
            "weights": {k: variant[k] for k in ("file_overlap", "signal_count", "rank")},
            "hits": hits,
            "n_retrievable": n_retrievable,
            "recall_15": round(recall, 4),
            "gate": GATE,
            "gate_passed": passed,
        }

        tag = "PASS" if passed else "FAIL"
        print(f"  {name:15s}  {recall:.2f} ({hits}/{n_retrievable})  [{tag}]  "
              f"w={variant['file_overlap']:.1f}/{variant['signal_count']:.1f}/{variant['rank']:.1f}")

        if recall > best_recall:
            best_recall = recall
            best_variant = name

    print(f"\n  Best variant: {best_variant} (Recall@15 = {best_recall:.2f})")
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"{'='*72}")

    output = {
        "checkpoint": "recall-15-ablation",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gate": GATE,
        "n_total": len(results_per_case),
        "n_retrievable": n_retrievable,
        "elapsed_s": round(elapsed, 1),
        "best_variant": best_variant,
        "best_recall_15": round(best_recall, 4),
        "gate_passed": best_recall >= GATE,
        "variants": variant_summaries,
        "cases": results_per_case,
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"\nResults written to {RESULTS_PATH}")

    sys.exit(0 if best_recall >= GATE else 1)


if __name__ == "__main__":
    main()
