"""Multi-turn A/B experiment on D3=0 hard commits (iter-3f-multiturn-ab).

Runs 2-turn investigation with targeted turn-2 context injection on the 3 hard
commits identified in iter-3-n20-v2, scores D3, and records activation decision.

Usage:
  python -m commit_investigator.runners.run_multiturn_ab
  python -m commit_investigator.runners.run_multiturn_ab --mock
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from commit_investigator.context.context_builder import AuthorStatsIndex, CommitContextBuilder
from commit_investigator.runners.eval_common import _load_dotenv, _git_rev
from commit_investigator.runners.eval_harness import EvalHarness
from commit_investigator.context.git_context import GitContextProvider, GitRepoNotFoundError
from commit_investigator.infra.ground_truth import GroundTruthGraph
from commit_investigator.infra.jira_client import JiraClient
from commit_investigator.infra.llm import MockLLMProvider, get_provider
from commit_investigator.pipeline.orchestrator import AgentOrchestrator, FollowUpMode
from commit_investigator.analysis.report import CommitInvestigationReport
from commit_investigator.routing.router import Route

HARD_COMMITS: list[tuple[str, str]] = [
    ("2213f71944ae", "camel"),
    ("409664582f53", "camel"),
    ("572f3cee35fe", "camel"),
]

FROZEN_CONTROL_D3: dict[str, float] = {
    "2213f71944ae": 0.0,
    "409664582f53": 0.0,
    "572f3cee35fe": 0.0,
}

BASELINE_COST_PER_COMMIT = 0.1043 / 20
COST_CAP = BASELINE_COST_PER_COMMIT * 2
DELTA_D3_THRESHOLD = 0.25
EXP_REPORT_PATH = Path(".harness/evals/exp-multiturn-ab.json")

logger = logging.getLogger("commit_investigator.runners.run_multiturn_ab")


def _build_run_dir(base: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = base / f"{ts}_multiturn_ab"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _load_csv_row(csv_path: Path, commit_id: str) -> dict[str, str] | None:
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("commit_id", "").strip().startswith(commit_id):
                return dict(row)
    return None


def _resolve_commit_id(csv_path: Path, prefix: str) -> str:
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cid = row.get("commit_id", "").strip()
            if cid.startswith(prefix):
                return cid
    return prefix


def _save_investigation(
    inv_dir: Path,
    report: CommitInvestigationReport,
    d3_score: float,
) -> None:
    path = inv_dir / f"{report.commit_id[:12]}_{report.project}.json"
    payload = json.loads(report.model_dump_json())
    payload["eval_d3"] = d3_score
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Multi-turn A/B on D3=0 hard commits")
    parser.add_argument("--mock", action="store_true", help="Use mock LLM (unit smoke only)")
    parser.add_argument("--repos-dir", default="data/repos")
    parser.add_argument("--train-csv", default="data/apachejit/apachejit_train.csv")
    parser.add_argument("--test-csv", default="data/apachejit/apachejit_test_large.csv")
    parser.add_argument("--zip", default="data/apachejit/apachejit_dataset_replication.zip")
    parser.add_argument("--runs-base", default="output/runs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    run_dir = _build_run_dir(Path(args.runs_base))
    inv_dir = run_dir / "investigations"
    inv_dir.mkdir(exist_ok=True)

    test_csv = Path(args.test_csv)
    gt = GroundTruthGraph.from_replication_zip(args.zip)
    author_stats = AuthorStatsIndex.from_train_csv(args.train_csv)

    try:
        git_provider = GitContextProvider.for_project("camel", args.repos_dir)
    except GitRepoNotFoundError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    if args.mock:
        llm = MockLLMProvider()
    else:
        llm = get_provider(prefer_real=True)
        if isinstance(llm, MockLLMProvider):
            logger.error("Real LLM required. Set CURSOR_API_KEY or use --mock.")
            sys.exit(1)

    jira = JiraClient()
    harness = EvalHarness(
        ground_truth=gt,
        jira_client=jira,
        git_providers={"camel": git_provider},
        judge_provider=None if args.mock else llm,
    )

    config = {
        "experiment": "iter-3f-multiturn-ab",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_rev": _git_rev(),
        "commits": [c[0] for c in HARD_COMMITS],
        "max_turns": 2,
        "follow_up_mode": FollowUpMode.ALWAYS.value,
        "judge_model": llm.model_name,
        "frozen_control_d3": FROZEN_CONTROL_D3,
        "cost_cap_usd": COST_CAP,
        "mock": args.mock,
    }
    (run_dir / "run-config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    per_commit: list[dict] = []
    d3_2turn_scores: list[float] = []
    d3_1turn_scores: list[float] = []

    for prefix, project in HARD_COMMITS:
        commit_id = _resolve_commit_id(test_csv, prefix)
        csv_row = _load_csv_row(test_csv, prefix)

        builder = CommitContextBuilder(git_provider, author_stats)
        context = builder.build(commit_id, project, csv_row)

        orchestrator = AgentOrchestrator(
            llm_provider=llm,
            max_turns=2,
            follow_up_mode=FollowUpMode.ALWAYS,
            checkpoint_dir=run_dir / "checkpoints" / prefix[:12],
        )

        t0 = time.time()
        report = orchestrator.investigate(
            commit_id=commit_id,
            project=project,
            csv_row=csv_row,
            git_provider=git_provider,
            context=context,
        )
        elapsed = time.time() - t0

        eval_result = harness.evaluate_report(report, buggy_label=True, route=Route.INVESTIGATE)
        d3_2turn = eval_result.scores.get("D3")
        d3_2turn_val = d3_2turn.score if d3_2turn else 0.0

        control_prefix = prefix[:12]
        d3_1turn = next(
            (v for k, v in FROZEN_CONTROL_D3.items() if commit_id.startswith(k)),
            0.0,
        )
        cost = report.metadata.get("total_cost_usd", report.metadata.get("total_cost", 0.0))

        per_commit.append({
            "commit_id": commit_id,
            "d3_1turn": d3_1turn,
            "d3_2turn": round(d3_2turn_val, 4),
            "cost_usd": round(float(cost), 6),
            "turn_count": report.turn_count,
            "elapsed_s": round(elapsed, 1),
            "d3_details": d3_2turn.details if d3_2turn else "",
        })
        d3_2turn_scores.append(d3_2turn_val)
        d3_1turn_scores.append(d3_1turn)

        _save_investigation(inv_dir, report, d3_2turn_val)
        logger.info(
            "  %s D3_1turn=%.2f D3_2turn=%.2f cost=$%.4f turns=%d t=%.1fs",
            commit_id[:12], d3_1turn, d3_2turn_val, cost, report.turn_count, elapsed,
        )

    mean_1turn = sum(d3_1turn_scores) / len(d3_1turn_scores)
    mean_2turn = sum(d3_2turn_scores) / len(d3_2turn_scores)
    delta_d3 = mean_2turn - mean_1turn
    improved = sum(1 for row in per_commit if row["d3_2turn"] > row["d3_1turn"])

    decision = (
        "multi-turn-activated"
        if delta_d3 >= DELTA_D3_THRESHOLD and improved >= 2
        else "single-turn-maintained"
    )

    exp_report = {
        "task_id": "iter-3f-multiturn-ab",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "judge_model": llm.model_name,
        "decision": decision,
        "delta_d3": round(delta_d3, 4),
        "mean_d3_1turn_control": round(mean_1turn, 4),
        "mean_d3_2turn": round(mean_2turn, 4),
        "improved_count": improved,
        "cost_cap_usd": COST_CAP,
        "baseline_cost_per_commit": round(BASELINE_COST_PER_COMMIT, 6),
        "per_commit": per_commit,
        "criteria": {
            "delta_d3_threshold": DELTA_D3_THRESHOLD,
            "min_improved_commits": 2,
            "delta_d3_pass": delta_d3 >= DELTA_D3_THRESHOLD,
            "improved_pass": improved >= 2,
            "all_costs_under_cap": all(r["cost_usd"] <= COST_CAP for r in per_commit),
        },
    }

    EXP_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXP_REPORT_PATH.write_text(json.dumps(exp_report, indent=2), encoding="utf-8")
    (run_dir / "exp-report.json").write_text(json.dumps(exp_report, indent=2), encoding="utf-8")

    logger.info("Decision: %s (ΔD3=%.3f, improved=%d/3)", decision, delta_d3, improved)
    logger.info("Report: %s", EXP_REPORT_PATH)


if __name__ == "__main__":
    main()
