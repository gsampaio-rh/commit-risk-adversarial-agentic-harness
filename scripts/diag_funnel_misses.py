#!/usr/bin/env python3
"""P28 Diagnostic: exact pre-score ranks and funnel analysis for 7 remaining misses.

For each of the 7 cases that fail Hit@5 in the P26 eval, extracts:
  - GT commit hash and which retrieval strategies found it
  - Exact pre-score rank (out of 100 candidates)
  - Pre-score value, file_overlap, signal_count, retrieval_signal
  - Triage position (if applicable)
  - Whether GT was examined during investigation (from trace files)

Confirms which failures are addressable by pre-score tuning vs investigation.

Usage:
    python scripts/diag_funnel_misses.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commit_investigator.eval.ground_truth import GroundTruthGraph
from commit_investigator.eval.helpers import (
    ZIP_PATH,
    EvalCase,
    build_eval_cases,
    load_jira_text,
)
from commit_investigator.narrowing.scoring import compute_pre_scores
from commit_investigator.retrieval import prepare_investigation

TARGET_ISSUES = [
    "SPARK-27907",
    "GROOVY-7014",
    "GROOVY-8298",
    "GROOVY-5775",
    "IGNITE-6748",
    "HIVE-4113",
    "SPARK-23059",
]

TRACE_DIR = (
    Path(__file__).resolve().parents[1]
    / "results"
    / "2026-06-18T23-59-scaled-eval-gpt-5.3-codex-gpt-5.3-codex-n20"
    / "traces"
)


@dataclass
class FunnelDiag:
    """Diagnostic data for one miss case."""

    issue_key: str
    gt_hash: str
    retrieval_rank: int | None
    retrieval_signal: str
    pre_score_rank: int | None
    pre_score: float | None
    file_overlap: float | None
    signal_count: int | None
    triage_position: str
    exam_recall: bool | None
    hit_at_5: bool | None
    failure_stage: str
    gt_suspect_rank: int | None


def _load_trace(issue_key: str) -> dict[str, Any] | None:
    path = TRACE_DIR / f"{issue_key}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _find_gt_in_candidates(candidate_set, bug_hashes: list[str]) -> tuple[str | None, int | None]:
    """Find best-ranking GT hash in the candidate set."""
    targets = {bh.lower() for bh in bug_hashes}
    for c in candidate_set.commits:
        if c.commit_id.lower() in targets:
            return c.commit_id, c.rank
    return None, None


def _find_gt_in_scored(shortlist, bug_hashes: list[str]) -> tuple[int | None, float | None, float | None, int | None, str]:
    """Find GT in scored shortlist. Returns (rank, score, file_overlap, signal_count, signal)."""
    targets = {bh.lower() for bh in bug_hashes}
    for idx, sc in enumerate(shortlist.candidates, start=1):
        if sc.commit_id.lower() in targets:
            return idx, sc.pre_score, sc.file_overlap, sc.signal_count, sc.retrieval_signal
    return None, None, None, None, ""


def _find_gt_suspect_rank(trace: dict[str, Any], bug_hashes: list[str]) -> int | None:
    """Find GT rank in suspect list from the trace."""
    targets = {bh.lower() for bh in bug_hashes}
    suspects = trace.get("outcome", {}).get("suspects", [])
    for s in suspects:
        if s["commit_id"].lower() in targets:
            return s["rank"]
    return None


def _classify_failure(trace: dict[str, Any]) -> str:
    """Classify which stage the case fails at."""
    if not trace.get("pre_score_recall_15"):
        return "pre_score"
    if not trace.get("triage_recall_7"):
        return "triage"
    if not trace.get("exam_recall"):
        return "investigation (not examined)"
    return "investigation (ranked > 5)"


def diagnose_case(case: EvalCase) -> FunnelDiag | None:
    """Run retrieval + pre-scoring for one case and extract GT diagnostics."""
    jira = load_jira_text(case.issue_key)
    if jira is None:
        print(f"  SKIP: JIRA cache missing for {case.issue_key}")
        return None

    if not (case.repo_path / ".git").exists():
        print(f"  SKIP: repo not found for {case.issue_key}")
        return None

    retrieval = prepare_investigation(
        source=(jira["title"], jira["description"]),
        repo_path=case.repo_path,
        temporal_bound=case.temporal_bound,
        project=case.project,
        issue_key=case.issue_key,
        fix_hash=case.fix_hash,
    )

    gt_hash, retrieval_rank = _find_gt_in_candidates(
        retrieval.candidate_set, case.bug_hashes
    )

    if gt_hash is None:
        print(f"  WARNING: GT not in candidate set for {case.issue_key}")
        return FunnelDiag(
            issue_key=case.issue_key, gt_hash="NOT_FOUND",
            retrieval_rank=None, retrieval_signal="",
            pre_score_rank=None, pre_score=None,
            file_overlap=None, signal_count=None,
            triage_position="N/A", exam_recall=None,
            hit_at_5=None, failure_stage="retrieval",
            gt_suspect_rank=None,
        )

    retrieval_signal = ""
    for c in retrieval.candidate_set.commits:
        if c.commit_id.lower() == gt_hash.lower():
            retrieval_signal = c.retrieval_signal
            break

    shortlist = compute_pre_scores(
        retrieval.candidate_set,
        retrieval.problem_statement,
        shortlist_size=100,
    )

    ps_rank, ps_score, fo, sc, _ = _find_gt_in_scored(shortlist, case.bug_hashes)

    triaged_shortlist = compute_pre_scores(
        retrieval.candidate_set,
        retrieval.problem_statement,
    )
    triaged_shas = [c.commit_id.lower() for c in triaged_shortlist.candidates[:7]]
    targets_lower = {bh.lower() for bh in case.bug_hashes}
    if any(sha in targets_lower for sha in triaged_shas[:3]):
        triage_pos = "must_examine"
    elif any(sha in targets_lower for sha in triaged_shas[3:7]):
        triage_pos = "watchlist"
    elif ps_rank and ps_rank <= 20:
        triage_pos = f"shortlist (rank {ps_rank}, outside top 7)"
    else:
        triage_pos = f"outside shortlist (rank {ps_rank})"

    trace = _load_trace(case.issue_key)
    exam_recall = trace.get("exam_recall") if trace else None
    hit_at_5 = trace.get("outcome", {}).get("hit_at_5") if trace else None
    failure_stage = _classify_failure(trace) if trace else "unknown"
    gt_suspect_rank = _find_gt_suspect_rank(trace, case.bug_hashes) if trace else None

    return FunnelDiag(
        issue_key=case.issue_key,
        gt_hash=gt_hash[:12],
        retrieval_rank=retrieval_rank,
        retrieval_signal=retrieval_signal,
        pre_score_rank=ps_rank,
        pre_score=ps_score,
        file_overlap=fo,
        signal_count=sc,
        triage_position=triage_pos,
        exam_recall=exam_recall,
        hit_at_5=hit_at_5,
        failure_stage=failure_stage,
        gt_suspect_rank=gt_suspect_rank,
    )


def print_diagnostic_table(results: list[FunnelDiag]) -> None:
    """Print formatted diagnostic table."""
    print(f"\n{'='*100}")
    print("P28 DIAGNOSTIC: 7 Remaining Misses — Funnel Breakdown")
    print(f"{'='*100}\n")

    print(f"{'Issue':<14} {'Stage':<28} {'GT Hash':<14} {'R@100':<6} {'PS Rank':<8} "
          f"{'Score':<7} {'FO':<6} {'SC':<4} {'Signals'}")
    print(f"{'-'*14} {'-'*28} {'-'*14} {'-'*6} {'-'*8} {'-'*7} {'-'*6} {'-'*4} {'-'*30}")

    for r in results:
        ps_rank_str = str(r.pre_score_rank) if r.pre_score_rank else "---"
        score_str = f"{r.pre_score:.3f}" if r.pre_score is not None else "---"
        fo_str = f"{r.file_overlap:.2f}" if r.file_overlap is not None else "---"
        sc_str = str(r.signal_count) if r.signal_count is not None else "---"
        r100_str = str(r.retrieval_rank) if r.retrieval_rank else "---"

        print(f"{r.issue_key:<14} {r.failure_stage:<28} {r.gt_hash:<14} "
              f"{r100_str:<6} {ps_rank_str:<8} {score_str:<7} {fo_str:<6} {sc_str:<4} "
              f"{r.retrieval_signal}")

    print(f"\n{'='*100}")
    print("DETAIL: Triage + Investigation")
    print(f"{'='*100}\n")

    print(f"{'Issue':<14} {'Triage Position':<36} {'Exam?':<7} {'Suspect Rank':<14}")
    print(f"{'-'*14} {'-'*36} {'-'*7} {'-'*14}")

    for r in results:
        exam_str = str(r.exam_recall) if r.exam_recall is not None else "N/A"
        rank_str = str(r.gt_suspect_rank) if r.gt_suspect_rank else "not in top 5"
        print(f"{r.issue_key:<14} {r.triage_position:<36} {exam_str:<7} {rank_str:<14}")

    print(f"\n{'='*100}")
    print("SUMMARY by failure stage:")
    stages = {}
    for r in results:
        stages.setdefault(r.failure_stage, []).append(r.issue_key)
    for stage, cases in sorted(stages.items()):
        print(f"  {stage}: {', '.join(cases)}")

    blame_cases = [r for r in results if "localization_blame" in (r.retrieval_signal or "")]
    if blame_cases:
        print(f"\n  Blame-sourced (localization_blame in signal): "
              f"{', '.join(r.issue_key for r in blame_cases)}")
        print(f"  → These are candidates for blame_bonus in pre-score (P29)")

    print(f"{'='*100}\n")


def main() -> None:
    print("P28 Diagnostic: Funnel analysis for 7 remaining misses")
    print("=" * 60)

    print("\nLoading ground truth graph...")
    gt = GroundTruthGraph.from_replication_zip(str(ZIP_PATH))

    print("Building eval cases...")
    all_cases = build_eval_cases(gt)
    target_cases = [c for c in all_cases if c.issue_key in TARGET_ISSUES]
    print(f"  Found {len(target_cases)}/{len(TARGET_ISSUES)} target cases\n")

    results: list[FunnelDiag] = []
    for i, case in enumerate(target_cases, start=1):
        print(f"[{i}/{len(target_cases)}] {case.issue_key}...", end=" ", flush=True)
        diag = diagnose_case(case)
        if diag:
            results.append(diag)
            print(f"rank={diag.pre_score_rank}, stage={diag.failure_stage}")
        else:
            print("SKIPPED")

    print_diagnostic_table(results)

    output_path = Path(__file__).resolve().parents[1] / "results" / "p28-funnel-diagnostic.json"
    output_data = {
        "diagnostic": "p28-funnel-7-misses",
        "cases": [
            {
                "issue_key": r.issue_key,
                "gt_hash": r.gt_hash,
                "failure_stage": r.failure_stage,
                "retrieval_rank": r.retrieval_rank,
                "pre_score_rank": r.pre_score_rank,
                "pre_score": r.pre_score,
                "file_overlap": r.file_overlap,
                "signal_count": r.signal_count,
                "retrieval_signal": r.retrieval_signal,
                "triage_position": r.triage_position,
                "exam_recall": r.exam_recall,
                "gt_suspect_rank": r.gt_suspect_rank,
            }
            for r in results
        ],
    }
    output_path.write_text(json.dumps(output_data, indent=2) + "\n", encoding="utf-8")
    print(f"Results written to {output_path}")


if __name__ == "__main__":
    main()
