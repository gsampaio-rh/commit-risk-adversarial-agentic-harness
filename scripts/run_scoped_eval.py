#!/usr/bin/env python3
"""V4.2 Scoped Evaluation: full pipeline eval with 5-stage funnel metrics.

Runs the complete V4.2 pipeline on n=20 seed=42 eval cases:
  Retrieval → Phase 1 (pre-score + triage) → Phase 2 (scoped investigation)
  → Phase 2b (conditional watchlist expansion) → Funnel metrics + trace

Reports per case: issue_key, funnel stages, hit_rank, exit_reason, phase2b.
Aggregates: Recall@100, Recall@15, TriageRecall@7, ExamRecall, Hit@5, MRR.

Usage:
    python scripts/run_scoped_eval.py [--n N] [--model MODEL]
"""

from __future__ import annotations

import argparse
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
    load_jira_text,
)
from commit_investigator.eval.metrics import FunnelMetrics, compute_funnel
from commit_investigator.harness.phase2b import run_phase2b
from commit_investigator.harness.result import Suspect
from commit_investigator.harness.scoped_runner import RevisedScopedInvestigator
from commit_investigator.harness.trace_writer import TraceWriter, build_v42_trace
from commit_investigator.infra.git_context import GitContextProvider
from commit_investigator.infra.llm import get_provider
from commit_investigator.narrowing.scoring import compute_pre_scores
from commit_investigator.narrowing.triage import assign_tiers
from commit_investigator.retrieval import compute_recall_at_k, prepare_investigation

RESULTS_PATH = Path(__file__).resolve().parents[1] / "results" / "v4-checkpoints" / "scoped-eval.json"
TRACES_DIR = Path(__file__).resolve().parents[1] / "results" / "traces"


def run_single_case(case: EvalCase, llm: Any) -> dict:
    """Run full V4.2 pipeline on a single eval case."""
    result: dict = {"issue_key": case.issue_key, "project": case.project}

    jira = load_jira_text(case.issue_key)
    if jira is None:
        return {**result, "status": "skipped", "reason": "jira_cache_missing"}
    if not (case.repo_path / ".git").exists():
        return {**result, "status": "skipped", "reason": "repo_not_found"}

    t0 = time.time()
    try:
        retrieval = prepare_investigation(
            source=(jira["title"], jira["description"]),
            repo_path=case.repo_path,
            temporal_bound=case.temporal_bound,
            project=case.project,
            issue_key=case.issue_key,
        )
    except Exception as e:
        return {**result, "status": "error", "reason": str(e)}

    pipeline = _run_pipeline(retrieval, case, llm)
    funnel = _compute_case_funnel(case.bug_hashes, retrieval, pipeline)
    _write_case_trace(case, retrieval, pipeline, funnel)

    result.update(
        status="completed",
        recall_100=funnel.recall_100,
        pre_score_recall_15=funnel.pre_score_recall_15,
        triage_recall_7=funnel.triage_recall_7,
        exam_recall=funnel.exam_recall,
        hit_at_5=funnel.hit_at_5,
        hit_rank=funnel.hit_rank,
        mrr=funnel.mrr,
        phase2b_triggered=funnel.phase2b_triggered,
        exit_reason=pipeline["exit_reason"].value if pipeline["exit_reason"] else "",
        suspect_count=len(pipeline["suspects"]),
        total_candidates=len(retrieval.candidate_set.commits),
        elapsed_ms=round((time.time() - t0) * 1000, 1),
    )
    return result


def _run_pipeline(retrieval: Any, case: EvalCase, llm: Any) -> dict:
    """Execute Phase 1 → Phase 2 → Phase 2b, return intermediate outputs."""
    shortlist = compute_pre_scores(retrieval.candidate_set, retrieval.problem_statement)
    triage = assign_tiers(shortlist)

    git = GitContextProvider(str(case.repo_path), case.temporal_bound)
    inv = RevisedScopedInvestigator(
        llm=llm, problem=retrieval.problem_statement,
        triage=triage, candidate_set=retrieval.candidate_set, git=git,
    )
    t_agent = time.time()
    p2_result = inv.investigate()
    agent_ms = (time.time() - t_agent) * 1000

    suspects, p2b_result, exit_reason = run_phase2b(
        p2_result, retrieval.problem_statement, triage,
        retrieval.candidate_set, git, llm,
    )

    return {
        "shortlist": shortlist,
        "triage": triage,
        "p2_result": p2_result,
        "suspects": suspects,
        "phase2b_triggered": p2b_result is not None,
        "exit_reason": exit_reason,
        "agent_ms": agent_ms,
    }


def _compute_case_funnel(
    bug_hashes: list[str], retrieval: Any, pipeline: dict,
) -> FunnelMetrics:
    """Compute 5-stage funnel metrics for a case."""
    best_gt = _best_gt_sha(bug_hashes, retrieval.candidate_set)
    if not best_gt:
        return FunnelMetrics(
            recall_100=False, phase2b_triggered=pipeline["phase2b_triggered"],
        )
    return compute_funnel(
        best_gt, retrieval.candidate_set, pipeline["shortlist"],
        pipeline["triage"], pipeline["p2_result"].tool_trace,
        pipeline["suspects"], pipeline["phase2b_triggered"],
    )


def _write_case_trace(
    case: EvalCase, retrieval: Any, pipeline: dict, funnel: FunnelMetrics,
) -> None:
    """Write investigation trace JSON for a case."""
    suspect_dicts = [s.to_dict() for s in pipeline["suspects"]]
    tool_dicts = [{"tool": tc.tool, "args": tc.args} for tc in pipeline["p2_result"].tool_trace]
    trace = build_v42_trace(
        issue_key=case.issue_key,
        temporal_bound=case.temporal_bound,
        candidate_count=len(retrieval.candidate_set.commits),
        suspects=suspect_dicts,
        tool_trace=tool_dicts,
        funnel=funnel,
        exit_reason=pipeline["exit_reason"].value if pipeline["exit_reason"] else "",
        agent_ms=pipeline["agent_ms"],
    )
    TraceWriter(TRACES_DIR).write(trace)


def _best_gt_sha(bug_hashes: list[str], cs: Any) -> str:
    """Find the bug hash with the best recall@100 rank."""
    best_sha, best_rank = "", float("inf")
    for bh in bug_hashes:
        diag = compute_recall_at_k(cs, bh, k=100)
        if diag.found and diag.rank is not None and diag.rank < best_rank:
            best_sha, best_rank = bh, diag.rank
    return best_sha


def print_results(cases: list[dict]) -> dict:
    """Print aggregate funnel metrics and return summary dict."""
    completed = [c for c in cases if c.get("status") == "completed"]
    n_total, n_completed = len(cases), len(completed)

    def _count(field: str) -> int:
        return sum(1 for c in completed if c.get(field) is True)

    counts = {k: _count(k) for k in [
        "recall_100", "pre_score_recall_15", "triage_recall_7",
        "exam_recall", "hit_at_5", "phase2b_triggered",
    ]}

    mrr_values = [c.get("mrr", 0.0) or 0.0 for c in completed]
    mean_mrr = sum(mrr_values) / len(mrr_values) if mrr_values else 0.0

    print(f"\n{'='*72}")
    print(f"V4.2 SCOPED EVAL RESULTS  (n={n_total}, completed={n_completed})")
    print(f"{'='*72}")
    if n_completed:
        for label, key in [
            ("Recall@100", "recall_100"), ("Pre-score Recall@15", "pre_score_recall_15"),
            ("Triage Recall@7", "triage_recall_7"), ("Exam Recall", "exam_recall"),
            ("Hit@5", "hit_at_5"),
        ]:
            print(f"  {label:22s}{counts[key]}/{n_completed} = {counts[key]/n_completed:.3f}")
    print(f"  {'MRR':22s}{mean_mrr:.4f}")
    print(f"  {'Phase 2b triggered':22s}{counts['phase2b_triggered']}/{n_completed}")

    hit_cases = [c["issue_key"] for c in completed if c.get("hit_at_5")]
    if hit_cases:
        print(f"\n  Hit cases: {', '.join(hit_cases)}")
    print(f"{'='*72}")

    return {
        "n_total": n_total, "n_completed": n_completed,
        **counts, "mrr": round(mean_mrr, 4),
        "hit_cases": hit_cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="V4.2 scoped eval")
    parser.add_argument("--n", type=int, default=None, help="Max cases to run")
    parser.add_argument("--model", type=str, default=None, help="INVESTIGATION_MODEL override")
    args = parser.parse_args()

    if args.model:
        import os
        os.environ["INVESTIGATION_MODEL"] = args.model

    print("Loading ground truth graph...")
    gt = GroundTruthGraph.from_replication_zip(str(ZIP_PATH))

    print("Building eval cases...")
    cases = build_eval_cases(gt, max_n=args.n)
    print(f"  {len(cases)} cases resolved\n")

    llm = get_provider(phase="investigation")
    print(f"  LLM provider: {llm.model_name}\n")

    case_results: list[dict] = []
    for i, case in enumerate(cases, 1):
        print(f"[{i:2d}/{len(cases)}] {case.issue_key:16s}", end="  ", flush=True)
        result = run_single_case(case, llm)
        case_results.append(result)

        status = result.get("status", "?")
        if status == "completed":
            hit = "HIT" if result.get("hit_at_5") else "---"
            rank = result.get("hit_rank", "")
            rank_str = f"@{rank}" if rank else ""
            p2b = " +2b" if result.get("phase2b_triggered") else ""
            print(f"{hit}{rank_str}{p2b}  ({result.get('elapsed_ms', 0):.0f}ms)")
        else:
            print(f"{status.upper()} ({result.get('reason', '')})")

    aggregates = print_results(case_results)

    output = {
        "checkpoint": "scoped-eval-v42",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": llm.model_name,
        "aggregates": aggregates,
        "cases": case_results,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"\nResults written to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
