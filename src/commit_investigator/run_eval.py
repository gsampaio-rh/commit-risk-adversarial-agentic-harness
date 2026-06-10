"""First evaluation run: mock agent on test_small gray-zone commits.

Demonstrates the full harness pipeline: route → investigate → evaluate → report.
Run: python -m commit_investigator.run_eval
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from commit_investigator.context_builder import InvestigationContext
from commit_investigator.eval_harness import EvalHarness, save_eval_report
from commit_investigator.ground_truth import GroundTruthGraph
from commit_investigator.llm import MockLLMProvider
from commit_investigator.orchestrator import AgentOrchestrator
from commit_investigator.report import CommitInvestigationReport
from commit_investigator.router import Route, XGBoostRouter


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evaluation on test split")
    parser.add_argument("--train", default="data/apachejit/apachejit_train.csv")
    parser.add_argument("--test", default="data/apachejit/apachejit_test_small.csv")
    parser.add_argument("--zip", default="data/apachejit/apachejit_dataset_replication.zip")
    parser.add_argument("--max-evals", type=int, default=100, help="Max commits to evaluate")
    parser.add_argument("--output-dir", default="output/eval_run_1")
    args = parser.parse_args()

    print("Loading ground truth graph...", file=sys.stderr)
    gt = GroundTruthGraph.from_replication_zip(args.zip)

    print("Training router...", file=sys.stderr)
    router = XGBoostRouter()
    metrics = router.train(args.train)
    print(f"  Router AUC: {metrics.auc_roc:.4f}", file=sys.stderr)

    print("Routing test split...", file=sys.stderr)
    decisions = router.route_split(args.test)

    gray_zone = [d for d in decisions if d.route == Route.INVESTIGATE]
    high_zone = [d for d in decisions if d.route == Route.HIGH]
    target_commits = (gray_zone + high_zone)[:args.max_evals]
    print(f"  Gray zone: {len(gray_zone)}, High: {len(high_zone)}, evaluating: {len(target_commits)}", file=sys.stderr)

    buggy_lookup = _load_buggy_labels(args.test)

    print("Running mock investigations...", file=sys.stderr)
    orchestrator = AgentOrchestrator(llm_provider=MockLLMProvider(), max_turns=1)

    eval_tuples: list[tuple[CommitInvestigationReport, bool, Route]] = []
    for decision in target_commits:
        context = InvestigationContext(
            commit_id=decision.commit_id,
            project=decision.project,
            diff=None,
            message=None,
            touched_files=[],
            csv_features={},
            file_histories={},
            author_stats=None,
            missing_reasons=["No git clone available for mock eval"],
        )

        report = orchestrator.investigate(
            commit_id=decision.commit_id,
            project=decision.project,
            context=context,
        )

        buggy_label = buggy_lookup.get(decision.commit_id, False)
        eval_tuples.append((report, buggy_label, decision.route))

    print("Running evaluation harness...", file=sys.stderr)
    harness = EvalHarness(ground_truth=gt, budget_tier="mock-$0", max_evals=args.max_evals)
    eval_report = harness.evaluate_batch(eval_tuples)
    eval_report.metadata = {
        "router_auc": metrics.auc_roc,
        "gray_zone_total": len(gray_zone),
        "high_zone_total": len(high_zone),
        "evaluated": len(target_commits),
        "provider": "mock",
    }

    save_eval_report(eval_report, args.output_dir)
    print(f"\nResults saved to {args.output_dir}/", file=sys.stderr)
    print(f"  Dimension averages: {eval_report.dimension_averages}", file=sys.stderr)
    print(f"  Router baseline: {eval_report.router_baseline}", file=sys.stderr)


def _load_buggy_labels(csv_path: str) -> dict[str, bool]:
    labels = {}
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            commit_id = row.get("commit_id", "").strip()
            buggy = row.get("buggy", "False") in ("True", "true", "1")
            labels[commit_id] = buggy
    return labels


if __name__ == "__main__":
    main()
