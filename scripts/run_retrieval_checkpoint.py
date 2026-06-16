#!/usr/bin/env python3
"""P6 Checkpoint: Retrieval Recall@100 on n=20 eval cases.

Runs production retrieval (prepare_investigation) on all 20 seed=42 eval cases.
Measures Retrieval Recall@100 per case and aggregate.
Compares to spike baseline (0.45). Reports per-strategy contribution.
Writes results to results/v4-checkpoints/retrieval-recall.json.

Usage:
    python scripts/run_retrieval_checkpoint.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commit_investigator.retrieval import (
    RetrievalConfig,
    compute_recall_at_k,
    prepare_investigation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "results" / "v3-subagent-eval-v2" / "manifest.json"
JIRA_CACHE_DIR = PROJECT_ROOT / "data" / "jira_cache"
RESULTS_PATH = PROJECT_ROOT / "results" / "v4-checkpoints" / "retrieval-recall.json"

GATE = 0.35
TARGET = 0.45


def load_jira_text(issue_key: str) -> dict | None:
    path = JIRA_CACHE_DIR / f"{issue_key}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "title": raw["fields"]["summary"],
        "description": raw["fields"].get("description") or "",
    }


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    cases = manifest["cases"]

    results_per_case = []
    hits = 0
    skipped = 0
    strategy_contributions: dict[str, int] = {}
    start_time = time.time()

    for case in cases:
        issue_key = case["issue_key"]
        project = case["project"]
        repo_path = Path(case["repo_path"])
        temporal_bound = case["temporal_bound"]
        bug_hash = case["bug_hash"]

        if not (repo_path / ".git").exists():
            print(f"  SKIP {issue_key}: repo not cloned at {repo_path}")
            skipped += 1
            results_per_case.append({
                "issue_key": issue_key,
                "status": "skipped",
                "reason": "repo not cloned",
            })
            continue

        jira = load_jira_text(issue_key)
        if jira is None:
            print(f"  SKIP {issue_key}: JIRA cache missing")
            skipped += 1
            results_per_case.append({
                "issue_key": issue_key,
                "status": "skipped",
                "reason": "jira cache missing",
            })
            continue

        try:
            result = prepare_investigation(
                source=(jira["title"], jira["description"]),
                repo_path=repo_path,
                temporal_bound=temporal_bound,
                project=project,
                issue_key=issue_key,
            )

            diagnostic = compute_recall_at_k(result.candidate_set, bug_hash, k=100)

            case_result = {
                "issue_key": issue_key,
                "project": project,
                "status": "found" if diagnostic.found else "not_found",
                "rank": diagnostic.rank,
                "total_candidates": diagnostic.total_candidates,
                "strategies_that_found": diagnostic.strategies_that_found,
                "retry_triggered": result.metadata["retry_triggered"],
                "fallback_triggered": result.metadata["fallback_triggered"],
            }
            results_per_case.append(case_result)

            if diagnostic.found:
                hits += 1
                for strat in diagnostic.strategies_that_found:
                    strategy_contributions[strat] = strategy_contributions.get(strat, 0) + 1
                print(f"  HIT  {issue_key}: rank {diagnostic.rank} via {diagnostic.strategies_that_found}")
            else:
                print(f"  MISS {issue_key}: not in top {diagnostic.total_candidates}")

        except Exception as e:
            print(f"  ERR  {issue_key}: {e}")
            results_per_case.append({
                "issue_key": issue_key,
                "status": "error",
                "error": str(e),
            })

    elapsed = time.time() - start_time
    tested = len(cases) - skipped
    recall = hits / tested if tested > 0 else 0.0

    print(f"\n{'='*60}")
    print(f"Retrieval Recall@100: {recall:.3f} ({hits}/{tested})")
    print(f"Skipped: {skipped}")
    print(f"Time: {elapsed:.1f}s")
    print(f"Gate: {GATE} — {'PASS' if recall >= GATE else 'FAIL'}")
    print(f"Target: {TARGET} — {'PASS' if recall >= TARGET else 'MISS'}")
    print(f"Strategy contributions: {strategy_contributions}")
    print(f"{'='*60}")

    output = {
        "checkpoint": "retrieval-recall-at-100",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_total": len(cases),
        "n_tested": tested,
        "n_skipped": skipped,
        "n_hits": hits,
        "recall_at_100": round(recall, 4),
        "gate": GATE,
        "gate_passed": recall >= GATE,
        "target": TARGET,
        "target_passed": recall >= TARGET,
        "elapsed_seconds": round(elapsed, 1),
        "strategy_contributions": strategy_contributions,
        "spike_baseline": 0.45,
        "cases": results_per_case,
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"\nResults written to {RESULTS_PATH}")

    if recall < GATE:
        sys.exit(1)


if __name__ == "__main__":
    main()
