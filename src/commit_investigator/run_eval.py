"""Evaluation runner: real agent investigation on test_small gray-zone commits.

Wire-up: route → build real context (git+CSV) → investigate (LLM) → evaluate (GT+JIRA) → report.
Run: python -m commit_investigator.run_eval

Each run creates a timestamped folder under output/runs/ with:
  run-config.json        — all CLI args, env, git rev
  run.log                — full log capture
  eval-report.json       — aggregate scores
  eval-report.md         — human-readable report
  investigations/        — per-commit investigation reports
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from commit_investigator.context_builder import AuthorStatsIndex, CommitContextBuilder
from commit_investigator.eval_harness import EvalHarness, save_eval_report
from commit_investigator.git_context import GitContextProvider, GitRepoNotFoundError
from commit_investigator.ground_truth import GroundTruthGraph
from commit_investigator.jira_client import JiraClient
from commit_investigator.llm import CursorSDKProvider, MockLLMProvider, get_provider
from commit_investigator.orchestrator import AgentOrchestrator
from commit_investigator.report import CommitInvestigationReport
from commit_investigator.router import Route, XGBoostRouter

V1_PROJECTS = {"camel", "hadoop"}
REPOS_DIR = Path("data/repos")

logger = logging.getLogger("commit_investigator.run_eval")


def _build_run_dir(base: str, eval_mode: str, max_evals: int) -> Path:
    """Create a timestamped run directory: output/runs/YYYY-MM-DD_HH-MM-SS_<mode>_n<count>/"""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    tag = f"{ts}_{eval_mode}_n{max_evals}"
    run_dir = Path(base) / tag
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _setup_logging(run_dir: Path) -> logging.FileHandler:
    """Configure logging to write to both stderr and run.log inside the run folder."""
    log_path = run_dir / "run.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)

    return file_handler


def _save_run_config(run_dir: Path, args: argparse.Namespace, extra: dict) -> None:
    """Persist all run parameters and environment info for reproducibility."""
    git_rev = _git_rev()
    config = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "args": vars(args),
        "git_rev": git_rev,
        "python_version": sys.version,
        "cwd": os.getcwd(),
        **extra,
    }
    (run_dir / "run-config.json").write_text(
        json.dumps(config, indent=2, default=str), encoding="utf-8"
    )


def _git_rev() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        return "unknown"


def _save_investigation(
    inv_dir: Path,
    report: CommitInvestigationReport,
    buggy_label: bool,
    elapsed: float,
    route: str,
) -> None:
    """Persist a single investigation report as JSON."""
    data = {
        "commit_id": report.commit_id,
        "project": report.project,
        "buggy_label": buggy_label,
        "route": route,
        "elapsed_seconds": round(elapsed, 2),
        "risk_level": report.risk_assessment.level.value,
        "confidence": report.risk_assessment.confidence,
        "reasoning_summary": report.reasoning_summary,
        "findings": report.findings,
        "localization": [
            {"file": loc.file, "lines": loc.lines, "rationale": loc.rationale}
            for loc in report.localization
        ],
        "recommendations": [
            {"action": r.action, "priority": r.priority.value, "rationale": r.rationale}
            for r in report.recommendations
        ],
        "evidence": [
            {"type": e.type.value, "source": e.source, "relevance": e.relevance}
            for e in report.evidence
        ],
        "tools_used": report.tools_used,
        "turn_count": report.turn_count,
        "metadata": report.metadata,
    }
    filename = f"{report.commit_id[:12]}_{report.project}.json"
    (inv_dir / filename).write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8"
    )


def _log(msg: str) -> None:
    """Print to stderr and also emit to the file logger."""
    print(msg, file=sys.stderr)
    logger.info(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evaluation on test split")
    parser.add_argument("--train", default="data/apachejit/apachejit_train.csv")
    parser.add_argument("--test", default="data/apachejit/apachejit_test_small.csv")
    parser.add_argument("--zip", default="data/apachejit/apachejit_dataset_replication.zip")
    parser.add_argument("--repos-dir", default="data/repos")
    parser.add_argument("--max-evals", type=int, default=100, help="Max commits to evaluate")
    parser.add_argument("--output-dir", default=None, help="Override run dir (default: auto-timestamped)")
    parser.add_argument("--runs-base", default="output/runs", help="Base directory for timestamped runs")
    parser.add_argument("--mock", action="store_true", help="Use mock LLM provider")
    args = parser.parse_args()

    repos_dir = Path(args.repos_dir)

    eval_mode = "mock" if args.mock else "real"
    if args.output_dir:
        run_dir = Path(args.output_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        run_dir = _build_run_dir(args.runs_base, eval_mode, args.max_evals)

    file_handler = _setup_logging(run_dir)
    inv_dir = run_dir / "investigations"
    inv_dir.mkdir(exist_ok=True)

    run_start = time.time()
    _log(f"Run directory: {run_dir}")

    _log("Loading ground truth graph...")
    gt = GroundTruthGraph.from_replication_zip(args.zip)

    _log("Building author stats index from train split...")
    author_stats = AuthorStatsIndex.from_train_csv(args.train)

    _log("Training router...")
    router = XGBoostRouter()
    metrics = router.train(args.train)
    _log(f"  Router AUC: {metrics.auc_roc:.4f}")

    _log("Routing test split...")
    decisions = router.route_split(args.test)

    csv_rows = _load_csv_rows(args.test)
    buggy_lookup = {cid: row.get("buggy", "False") in ("True", "true", "1") for cid, row in csv_rows.items()}

    gray_zone = [d for d in decisions if d.route == Route.INVESTIGATE]
    high_zone = [d for d in decisions if d.route == Route.HIGH]

    _log("Initializing git context providers...")
    git_providers = _init_git_providers(repos_dir)
    _log(f"  Available: {sorted(git_providers.keys())}")

    target_commits, strat_stats = _select_stratified_commits(
        gray_zone + high_zone,
        csv_rows,
        gt,
        git_providers,
        buggy_lookup,
        args.max_evals,
    )
    _log(
        f"  Gray zone: {len(gray_zone)}, High: {len(high_zone)}, "
        f"V1 stratified: {strat_stats['v1_routed']}, evaluating: {len(target_commits)} "
        f"(buggy_chain={strat_stats['buggy_with_chain']}, clean={strat_stats['clean']})"
    )

    if args.mock:
        llm = MockLLMProvider()
    else:
        llm = get_provider(prefer_real=True)
        if isinstance(llm, MockLLMProvider):
            _log(
                "ERROR: Real eval requested but no CURSOR_API_KEY or OPENAI_API_KEY set. "
                "Export an API key or pass --mock for methodology testing."
            )
            sys.exit(1)
    _log(f"LLM provider: {llm.model_name} (eval_mode={eval_mode})")

    _save_run_config(run_dir, args, {
        "eval_mode": eval_mode,
        "provider": llm.model_name,
        "router_auc": metrics.auc_roc,
        "v1_projects": sorted(V1_PROJECTS),
        "stratification": strat_stats,
    })

    orchestrator = AgentOrchestrator(llm_provider=llm, max_turns=1)

    _log("Initializing JIRA client...")
    jira = JiraClient()

    _log(f"\nRunning investigations on {len(target_commits)} commits...")
    eval_tuples: list[tuple[CommitInvestigationReport, bool, Route]] = []
    total_cost = 0.0
    skipped = 0
    timings: list[dict] = []

    for i, decision in enumerate(target_commits, 1):
        project_lower = _normalize_project(decision.project)
        git_provider = git_providers.get(project_lower)

        if git_provider is None:
            skipped += 1
            continue

        csv_row = csv_rows.get(decision.commit_id, {})

        builder = CommitContextBuilder(git_provider, author_stats)
        context = builder.build(decision.commit_id, project_lower, csv_row)

        t0 = time.time()
        report = orchestrator.investigate(
            commit_id=decision.commit_id,
            project=project_lower,
            context=context,
        )
        elapsed = time.time() - t0

        cost = report.metadata.get("total_cost", 0.0)
        total_cost += cost

        buggy_label = buggy_lookup.get(decision.commit_id, False)
        eval_tuples.append((report, buggy_label, decision.route))

        _save_investigation(inv_dir, report, buggy_label, elapsed, decision.route.value)
        timings.append({
            "commit_id": decision.commit_id[:12],
            "project": project_lower,
            "elapsed_s": round(elapsed, 1),
            "cost": round(cost, 6),
            "risk": report.risk_assessment.level.value,
            "buggy": buggy_label,
        })

        _print_progress(i, len(target_commits), decision, buggy_label, report, elapsed, cost)

    _log(f"\n  Investigated: {len(eval_tuples)}, Skipped: {skipped}")
    _log(f"  Total LLM cost: ${total_cost:.4f}")

    judge_provider = None if args.mock else llm
    _log("\nRunning evaluation harness...")
    harness = EvalHarness(
        ground_truth=gt,
        jira_client=jira,
        git_providers=git_providers,
        budget_tier="$50" if not args.mock else "mock-$0",
        max_evals=args.max_evals,
        judge_provider=judge_provider,
    )
    eval_report = harness.evaluate_batch(eval_tuples)
    eval_report.cost_actual = total_cost
    eval_report.metadata = {
        "run_dir": str(run_dir),
        "run_started_utc": datetime.fromtimestamp(run_start, tz=timezone.utc).isoformat(),
        "run_elapsed_seconds": round(time.time() - run_start, 1),
        "router_auc": metrics.auc_roc,
        "gray_zone_total": len(gray_zone),
        "high_zone_total": len(high_zone),
        "evaluated": len(eval_tuples),
        "skipped": skipped,
        "provider": llm.model_name,
        "eval_mode": eval_mode,
        "v1_projects": sorted(V1_PROJECTS),
        "stratification": strat_stats,
        "timings": timings,
    }

    save_eval_report(eval_report, run_dir)
    _log(f"\nResults saved to {run_dir}/")
    _log(f"  Dimension averages: {eval_report.dimension_averages}")
    if eval_report.subset_averages:
        _log(f"  Subset averages: {eval_report.subset_averages}")
    if eval_report.stratum_averages:
        buggy_n = int(eval_report.stratum_averages.get("buggy", {}).get("_count", 0))
        clean_n = int(eval_report.stratum_averages.get("clean", {}).get("_count", 0))
        _log(f"  Stratum counts: buggy={buggy_n}, clean={clean_n}")
    _log(f"  Router baseline: {eval_report.router_baseline}")

    logging.getLogger().removeHandler(file_handler)
    file_handler.close()


def _load_csv_rows(csv_path: str) -> dict[str, dict[str, str]]:
    """Load all CSV rows keyed by commit_id."""
    rows: dict[str, dict[str, str]] = {}
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            commit_id = row.get("commit_id", "").strip()
            if commit_id:
                rows[commit_id] = dict(row)
    return rows


def _filter_v1_projects(
    decisions: list,
    csv_rows: dict[str, dict[str, str]],
) -> list:
    """Keep only commits from V1 projects (camel, hadoop)."""
    filtered = []
    for d in decisions:
        row = csv_rows.get(d.commit_id, {})
        project = _normalize_project(row.get("project", d.project))
        if project in V1_PROJECTS:
            filtered.append(d)
    return filtered


def _select_stratified_commits(
    decisions: list,
    csv_rows: dict[str, dict[str, str]],
    gt: GroundTruthGraph,
    git_providers: dict[str, GitContextProvider],
    buggy_lookup: dict[str, bool],
    max_evals: int,
) -> tuple[list, dict[str, int]]:
    """Pick commits that exercise D1–D5: prioritize buggy rows with full GT chains."""
    v1_decisions = _filter_v1_projects(decisions, csv_rows)

    buggy_with_chain: list = []
    buggy_partial: list = []
    clean: list = []

    for decision in v1_decisions:
        commit_id = decision.commit_id
        row = csv_rows.get(commit_id, {})
        project = _normalize_project(row.get("project", decision.project))
        is_buggy = buggy_lookup.get(commit_id, False)

        if not is_buggy:
            clean.append(decision)
            continue

        chain = gt.get_chain(commit_id)
        provider = git_providers.get(project)
        if (
            chain.fix_hashes
            and chain.issue_keys
            and provider is not None
            and provider.commit_exists(commit_id)
        ):
            buggy_with_chain.append(decision)
        else:
            buggy_partial.append(decision)

    min_chain = min(len(buggy_with_chain), max(1, max_evals // 2))
    selected: list = list(buggy_with_chain[:min_chain])
    seen_ids = {d.commit_id for d in selected}

    for pool in (clean, buggy_partial, buggy_with_chain[min_chain:]):
        for decision in pool:
            if len(selected) >= max_evals:
                break
            if decision.commit_id not in seen_ids:
                selected.append(decision)
                seen_ids.add(decision.commit_id)

    def _has_full_chain(decision: object) -> bool:
        commit_id = decision.commit_id
        if not buggy_lookup.get(commit_id, False):
            return False
        chain = gt.get_chain(commit_id)
        return bool(chain.fix_hashes and chain.issue_keys)

    stats = {
        "v1_routed": len(v1_decisions),
        "buggy_with_chain": sum(1 for d in selected if _has_full_chain(d)),
        "clean": sum(1 for d in selected if not buggy_lookup.get(d.commit_id, False)),
        "buggy_partial": sum(
            1 for d in selected
            if buggy_lookup.get(d.commit_id, False) and not _has_full_chain(d)
        ),
    }
    return selected[:max_evals], stats


def _normalize_project(project: str) -> str:
    """Normalize project name to lowercase V1 format."""
    p = project.strip().lower()
    if "/" in p:
        p = p.split("/")[-1]
    return p


def _init_git_providers(repos_dir: Path) -> dict[str, GitContextProvider]:
    """Create GitContextProvider for each available project repo."""
    providers: dict[str, GitContextProvider] = {}
    for project in V1_PROJECTS:
        try:
            providers[project] = GitContextProvider.for_project(project, repos_dir)
        except GitRepoNotFoundError:
            print(f"  WARNING: {project} repo not found at {repos_dir / project}", file=sys.stderr)
    return providers


def _print_progress(
    i: int,
    total: int,
    decision: object,
    buggy: bool,
    report: CommitInvestigationReport,
    elapsed: float,
    cost: float,
) -> None:
    """Print one-line progress for each investigated commit."""
    risk = report.risk_assessment.level.value
    conf = report.risk_assessment.confidence
    label = "BUG" if buggy else "clean"
    cid = report.commit_id[:12]
    print(
        f"  [{i:3d}/{total}] {cid} {report.project:8s} "
        f"risk={risk:8s} conf={conf:.2f} label={label:5s} "
        f"t={elapsed:.1f}s cost=${cost:.4f}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
