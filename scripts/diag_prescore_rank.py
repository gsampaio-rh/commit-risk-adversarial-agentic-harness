#!/usr/bin/env python3
"""Diagnostic: find actual pre-score rank of ground truth commits.

For cases where GT is in Recall@100 but drops out of top-15 pre-scoring,
compute the full scored list and report the GT rank, score components,
and a verdict on whether shortlist expansion or formula change is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commit_investigator.eval.ground_truth import GroundTruthGraph
from commit_investigator.eval.helpers import (
    ZIP_PATH,
    EvalCase,
    build_eval_cases,
    load_jira_text,
    find_hashes,
)
from commit_investigator.narrowing.scoring import (
    compute_file_overlap,
    compute_pre_scores,
    get_signal_count,
    DEFAULT_WEIGHTS,
)
from commit_investigator.retrieval import prepare_investigation, compute_recall_at_k

CASES_TO_ANALYZE = ["HBASE-4577", "GROOVY-5775", "IGNITE-1787"]
RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
REPOS_DIR = Path(__file__).resolve().parents[1] / "data" / "repos"


def main():
    print("Loading ground truth...")
    gt = GroundTruthGraph.from_replication_zip(str(ZIP_PATH))

    eval_cases = build_eval_cases(gt)
    case_map = {c.issue_key: c for c in eval_cases}

    results = []

    for issue_key in CASES_TO_ANALYZE:
        print(f"\n{'='*60}")
        print(f"Case: {issue_key}")
        print(f"{'='*60}")

        case = case_map.get(issue_key)
        if not case:
            print(f"  ERROR: {issue_key} not found in eval cases")
            results.append({
                "case": issue_key,
                "error": "not in eval cases",
            })
            continue

        print(f"  Fix hash: {case.fix_hash}")
        print(f"  Bug hashes: {case.bug_hashes}")
        print(f"  Temporal bound: {case.temporal_bound}")
        print(f"  Repo: {case.repo_path}")

        jira = load_jira_text(issue_key)
        if not jira:
            print(f"  ERROR: JIRA cache missing for {issue_key}")
            results.append({"case": issue_key, "error": "jira cache missing"})
            continue

        print(f"  Running retrieval...")
        try:
            retrieval = prepare_investigation(
                source=(jira["title"], jira["description"]),
                repo_path=case.repo_path,
                temporal_bound=case.temporal_bound,
                project=case.project,
                issue_key=issue_key,
            )
        except Exception as e:
            print(f"  ERROR: Retrieval failed: {e}")
            results.append({"case": issue_key, "error": f"retrieval: {e}"})
            continue

        candidate_set = retrieval.candidate_set
        problem = retrieval.problem_statement
        n_candidates = len(candidate_set.commits)
        print(f"  Candidates: {n_candidates}")
        print(f"  Extracted files: {problem.extracted_files}")

        best_gt_hash = ""
        best_gt_rank = float("inf")
        for bh in case.bug_hashes:
            diag = compute_recall_at_k(candidate_set, bh, k=100)
            if diag.found and diag.rank is not None and diag.rank < best_gt_rank:
                best_gt_hash, best_gt_rank = bh, diag.rank

        if not best_gt_hash:
            print(f"  GT NOT in candidate set (retrieval miss)")
            results.append({
                "case": issue_key,
                "gt_hash": case.bug_hashes[0] if case.bug_hashes else "",
                "in_candidate_set": False,
                "error": "not in retrieval",
            })
            continue

        print(f"  GT hash: {best_gt_hash}")
        print(f"  GT retrieval rank: {best_gt_rank}")

        # Compute pre-scores for ALL candidates (not just top 15)
        shortlist_full = compute_pre_scores(
            candidate_set, problem, shortlist_size=n_candidates
        )

        # Find GT in the scored list
        gt_scored = None
        gt_prescore_rank = None
        for idx, sc in enumerate(shortlist_full.candidates, 1):
            if sc.commit_id.lower() == best_gt_hash.lower():
                gt_scored = sc
                gt_prescore_rank = idx
                break

        if gt_scored is None:
            print(f"  ERROR: GT hash not found in scored output")
            results.append({
                "case": issue_key,
                "gt_hash": best_gt_hash,
                "in_candidate_set": True,
                "retrieval_rank": int(best_gt_rank),
                "error": "not in scored output",
            })
            continue

        # Determine verdict
        if gt_prescore_rank <= 15:
            verdict = "Already in top 15 (no issue)"
        elif gt_prescore_rank <= 20:
            verdict = "Expand shortlist to 20"
        elif gt_prescore_rank <= 25:
            verdict = "Expand shortlist to 25"
        else:
            verdict = "Formula change needed"

        print(f"\n  --- Pre-Score Diagnosis ---")
        print(f"  GT pre-score rank: {gt_prescore_rank} / {n_candidates}")
        print(f"  file_overlap: {gt_scored.file_overlap}")
        print(f"  signal_count: {gt_scored.signal_count}")
        print(f"  pre_score:    {gt_scored.pre_score}")
        print(f"  original_rank (retrieval): {gt_scored.original_rank}")
        print(f"  Verdict: {verdict}")

        # Show top-5 and neighbors for context
        print(f"\n  Top-5 pre-scored candidates:")
        for i, sc in enumerate(shortlist_full.candidates[:5], 1):
            marker = " <<<GT" if sc.commit_id.lower() == best_gt_hash.lower() else ""
            print(f"    #{i:2d}  score={sc.pre_score:.4f}  fo={sc.file_overlap:.3f}  "
                  f"sig={sc.signal_count}  orig_rank={sc.original_rank}{marker}")

        # Show around the GT rank
        gt_idx = gt_prescore_rank - 1
        start = max(0, gt_idx - 2)
        end = min(n_candidates, gt_idx + 3)
        print(f"\n  Neighbors around GT (rank {gt_prescore_rank}):")
        for i in range(start, end):
            sc = shortlist_full.candidates[i]
            marker = " <<<GT" if sc.commit_id.lower() == best_gt_hash.lower() else ""
            print(f"    #{i+1:2d}  score={sc.pre_score:.4f}  fo={sc.file_overlap:.3f}  "
                  f"sig={sc.signal_count}  orig_rank={sc.original_rank}  "
                  f"sha={sc.commit_id[:12]}{marker}")

        results.append({
            "case": issue_key,
            "gt_hash": best_gt_hash,
            "in_candidate_set": True,
            "retrieval_rank": int(best_gt_rank),
            "gt_prescore_rank": gt_prescore_rank,
            "total_candidates": n_candidates,
            "file_overlap": gt_scored.file_overlap,
            "signal_count": gt_scored.signal_count,
            "pre_score": gt_scored.pre_score,
            "verdict": verdict,
        })

    # Print summary table
    print(f"\n\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"{'Case':<16} {'GT Hash':<14} {'Rank':>6} {'f_overlap':>10} "
          f"{'signals':>8} {'pre_score':>10} {'Verdict'}")
    print("-" * 80)
    for r in results:
        if "error" in r:
            print(f"{r['case']:<16} ERROR: {r['error']}")
        else:
            print(f"{r['case']:<16} {r['gt_hash'][:12]:<14} "
                  f"{r['gt_prescore_rank']:>6} "
                  f"{r['file_overlap']:>10.4f} "
                  f"{r['signal_count']:>8} "
                  f"{r['pre_score']:>10.6f} "
                  f"{r['verdict']}")

    # Write JSON for further processing
    out_path = RESULTS_DIR / "2026-06-18-diag-prescore-rank.json"
    out_path.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nJSON output: {out_path}")

    return results


if __name__ == "__main__":
    main()
