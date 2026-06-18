#!/usr/bin/env python3
"""V4.2 Checkpoint: Phase 1 funnel metrics (Recall@15 + TriageRecall@7).

Runs the production narrowing module (narrow_candidates) on all 20 seed=42
eval cases, measuring the 5-stage funnel metrics:
  - Recall@100: GT in retrieval output
  - Recall@15: GT in pre-score shortlist (top 15)
  - TriageRecall@7: GT in must_examine ∪ watchlist (top 7)

Gate: recall_15 >= 0.846, triage_recall_7 = 1.00 (on retrievable cases)

Usage:
    python scripts/run_phase1_checkpoint.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commit_investigator.eval.ground_truth import GroundTruthGraph
from commit_investigator.eval.helpers import (
    ZIP_PATH,
    EvalCase,
    build_eval_cases,
    create_run_folder,
    gt_in_set,
    load_jira_text,
    update_latest_symlink,
    write_run_config,
)
from commit_investigator.narrowing import narrow_candidates
from commit_investigator.narrowing.scoring import compute_pre_scores
from commit_investigator.retrieval import compute_recall_at_k, prepare_investigation

GATE_RECALL_15 = 0.846
GATE_TRIAGE_RECALL_7 = 1.00


def _find_best_recall_at_100(case: EvalCase, candidate_set: Any) -> Any | None:
    """Find the best-ranking ground truth hit in the retrieval candidate set."""
    best = None
    for bh in case.bug_hashes:
        diag = compute_recall_at_k(candidate_set, bh, k=100)
        if diag.found and (best is None or diag.rank < best.rank):
            best = diag
    return best


def _measure_narrowing(case: EvalCase, retrieval: Any) -> dict[str, Any]:
    """Run Phase 1 narrowing and measure Recall@15 + TriageRecall@7."""
    triage_result = narrow_candidates(
        retrieval.candidate_set,
        retrieval.problem_statement,
    )

    shortlist = compute_pre_scores(retrieval.candidate_set, retrieval.problem_statement)
    shortlist_shas = [c.commit_id.lower() for c in shortlist.candidates]

    recall_15 = gt_in_set(case.bug_hashes, shortlist_shas)

    gt_rank_in_shortlist = None
    targets_lower = {bh.lower() for bh in case.bug_hashes}
    for idx, sha in enumerate(shortlist_shas, start=1):
        if sha in targets_lower:
            gt_rank_in_shortlist = idx
            break

    all_triaged_shas = triage_result.must_examine_shas + triage_result.watchlist_shas
    triage_recall_7 = gt_in_set(case.bug_hashes, all_triaged_shas)
    in_must = gt_in_set(case.bug_hashes, triage_result.must_examine_shas)
    in_watch = gt_in_set(case.bug_hashes, triage_result.watchlist_shas)

    return {
        "recall_15": recall_15,
        "triage_recall_7": triage_recall_7,
        "gt_rank_in_shortlist": gt_rank_in_shortlist,
        "triage_tier": "must_examine" if in_must else ("watchlist" if in_watch else "outside"),
        "shortlist_size": shortlist.size,
        "must_examine_shas": triage_result.must_examine_shas,
        "watchlist_shas": triage_result.watchlist_shas,
    }


def _build_retrievable_result(
    case: EvalCase, best_r100: Any, retrieval: Any, narrowing: dict[str, Any]
) -> dict[str, Any]:
    """Build result dict for a retrievable case that passed recall@100."""
    marker = "HIT@7" if narrowing["triage_recall_7"] else (
        "HIT@15" if narrowing["recall_15"] else "MISS")
    print(
        f"{marker:6s}  R@100 rank={best_r100.rank:>3d}  "
        f"R@15 rank={narrowing['gt_rank_in_shortlist'] or '---':>3}  "
        f"tier={narrowing['triage_tier']}"
    )
    return {
        "issue_key": case.issue_key, "project": case.project,
        "status": "retrievable", "recall_100": True,
        "recall_15": narrowing["recall_15"],
        "triage_recall_7": narrowing["triage_recall_7"],
        "r100_rank": best_r100.rank,
        "r15_rank": narrowing["gt_rank_in_shortlist"],
        "triage_tier": narrowing["triage_tier"],
        "total_candidates": len(retrieval.candidate_set.commits),
        "shortlist_size": narrowing["shortlist_size"],
        "must_examine_shas": narrowing["must_examine_shas"],
        "watchlist_shas": narrowing["watchlist_shas"],
    }


def run_single_phase1_case(case: EvalCase) -> dict[str, Any]:
    """Process one eval case through retrieval + narrowing. Returns result dict."""
    jira = load_jira_text(case.issue_key)
    if jira is None:
        print("SKIP (JIRA cache missing)")
        return {"issue_key": case.issue_key, "project": case.project,
                "status": "skipped", "reason": "jira_cache_missing"}

    if not (case.repo_path / ".git").exists():
        print(f"SKIP (repo not found)")
        return {"issue_key": case.issue_key, "project": case.project,
                "status": "skipped", "reason": "repo_not_found"}

    try:
        retrieval = prepare_investigation(
            source=(jira["title"], jira["description"]),
            repo_path=case.repo_path,
            temporal_bound=case.temporal_bound,
            project=case.project,
            issue_key=case.issue_key,
        )
    except Exception as e:
        print(f"ERR ({e})")
        return {"issue_key": case.issue_key, "project": case.project,
                "status": "error", "reason": str(e)}

    best_r100 = _find_best_recall_at_100(case, retrieval.candidate_set)
    if best_r100 is None or not best_r100.found:
        print("---  (not retrievable)")
        return {
            "issue_key": case.issue_key, "project": case.project,
            "status": "not_retrievable",
            "recall_100": False, "recall_15": False, "triage_recall_7": False,
            "r100_rank": None, "r15_rank": None,
            "total_candidates": len(retrieval.candidate_set.commits),
        }

    narrowing = _measure_narrowing(case, retrieval)
    return _build_retrievable_result(case, best_r100, retrieval, narrowing)


def compute_phase1_aggregates(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate metrics from individual case results."""
    retrievable = [c for c in case_results if c.get("status") == "retrievable"]
    n_retrievable = len(retrievable)
    n_total = len(case_results)
    n_recall_15 = sum(1 for c in retrievable if c["recall_15"])
    n_triage_7 = sum(1 for c in retrievable if c["triage_recall_7"])

    recall_15_rate = n_recall_15 / n_retrievable if n_retrievable > 0 else 0.0
    triage_recall_7_rate = n_triage_7 / n_recall_15 if n_recall_15 > 0 else 0.0
    recall_100_rate = n_retrievable / n_total if n_total > 0 else 0.0

    n_must = sum(1 for c in retrievable if c.get("triage_tier") == "must_examine")
    n_watch = sum(1 for c in retrievable if c.get("triage_tier") == "watchlist")

    gate_r15 = recall_15_rate >= GATE_RECALL_15
    gate_t7 = triage_recall_7_rate >= GATE_TRIAGE_RECALL_7

    return {
        "n_total": n_total, "n_retrievable": n_retrievable,
        "n_recall_15": n_recall_15, "n_triage_7": n_triage_7,
        "recall_100_rate": recall_100_rate,
        "recall_15_rate": recall_15_rate,
        "triage_recall_7_rate": triage_recall_7_rate,
        "n_must": n_must, "n_watch": n_watch,
        "gate_r15": gate_r15, "gate_t7": gate_t7,
        "gate_passed": gate_r15 and gate_t7,
    }


def print_phase1_summary(agg: dict[str, Any]) -> None:
    """Print formatted Phase 1 results table to stdout."""
    print(f"\n{'='*72}")
    print(f"PHASE 1 FUNNEL RESULTS  (n_total={agg['n_total']}, n_retrievable={agg['n_retrievable']})")
    print(f"{'='*72}")
    print(f"  Recall@100:      {agg['n_retrievable']}/{agg['n_total']} = {agg['recall_100_rate']:.3f}")
    print(f"  Recall@15:       {agg['n_recall_15']}/{agg['n_retrievable']} = {agg['recall_15_rate']:.3f}  "
          f"[gate={GATE_RECALL_15:.3f}]  {'PASS' if agg['gate_r15'] else 'FAIL'}")
    print(f"  TriageRecall@7:  {agg['n_triage_7']}/{agg['n_recall_15']} = {agg['triage_recall_7_rate']:.3f}  "
          f"(conditioned on Recall@15=true)  "
          f"[gate={GATE_TRIAGE_RECALL_7:.3f}]  {'PASS' if agg['gate_t7'] else 'FAIL'}")
    print(f"\n  Tier breakdown (of {agg['n_recall_15']} cases surviving to triage):")
    print(f"    must_examine: {agg['n_must']}")
    print(f"    watchlist:    {agg['n_watch']}")


def build_phase1_output(
    agg: dict[str, Any], case_results: list[dict[str, Any]], elapsed: float
) -> dict[str, Any]:
    """Build the summary dict for JSON serialization."""
    return {
        "checkpoint": "phase1-funnel",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_total": agg["n_total"],
        "n_retrievable": agg["n_retrievable"],
        "n_recall_15": agg["n_recall_15"],
        "metrics": {
            "recall_100": round(agg["recall_100_rate"], 4),
            "recall_15": round(agg["recall_15_rate"], 4),
            "triage_recall_7": round(agg["triage_recall_7_rate"], 4),
            "triage_recall_7_note": f"Conditioned on Recall@15=true ({agg['n_triage_7']}/{agg['n_recall_15']})",
        },
        "tier_breakdown": {
            "must_examine": agg["n_must"],
            "watchlist": agg["n_watch"],
            "n_shortlist_survivors": agg["n_recall_15"],
        },
        "gates": {
            "recall_15": {"threshold": GATE_RECALL_15, "value": round(agg["recall_15_rate"], 4), "passed": agg["gate_r15"]},
            "triage_recall_7": {"threshold": GATE_TRIAGE_RECALL_7, "value": round(agg["triage_recall_7_rate"], 4), "passed": agg["gate_t7"]},
        },
        "gate_passed": agg["gate_passed"],
        "elapsed_s": round(elapsed, 1),
        "cases": case_results,
    }


def main() -> None:
    print("=" * 72)
    print("V4.2 CHECKPOINT: Phase 1 Funnel Metrics")
    print("=" * 72)

    print("\nLoading ground truth graph...")
    gt = GroundTruthGraph.from_replication_zip(str(ZIP_PATH))

    print("Building eval cases from retrieval-recall.json...")
    cases = build_eval_cases(gt)
    print(f"  Resolved {len(cases)} cases\n")

    start_time = time.time()
    case_results: list[dict] = []

    for i, case in enumerate(cases, start=1):
        print(f"[{i:2d}/{len(cases)}] {case.issue_key:16s}", end="  ", flush=True)
        case_results.append(run_single_phase1_case(case))

    elapsed = time.time() - start_time

    agg = compute_phase1_aggregates(case_results)
    print_phase1_summary(agg)
    print(f"\n  Elapsed: {elapsed:.1f}s")
    print(f"  Overall gate: {'PASS' if agg['gate_passed'] else 'FAIL'}")
    print(f"{'='*72}")

    run_dir = create_run_folder("phase1-checkpoint", n=agg["n_total"])
    write_run_config(run_dir, label="phase1-checkpoint", pipeline="v4.2-phase1", n=agg["n_total"])

    output = build_phase1_output(agg, case_results, elapsed)
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    update_latest_symlink(run_dir)
    print(f"\nResults written to {run_dir}")

    sys.exit(0 if agg["gate_passed"] else 1)


if __name__ == "__main__":
    main()
