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
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commit_investigator.eval.ground_truth import GroundTruthGraph
from commit_investigator.narrowing import narrow_candidates
from commit_investigator.retrieval import compute_recall_at_k, prepare_investigation

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JIRA_CACHE_DIR = PROJECT_ROOT / "data" / "jira_cache"
REPOS_DIR = PROJECT_ROOT / "data" / "repos"
ZIP_PATH = PROJECT_ROOT / "data" / "apachejit" / "apachejit_dataset_replication.zip"
RECALL100_PATH = PROJECT_ROOT / "results" / "v4-checkpoints" / "retrieval-recall.json"
RESULTS_PATH = PROJECT_ROOT / "results" / "v4-checkpoints" / "phase1-funnel.json"

GATE_RECALL_15 = 0.846
GATE_TRIAGE_RECALL_7 = 1.00


@dataclass
class EvalCase:
    issue_key: str
    project: str
    bug_hashes: list[str]
    fix_hash: str
    repo_path: Path
    temporal_bound: str


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
    """Build eval cases from retrieval-recall.json + ground truth graph."""
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
    """Find (fix_hash, all_bug_hashes) for an issue key."""
    commits = gt._issue_to_commits.get(issue_key, [])
    for commit_id in commits:
        if gt.has_fix(commit_id):
            bug_hashes = gt.get_bug_commits(commit_id)
            if bug_hashes:
                return commit_id, bug_hashes
    return "", []


def gt_in_set(bug_hashes: list[str], sha_set: list[str]) -> bool:
    """Check if any bug hash appears in a SHA list (case-insensitive)."""
    targets = {bh.lower() for bh in bug_hashes}
    return bool(targets & {s.lower() for s in sha_set})


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

        jira = load_jira_text(case.issue_key)
        if jira is None:
            print("SKIP (JIRA cache missing)")
            case_results.append({
                "issue_key": case.issue_key, "project": case.project,
                "status": "skipped", "reason": "jira_cache_missing",
            })
            continue

        if not (case.repo_path / ".git").exists():
            print(f"SKIP (repo not found)")
            case_results.append({
                "issue_key": case.issue_key, "project": case.project,
                "status": "skipped", "reason": "repo_not_found",
            })
            continue

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
            case_results.append({
                "issue_key": case.issue_key, "project": case.project,
                "status": "error", "reason": str(e),
            })
            continue

        # Recall@100: is GT in retrieval output?
        best_r100 = None
        for bh in case.bug_hashes:
            diag = compute_recall_at_k(retrieval.candidate_set, bh, k=100)
            if diag.found and (best_r100 is None or diag.rank < best_r100.rank):
                best_r100 = diag

        recall_100 = best_r100 is not None and best_r100.found

        if not recall_100:
            print("---  (not retrievable)")
            case_results.append({
                "issue_key": case.issue_key, "project": case.project,
                "status": "not_retrievable",
                "recall_100": False, "recall_15": False, "triage_recall_7": False,
                "r100_rank": None, "r15_rank": None,
                "total_candidates": len(retrieval.candidate_set.commits),
            })
            continue

        # Phase 1: narrow_candidates (production module)
        triage_result = narrow_candidates(
            retrieval.candidate_set,
            retrieval.problem_statement,
        )

        # Recall@15: GT in shortlist (all triaged candidates come from top-15)
        all_triaged_shas = triage_result.must_examine_shas + triage_result.watchlist_shas
        # The shortlist is the full pre-score top-15; triage picks 7 from it.
        # To measure Recall@15 we need to check if GT was anywhere in the top-15
        # of the pre-score. We can re-derive this from the scoring step.
        from commit_investigator.narrowing.scoring import compute_pre_scores as _score
        shortlist = _score(retrieval.candidate_set, retrieval.problem_statement)
        shortlist_shas = [c.commit_id.lower() for c in shortlist.candidates]

        recall_15 = gt_in_set(case.bug_hashes, shortlist_shas)

        # Find GT rank in shortlist
        gt_rank_in_shortlist = None
        targets_lower = {bh.lower() for bh in case.bug_hashes}
        for idx, sha in enumerate(shortlist_shas, start=1):
            if sha in targets_lower:
                gt_rank_in_shortlist = idx
                break

        # TriageRecall@7: GT in must_examine ∪ watchlist
        triage_recall_7 = gt_in_set(case.bug_hashes, all_triaged_shas)

        # Where specifically?
        in_must = gt_in_set(case.bug_hashes, triage_result.must_examine_shas)
        in_watch = gt_in_set(case.bug_hashes, triage_result.watchlist_shas)

        marker = "HIT@7" if triage_recall_7 else ("HIT@15" if recall_15 else "MISS")
        tier = "must_examine" if in_must else ("watchlist" if in_watch else "outside")
        print(
            f"{marker:6s}  R@100 rank={best_r100.rank:>3d}  "
            f"R@15 rank={gt_rank_in_shortlist or '---':>3}  "
            f"tier={tier}"
        )

        case_results.append({
            "issue_key": case.issue_key,
            "project": case.project,
            "status": "retrievable",
            "recall_100": True,
            "recall_15": recall_15,
            "triage_recall_7": triage_recall_7,
            "r100_rank": best_r100.rank,
            "r15_rank": gt_rank_in_shortlist,
            "triage_tier": tier,
            "total_candidates": len(retrieval.candidate_set.commits),
            "shortlist_size": shortlist.size,
            "must_examine_shas": triage_result.must_examine_shas,
            "watchlist_shas": triage_result.watchlist_shas,
        })

    elapsed = time.time() - start_time

    # Aggregate metrics
    retrievable = [c for c in case_results if c.get("status") == "retrievable"]
    n_retrievable = len(retrievable)
    n_total = len(case_results)
    n_recall_15 = sum(1 for c in retrievable if c["recall_15"])
    n_triage_7 = sum(1 for c in retrievable if c["triage_recall_7"])

    recall_15_rate = n_recall_15 / n_retrievable if n_retrievable > 0 else 0.0
    triage_recall_7_rate = n_triage_7 / n_recall_15 if n_recall_15 > 0 else 0.0

    # Triage tier breakdown
    n_must = sum(1 for c in retrievable if c.get("triage_tier") == "must_examine")
    n_watch = sum(1 for c in retrievable if c.get("triage_tier") == "watchlist")

    gate_r15 = recall_15_rate >= GATE_RECALL_15
    gate_t7 = triage_recall_7_rate >= GATE_TRIAGE_RECALL_7
    gate_passed = gate_r15 and gate_t7

    print(f"\n{'='*72}")
    print(f"PHASE 1 FUNNEL RESULTS  (n_total={n_total}, n_retrievable={n_retrievable})")
    print(f"{'='*72}")
    print(f"  Recall@100:      {n_retrievable}/{n_total} = {n_retrievable/n_total:.3f}")
    print(f"  Recall@15:       {n_recall_15}/{n_retrievable} = {recall_15_rate:.3f}  "
          f"[gate={GATE_RECALL_15:.3f}]  {'PASS' if gate_r15 else 'FAIL'}")
    print(f"  TriageRecall@7:  {n_triage_7}/{n_recall_15} = {triage_recall_7_rate:.3f}  "
          f"(conditioned on Recall@15=true)  "
          f"[gate={GATE_TRIAGE_RECALL_7:.3f}]  {'PASS' if gate_t7 else 'FAIL'}")
    print(f"\n  Tier breakdown (of {n_recall_15} cases surviving to triage):")
    print(f"    must_examine: {n_must}")
    print(f"    watchlist:    {n_watch}")
    print(f"\n  Elapsed: {elapsed:.1f}s")
    print(f"  Overall gate: {'PASS' if gate_passed else 'FAIL'}")
    print(f"{'='*72}")

    output = {
        "checkpoint": "phase1-funnel",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_total": n_total,
        "n_retrievable": n_retrievable,
        "n_recall_15": n_recall_15,
        "metrics": {
            "recall_100": round(n_retrievable / n_total, 4) if n_total > 0 else 0,
            "recall_15": round(recall_15_rate, 4),
            "triage_recall_7": round(triage_recall_7_rate, 4),
            "triage_recall_7_note": f"Conditioned on Recall@15=true ({n_triage_7}/{n_recall_15})",
        },
        "tier_breakdown": {
            "must_examine": n_must,
            "watchlist": n_watch,
            "n_shortlist_survivors": n_recall_15,
        },
        "gates": {
            "recall_15": {"threshold": GATE_RECALL_15, "value": round(recall_15_rate, 4), "passed": gate_r15},
            "triage_recall_7": {"threshold": GATE_TRIAGE_RECALL_7, "value": round(triage_recall_7_rate, 4), "passed": gate_t7},
        },
        "gate_passed": gate_passed,
        "elapsed_s": round(elapsed, 1),
        "cases": case_results,
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"\nResults written to {RESULTS_PATH}")

    sys.exit(0 if gate_passed else 1)


if __name__ == "__main__":
    main()
