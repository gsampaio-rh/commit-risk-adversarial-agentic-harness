#!/usr/bin/env python3
"""Collect Task Subagent results and compute eval metrics.

After running Task subagent investigations, paste each subagent's
response text into a file at results/<run>/responses/<idx>_<issue>.txt.
This script parses the responses, scores them, and produces a
comparison report.

Usage:
    python scripts/collect_subagent_results.py \
        --manifest results/v3-subagent-eval-v2/manifest.json \
        --responses-dir results/v3-subagent-eval-v2/responses \
        --repos-dir data/repos \
        --output-dir results/v3-subagent-eval-v2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commit_investigator.infra.git_context import GitContextProvider
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.agent.orchestrator import _attach_evidence_scores
from commit_investigator.agent.task_subagent_investigator import (
    parse_subagent_response,
    suspects_to_report,
)
from commit_investigator.eval.eval_metrics import (
    aggregate_results,
    evaluate_attribution,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect subagent eval results")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--responses-dir", required=True)
    parser.add_argument("--repos-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text())
    responses_dir = Path(args.responses_dir)
    repos_dir = Path(args.repos_dir)
    output_dir = Path(args.output_dir)

    cases = manifest["cases"]
    results = []
    per_case_data = []

    for case in cases:
        idx = case["idx"]
        issue_key = case["issue_key"]
        project = case["project"]
        bug_hash = case["bug_hash"]
        fix_hash = case["fix_hash"]
        temporal_bound = case["temporal_bound"]

        response_files = list(responses_dir.glob(f"{idx:03d}_{issue_key}*"))
        if not response_files:
            response_files = list(responses_dir.glob(f"{idx:03d}_*"))

        if not response_files:
            print(f"  [{idx:03d}] {project}/{issue_key}: NO RESPONSE FILE")
            per_case_data.append({
                "idx": idx,
                "project": project,
                "issue_key": issue_key,
                "bug_hash": bug_hash,
                "hit_at_1": False,
                "hit_at_3": False,
                "hit_at_5": False,
                "rank": None,
                "suspects_found": 0,
                "error": "no_response_file",
            })
            continue

        response_text = response_files[0].read_text()
        suspects, reasoning = parse_subagent_response(response_text)

        problem = ProblemStatement(
            title=issue_key,
            description="",
            project=project,
            issue_key=issue_key,
        )

        repo_path = repos_dir / project.lower()
        git_provider = None
        if repo_path.exists():
            try:
                git_provider = GitContextProvider(repo_path, temporal_bound=temporal_bound)
                evidence_scores = _attach_evidence_scores(suspects, git_provider)
            except Exception:
                evidence_scores = []
        else:
            evidence_scores = []

        report = suspects_to_report(
            problem=problem,
            suspects=suspects,
            reasoning=reasoning,
            temporal_bound=temporal_bound,
        )
        report.metadata["evidence_scores"] = evidence_scores
        report.metadata["evidence_scoring_applied"] = True

        eval_result = evaluate_attribution(
            report=report,
            bug_hash=bug_hash,
            project=project,
            issue_key=issue_key,
            git_provider=git_provider,
        )
        results.append(eval_result)

        rank = None
        for detail in eval_result.suspect_details:
            if detail.get("is_ground_truth"):
                rank = detail["rank"]
                break

        status = f"Hit@1" if eval_result.hit_at_1 else (
            f"Hit@3(r{rank})" if eval_result.hit_at_3 else (
                f"Hit@5(r{rank})" if eval_result.hit_at_5 else "MISS"
            )
        )
        print(f"  [{idx:03d}] {project}/{issue_key}: {status} "
              f"({len(suspects)} suspects)")

        per_case_data.append({
            "idx": idx,
            "project": project,
            "issue_key": issue_key,
            "bug_hash": bug_hash,
            "hit_at_1": eval_result.hit_at_1,
            "hit_at_3": eval_result.hit_at_3,
            "hit_at_5": eval_result.hit_at_5,
            "rank": rank,
            "suspects_found": len(suspects),
        })

    agg = aggregate_results(results)

    print(f"\n{'='*60}")
    print(f"TASK SUBAGENT V2 RESULTS (n={len(results)})")
    print(f"{'='*60}")
    print(f"Hit@1: {agg.hit_at_1:.3f}")
    print(f"Hit@3: {agg.hit_at_3:.3f}")
    print(f"Hit@5: {agg.hit_at_5:.3f}")
    print(f"MRR:   {agg.mrr:.3f}")
    print(f"Evidence Grounding: {agg.evidence_grounding_rate:.3f}")
    print(f"{'='*60}")

    comparison = {
        "method": "task-subagent-v2",
        "model": "cursor-native-claude-sonnet",
        "prompt_version": "v2",
        "cases": len(results),
        "seed": manifest.get("seed", 42),
        "metrics": {
            "hit_at_1": agg.hit_at_1,
            "hit_at_3": agg.hit_at_3,
            "hit_at_5": agg.hit_at_5,
            "mrr": agg.mrr,
            "evidence_grounding": agg.evidence_grounding_rate,
        },
        "per_case": per_case_data,
        "comparison_vs_v1": {
            "v1_hit_at_5": 0.35,
            "v1_mrr": 0.242,
            "v2_hit_at_5": agg.hit_at_5,
            "v2_mrr": agg.mrr,
            "delta_hit_at_5": agg.hit_at_5 - 0.35,
            "delta_mrr": agg.mrr - 0.242,
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2)
    )
    print(f"\nResults written to {output_dir / 'comparison.json'}")


if __name__ == "__main__":
    main()
