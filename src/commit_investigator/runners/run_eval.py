"""Evaluation runner: real agent investigation on test_small gray-zone commits.

Wire-up: route → build real context (git+CSV) → investigate (LLM) → evaluate (GT+JIRA) → report.
Run: python -m commit_investigator.runners.run_eval

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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow `python src/commit_investigator/runners/run_eval.py` without PYTHONPATH
_SRC_ROOT = Path(__file__).resolve().parents[2]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from commit_investigator.context.context_builder import AuthorStatsIndex, CommitContextBuilder
from commit_investigator.runners.eval_common import _load_dotenv, _git_rev, _normalize_project
from commit_investigator.runners.eval_harness import EvalHarness, save_eval_report
from commit_investigator.context.git_context import GitContextProvider, GitRepoNotFoundError
from commit_investigator.infra.ground_truth import GroundTruthGraph
from commit_investigator.infra.jira_client import JiraClient
from commit_investigator.infra.llm import CursorSDKProvider, MockLLMProvider, get_provider
from commit_investigator.pipeline.orchestrator import AgentOrchestrator, InvalidInvestigationResponseError
from commit_investigator.analysis.report import CommitInvestigationReport
from commit_investigator.routing.router import Route, XGBoostRouter

V1_PROJECTS = {"camel", "hadoop"}
REPOS_DIR = Path("data/repos")

logger = logging.getLogger("commit_investigator.runners.run_eval")


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


def _investigate_with_retry(
    orchestrator: AgentOrchestrator,
    *,
    commit_id: str,
    project: str,
    context,
    max_attempts: int = 2,
) -> CommitInvestigationReport:
    """Investigate with one retry on empty/invalid LLM output (AC-12)."""
    last_error: InvalidInvestigationResponseError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return orchestrator.investigate(
                commit_id=commit_id,
                project=project,
                context=context,
            )
        except InvalidInvestigationResponseError as exc:
            last_error = exc
            if attempt >= max_attempts:
                raise
            _log(f"  Retry {attempt}/{max_attempts - 1} after invalid LLM response: {exc}")
            time.sleep(2.0 * attempt)
    raise last_error  # pragma: no cover


def _save_investigation(
    inv_dir: Path,
    report: CommitInvestigationReport,
    buggy_label: bool,
    elapsed: float,
    route: str,
    *,
    historical_defect_context_status: str | None = None,
) -> None:
    """Persist a single investigation report as JSON."""
    data = {
        "commit_id": report.commit_id,
        "project": report.project,
        "buggy_label": buggy_label,
        "route": route,
        "historical_defect_context_status": historical_defect_context_status or "disabled",
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


def _resolve_commit_id(prefix: str, csv_rows: dict[str, dict[str, str]]) -> str | None:
    """Match a short commit prefix to a full commit_id in the CSV."""
    prefix = prefix.strip().lower()
    if prefix in csv_rows:
        return prefix
    matches = [cid for cid in csv_rows if cid.lower().startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    return None



def _select_by_commit_ids(
    decisions: list,
    csv_rows: dict[str, dict[str, str]],
    git_providers: dict[str, GitContextProvider],
    commit_id_prefixes: list[str],
) -> tuple[list, dict[str, int]]:
    """Evaluate explicit commit IDs (AC-5 individual smoke)."""
    decision_by_id = {d.commit_id: d for d in decisions}
    selected: list = []
    missing: list[str] = []

    for prefix in commit_id_prefixes:
        full_id = _resolve_commit_id(prefix, csv_rows)
        if full_id is None:
            missing.append(prefix)
            continue
        row = csv_rows[full_id]
        project = _normalize_project(row.get("project", ""))
        if project not in git_providers:
            missing.append(prefix)
            continue
        decision = decision_by_id.get(full_id)
        if decision is None:
            missing.append(prefix)
            continue
        selected.append(decision)

    if missing:
        raise ValueError(
            f"Could not resolve commit(s) for evaluation: {', '.join(missing)}. "
            "Ensure IDs exist in the test CSV, have git clones, and were routed."
        )

    stats = {
        "v1_routed": len(selected),
        "buggy_with_chain": 0,
        "clean": 0,
        "buggy_partial": 0,
        "commit_ids_mode": 1,
        "requested_ids": commit_id_prefixes,
    }
    return selected, stats


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


def _init_git_providers(repos_dir: Path) -> dict[str, GitContextProvider]:
    """Create GitContextProvider for each available project repo."""
    providers: dict[str, GitContextProvider] = {}
    for project in V1_PROJECTS:
        try:
            providers[project] = GitContextProvider.for_project(project, repos_dir)
        except GitRepoNotFoundError:
            print(f"  WARNING: {project} repo not found at {repos_dir / project}", file=sys.stderr)
    return providers


def _compute_grounded_evidence_compliance(report: CommitInvestigationReport) -> float:
    """Fraction of diff_hunk evidence items that cite at least one +/- changed diff line."""
    from commit_investigator.hypothesis.hypothesis_engine import has_changed_line_citation
    from commit_investigator.analysis.report import EvidenceType
    diff_evidence = [e for e in report.evidence if e.type == EvidenceType.DIFF_HUNK]
    if not diff_evidence:
        return 0.0
    grounded = sum(1 for e in diff_evidence if has_changed_line_citation(e.content))
    return grounded / len(diff_evidence)


def _print_progress(
    i: int,
    total: int,
    decision: object,
    buggy: bool,
    report: CommitInvestigationReport,
    elapsed: float,
    cost: float,
    *,
    baseline_scores: dict[str, float] | None = None,
    mechanism_evaluator: bool = False,
) -> None:
    """Print rich per-commit progress with mechanism snippets and grounded evidence compliance."""
    risk = report.risk_assessment.level.value
    conf = report.risk_assessment.confidence
    label = "BUG" if buggy else "clean"
    cid = report.commit_id[:12]

    line = (
        f"  [{i:3d}/{total}] {cid} {report.project:8s} "
        f"risk={risk:8s} conf={conf:.2f} label={label:5s} "
        f"t={elapsed:.1f}s cost=${cost:.4f}"
    )

    if mechanism_evaluator:
        grounded = _compute_grounded_evidence_compliance(report)
        baseline = (baseline_scores or {}).get(cid, None)
        delta_str = f" Δbaseline={grounded - baseline:+.2f}" if baseline is not None else ""
        line += f" | grounded={grounded:.2f}{delta_str}"

    print(line, file=sys.stderr)

    # Per-evidence detail: source, relevance snippet, H4 grounding check on content.
    for j, ev in enumerate(report.evidence[:3], 1):
        from commit_investigator.hypothesis.hypothesis_engine import has_changed_line_citation
        grounded = "✓" if has_changed_line_citation(ev.content) else "✗"
        relevance_short = (ev.relevance[:100] + "…") if len(ev.relevance) > 100 else ev.relevance
        print(
            f"         E{j} [{ev.type.value:10s}] grounded={grounded}  {relevance_short}",
            file=sys.stderr,
        )


class EvalRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args

    def setup(self) -> None:
        """Initialize run directory, data sources, router, LLM, orchestrator, and JIRA."""
        repos_dir = Path(self.args.repos_dir)
        self.eval_mode = "mock" if self.args.mock else "real"
        run_count = len(self.args.commit_ids) if self.args.commit_ids else self.args.max_evals
        if self.args.output_dir:
            self.run_dir = Path(self.args.output_dir)
            self.run_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.run_dir = _build_run_dir(self.args.runs_base, self.eval_mode, run_count)

        self.file_handler = _setup_logging(self.run_dir)
        self.inv_dir = self.run_dir / "investigations"
        self.inv_dir.mkdir(exist_ok=True)
        self.run_start = time.time()
        _log(f"Run directory: {self.run_dir}")

        _log("Loading ground truth graph...")
        self.gt = GroundTruthGraph.from_replication_zip(self.args.zip)

        _log("Building author stats index from train split...")
        self.author_stats = AuthorStatsIndex.from_train_csv(self.args.train)

        _log("Training router...")
        self.router = XGBoostRouter()
        self.metrics = self.router.train(self.args.train)
        _log(f"  Router AUC: {self.metrics.auc_roc:.4f}")

        _log("Routing test split...")
        self.decisions = self.router.route_split(self.args.test)
        self.csv_rows = _load_csv_rows(self.args.test)
        self.buggy_lookup = {cid: row.get("buggy", "False") in ("True", "true", "1") for cid, row in self.csv_rows.items()}
        self.gray_zone = [d for d in self.decisions if d.route == Route.INVESTIGATE]
        self.high_zone = [d for d in self.decisions if d.route == Route.HIGH]

        _log("Initializing git context providers...")
        self.git_providers = _init_git_providers(repos_dir)
        _log(f"  Available: {sorted(self.git_providers.keys())}")

        if self.args.mock:
            self.llm = MockLLMProvider()
        else:
            self.llm = get_provider(prefer_real=True)
            if isinstance(self.llm, MockLLMProvider):
                _log(
                    "ERROR: Real eval requested but no LLM provider available. "
                    "Set CURSOR_API_KEY or OPENAI_API_KEY, configure Ollama locally, "
                    "or pass --mock for methodology testing."
                )
                sys.exit(1)
        _log(f"LLM provider: {self.llm.model_name} (eval_mode={self.eval_mode})")

        self.mechanism_evaluator = getattr(self.args, "enable_mechanism_evaluator", False)
        self.contrastive = getattr(self.args, "enable_contrastive", False)
        self.extended_context = getattr(self.args, "enable_extended_context", False)
        self.enable_historical_defect_context = getattr(
            self.args, "enable_historical_defect_context", False
        )
        self.baseline_scores: dict[str, float] = {}
        forensics_path = getattr(self.args, "forensics_json", None)
        if forensics_path:
            try:
                forensics_data = json.loads(Path(forensics_path).read_text())
                for c in forensics_data.get("commits", []):
                    prefix = c.get("commit_prefix", "")
                    d3 = c.get("scores", {}).get("D3")
                    if prefix and d3 is not None:
                        self.baseline_scores[prefix] = float(d3)
                _log(f"  Loaded {len(self.baseline_scores)} baseline D3 scores from {forensics_path}")
            except Exception as exc:
                _log(f"  WARNING: Could not load forensics baseline: {exc}")

        _log("Initializing JIRA client...")
        self.jira = JiraClient()

        self.orchestrator = AgentOrchestrator(
            llm_provider=self.llm,
            max_turns=1,
            enable_mechanism_evaluator=self.mechanism_evaluator,
            enable_contrastive=self.contrastive,
        )
        if self.mechanism_evaluator:
            _log("  [config] Mechanism evaluator loop ENABLED (symptom-first + changed-line evidence)")
        if self.contrastive:
            _log("  [config] Contrastive hypothesis ENABLED (diversity + grounded evidence selection)")
        if self.extended_context:
            _log("  [config] Extended context ENABLED (test-adjacency + blame snippets)")
        if self.enable_historical_defect_context:
            _log("  [config] Historical defect context ENABLED (ApacheJIT KNN priors)")

    def select_commits(self) -> None:
        """Select target commits via explicit IDs or stratified sampling."""
        if self.args.commit_ids:
            self.target_commits, self.strat_stats = _select_by_commit_ids(
                self.decisions, self.csv_rows, self.git_providers, self.args.commit_ids,
            )
            _log(f"  Commit-ids mode: evaluating {len(self.target_commits)} requested commit(s)")
        else:
            self.target_commits, self.strat_stats = _select_stratified_commits(
                self.gray_zone + self.high_zone,
                self.csv_rows,
                self.gt,
                self.git_providers,
                self.buggy_lookup,
                self.args.max_evals,
            )
            _log(
                f"  Gray zone: {len(self.gray_zone)}, High: {len(self.high_zone)}, "
                f"V1 stratified: {self.strat_stats['v1_routed']}, evaluating: {len(self.target_commits)} "
                f"(buggy_chain={self.strat_stats['buggy_with_chain']}, clean={self.strat_stats['clean']})"
            )

        _save_run_config(self.run_dir, self.args, {
            "eval_mode": self.eval_mode,
            "provider": self.llm.model_name,
            "router_auc": self.metrics.auc_roc,
            "v1_projects": sorted(V1_PROJECTS),
            "stratification": self.strat_stats,
            "enable_mechanism_evaluator": self.mechanism_evaluator,
            "enable_contrastive": self.contrastive,
            "extended_context": self.extended_context,
            "enable_historical_defect_context": self.enable_historical_defect_context,
        })

    def run_investigations(self) -> None:
        """Run agent investigations on each selected commit."""
        _log(f"\nRunning investigations on {len(self.target_commits)} commits...")
        self.eval_tuples: list[tuple[CommitInvestigationReport, bool, Route]] = []
        self.total_cost = 0.0
        self.skipped = 0
        self.timings: list[dict] = []

        for i, decision in enumerate(self.target_commits, 1):
            project_lower = _normalize_project(decision.project)
            git_provider = self.git_providers.get(project_lower)

            if git_provider is None:
                self.skipped += 1
                continue

            csv_row = self.csv_rows.get(decision.commit_id, {})
            builder = CommitContextBuilder(git_provider, self.author_stats)
            context = builder.build(
                decision.commit_id,
                project_lower,
                csv_row,
                include_test_adjacency=self.extended_context,
                include_blame_snippets=self.extended_context,
            )
            context.router_probability = decision.probability
            context.router_route = decision.route.value
            context.enable_historical_defect_context = self.enable_historical_defect_context

            t0 = time.time()
            try:
                report = _investigate_with_retry(
                    self.orchestrator,
                    commit_id=decision.commit_id,
                    project=project_lower,
                    context=context,
                )
            except InvalidInvestigationResponseError as exc:
                elapsed = time.time() - t0
                _log(
                    f"  [{i:3d}/{len(self.target_commits)}] {decision.commit_id[:12]} "
                    f"SKIPPED (LLM error after retries): {exc}"
                )
                self.skipped += 1
                continue
            elapsed = time.time() - t0

            cost = report.metadata.get("total_cost", 0.0)
            self.total_cost += cost

            buggy_label = self.buggy_lookup.get(decision.commit_id, False)
            self.eval_tuples.append((report, buggy_label, decision.route))

            _save_investigation(
                self.inv_dir,
                report,
                buggy_label,
                elapsed,
                decision.route.value,
                historical_defect_context_status=context.historical_defect_context_status,
            )
            self.timings.append({
                "commit_id": decision.commit_id[:12],
                "project": project_lower,
                "elapsed_s": round(elapsed, 1),
                "cost": round(cost, 6),
                "risk": report.risk_assessment.level.value,
                "buggy": buggy_label,
            })

            _print_progress(
                i, len(self.target_commits), decision, buggy_label, report, elapsed, cost,
                baseline_scores=self.baseline_scores,
                mechanism_evaluator=self.mechanism_evaluator or self.contrastive,
            )

        _log(f"\n  Investigated: {len(self.eval_tuples)}, Skipped: {self.skipped}")
        _log(f"  Total LLM cost: ${self.total_cost:.4f}")

    def run_harness(self) -> None:
        """Run the evaluation harness on investigation results."""
        judge_provider = None if self.args.mock else self.llm
        _log("\nRunning evaluation harness...")
        harness = EvalHarness(
            ground_truth=self.gt,
            jira_client=self.jira,
            git_providers=self.git_providers,
            budget_tier="$50" if not self.args.mock else "mock-$0",
            max_evals=self.args.max_evals,
            judge_provider=judge_provider,
        )
        self.eval_report = harness.evaluate_batch(self.eval_tuples)
        self.eval_report.cost_actual = self.total_cost
        self.eval_report.metadata = {
            "run_dir": str(self.run_dir),
            "run_started_utc": datetime.fromtimestamp(self.run_start, tz=timezone.utc).isoformat(),
            "run_elapsed_seconds": round(time.time() - self.run_start, 1),
            "router_auc": self.metrics.auc_roc,
            "gray_zone_total": len(self.gray_zone),
            "high_zone_total": len(self.high_zone),
            "evaluated": len(self.eval_tuples),
            "skipped": self.skipped,
            "provider": self.llm.model_name,
            "eval_mode": self.eval_mode,
            "v1_projects": sorted(V1_PROJECTS),
            "stratification": self.strat_stats,
            "timings": self.timings,
        }

    def write_reports(self) -> None:
        """Persist eval report and release log file handler."""
        save_eval_report(self.eval_report, self.run_dir)
        _log(f"\nResults saved to {self.run_dir}/")
        _log(f"  Dimension averages: {self.eval_report.dimension_averages}")
        if self.eval_report.subset_averages:
            _log(f"  Subset averages: {self.eval_report.subset_averages}")
        if self.eval_report.stratum_averages:
            buggy_n = int(self.eval_report.stratum_averages.get("buggy", {}).get("_count", 0))
            clean_n = int(self.eval_report.stratum_averages.get("clean", {}).get("_count", 0))
            _log(f"  Stratum counts: buggy={buggy_n}, clean={clean_n}")
        _log(f"  Router baseline: {self.eval_report.router_baseline}")

        logging.getLogger().removeHandler(self.file_handler)
        self.file_handler.close()


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the run_eval CLI argument parser (testable without running main)."""
    parser = argparse.ArgumentParser(description="Run evaluation on test split")
    parser.add_argument("--train", default="data/apachejit/apachejit_train.csv")
    parser.add_argument("--test", default="data/apachejit/apachejit_test_small.csv")
    parser.add_argument("--zip", default="data/apachejit/apachejit_dataset_replication.zip")
    parser.add_argument("--repos-dir", default="data/repos")
    parser.add_argument("--max-evals", type=int, default=100, help="Max commits to evaluate")
    parser.add_argument("--output-dir", default=None, help="Override run dir (default: auto-timestamped)")
    parser.add_argument("--runs-base", default="output/runs", help="Base directory for timestamped runs")
    parser.add_argument("--mock", action="store_true", help="Use mock LLM provider")
    parser.add_argument(
        "--commit-ids",
        nargs="+",
        metavar="COMMIT",
        help="Evaluate specific commit ID prefixes (overrides stratified selection)",
    )
    parser.add_argument(
        "--enable-mechanism-evaluator",
        action="store_true",
        help="Enable mechanism evaluator loop (symptom-first prompt with changed-line evidence grounding)",
    )
    parser.add_argument(
        "--enable-contrastive",
        action="store_true",
        help="Enable contrastive hypothesis generation (diversity constraint + grounded evidence selection)",
    )
    parser.add_argument(
        "--forensics-json",
        default=None,
        metavar="PATH",
        help="Path to forensics JSON for baseline D3 comparison in progress output",
    )
    parser.add_argument(
        "--enable-extended-context",
        action="store_true",
        help="Enable test-adjacency + blame context expansion for all commits in this run",
    )
    parser.add_argument(
        "--enable-historical-defect-context",
        action="store_true",
        help="Enable ApacheJIT KNN historical defect-category priors in hypothesis prompt",
    )
    return parser


def main() -> None:
    _load_dotenv()
    args = _build_arg_parser().parse_args()

    runner = EvalRunner(args)
    runner.setup()
    runner.select_commits()
    runner.run_investigations()
    runner.run_harness()
    runner.write_reports()


if __name__ == "__main__":
    main()
