"""V3 evaluation runner — n=20 smoke test and full eval.

Orchestrates the attribution agent and deterministic baselines against
ground truth from the ApacheJIT replication package:

  1. Load ground truth → select eval cases (fix commits with JIRA tickets)
  2. For each case: build ProblemStatement, run agent + baselines
  3. Score with Hit@k, MRR, evidence grounding
  4. Compare agent vs baselines, output report

Run:
    python -m commit_investigator.runners.run_eval \
        --zip data/apachejit.zip \
        --repos-dir data/repos \
        --n 20 \
        --output-dir results/v3-eval
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SRC_ROOT = Path(__file__).resolve().parents[2]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from commit_investigator.context.git_context import GitContextProvider
from commit_investigator.context.problem_extractor import ProblemExtractor, ProblemStatement
from commit_investigator.infra.ground_truth import GroundTruthGraph
from commit_investigator.infra.jira_client import JiraClient, JiraClientError
from commit_investigator.infra.llm import LLMProvider, get_provider
from commit_investigator.pipeline.orchestrator import AgentOrchestrator, BugAttributionReport
from commit_investigator.runners.baselines import (
    BaselineResult,
    file_history_recency,
    git_blame_naive,
)
from commit_investigator.runners.eval_metrics import (
    AggregateEvalReport,
    AttributionEvalResult,
    aggregate_results,
    evaluate_attribution,
)

logger = logging.getLogger(__name__)


@dataclass
class EvalCase:
    """A single evaluation case: a bug-fix pair with JIRA context."""

    bug_hash: str
    fix_hash: str
    project: str
    issue_key: str
    problem: ProblemStatement | None = None


@dataclass
class ComparisonReport:
    """Side-by-side comparison of agent vs baselines."""

    agent: AggregateEvalReport
    baselines: dict[str, AggregateEvalReport]
    eval_cases: list[EvalCase] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent.to_dict(),
            "baselines": {k: v.to_dict() for k, v in self.baselines.items()},
            "case_count": len(self.eval_cases),
        }


def select_eval_cases(
    gt: GroundTruthGraph,
    jira: JiraClient,
    n: int = 20,
    seed: int = 42,
    require_jira_description: bool = True,
) -> list[EvalCase]:
    """Select n eval cases: bug commits with linked fix+JIRA.

    Filters for cases where the JIRA ticket has a non-empty description
    (eliminates ~8% noise per the plan).
    """
    rng = random.Random(seed)

    candidates: list[tuple[str, str, str, str]] = []
    for project in gt.projects:
        for bug_hash in _iter_project_bugs(gt, project):
            chain = gt.get_chain(bug_hash)
            if not chain.fix_hashes or not chain.issue_keys:
                continue
            fix_hash = min(chain.fix_hashes)
            issue_key = chain.issue_keys[0]
            candidates.append((bug_hash, fix_hash, project, issue_key))

    rng.shuffle(candidates)

    extractor = ProblemExtractor()
    cases: list[EvalCase] = []
    for bug_hash, fix_hash, project, issue_key in candidates:
        if len(cases) >= n:
            break

        if require_jira_description:
            try:
                jira_issue = jira.get_issue(issue_key)
                if not jira_issue.description or not jira_issue.description.strip():
                    continue
                problem = extractor.from_jira_issue(jira_issue, project=project)
            except JiraClientError:
                continue
        else:
            problem = None

        cases.append(EvalCase(
            bug_hash=bug_hash,
            fix_hash=fix_hash,
            project=project,
            issue_key=issue_key,
            problem=problem,
        ))

    return cases


def _iter_project_bugs(gt: GroundTruthGraph, project: str) -> list[str]:
    """Get all bug hashes for a project from the ground truth graph."""
    bugs = []
    for bug_hash in gt._bug_to_fixes:
        chain = gt.get_chain(bug_hash)
        for ik in chain.issue_keys:
            if ik.startswith(project + "-"):
                bugs.append(bug_hash)
                break
    return bugs


def run_single_case(
    case: EvalCase,
    agent: AgentOrchestrator,
    repos_dir: Path,
) -> tuple[
    AttributionEvalResult | None,
    dict[str, AttributionEvalResult | None],
]:
    """Run agent + baselines on a single eval case.

    Returns (agent_result, {baseline_name: baseline_result}).
    """
    repo_path = repos_dir / case.project.lower()
    if not repo_path.exists():
        logger.warning("Repo not found for %s at %s", case.project, repo_path)
        return None, {}

    try:
        git_provider = GitContextProvider(repo_path, temporal_bound=f"{case.fix_hash}~1")
    except Exception as e:
        logger.error("Cannot create git provider for %s: %s", case.project, e)
        return None, {}

    if case.problem is None:
        logger.warning("No problem statement for case %s", case.issue_key)
        return None, {}

    agent_report = agent.investigate(case.problem, git_provider)
    agent_eval = evaluate_attribution(
        report=agent_report,
        bug_hash=case.bug_hash,
        project=case.project,
        issue_key=case.issue_key,
        git_provider=git_provider,
    )

    baseline_evals: dict[str, AttributionEvalResult | None] = {}

    blame_result = git_blame_naive(case.problem, git_provider)
    baseline_evals["git-blame-naive"] = evaluate_attribution(
        report=blame_result.report,
        bug_hash=case.bug_hash,
        project=case.project,
        issue_key=case.issue_key,
        git_provider=git_provider,
    )

    recency_result = file_history_recency(case.problem, git_provider)
    baseline_evals["file-history-recency"] = evaluate_attribution(
        report=recency_result.report,
        bug_hash=case.bug_hash,
        project=case.project,
        issue_key=case.issue_key,
        git_provider=git_provider,
    )

    return agent_eval, baseline_evals


def run_eval(
    gt: GroundTruthGraph,
    jira: JiraClient,
    repos_dir: Path,
    llm_provider: LLMProvider | None = None,
    n: int = 20,
    seed: int = 42,
    output_dir: Path | None = None,
) -> ComparisonReport:
    """Run the full evaluation: agent + baselines on n cases."""
    logger.info("Selecting %d eval cases...", n)
    cases = select_eval_cases(gt, jira, n=n, seed=seed)
    logger.info("Selected %d cases across %d projects",
                len(cases), len({c.project for c in cases}))

    agent = AgentOrchestrator(llm_provider=llm_provider or get_provider())

    agent_results: list[AttributionEvalResult] = []
    baseline_results: dict[str, list[AttributionEvalResult]] = {
        "git-blame-naive": [],
        "file-history-recency": [],
    }

    for i, case in enumerate(cases):
        logger.info("[%d/%d] %s %s (bug=%s...)",
                    i + 1, len(cases), case.project, case.issue_key, case.bug_hash[:12])

        agent_eval, bl_evals = run_single_case(case, agent, repos_dir)

        if agent_eval is not None:
            agent_results.append(agent_eval)
        for bl_name, bl_eval in bl_evals.items():
            if bl_eval is not None:
                baseline_results[bl_name].append(bl_eval)

    agent_agg = aggregate_results(agent_results)
    baseline_aggs = {
        name: aggregate_results(results)
        for name, results in baseline_results.items()
    }

    report = ComparisonReport(
        agent=agent_agg,
        baselines=baseline_aggs,
        eval_cases=cases,
    )

    if output_dir:
        _save_comparison_report(report, output_dir)

    _print_comparison(report)
    return report


def _save_comparison_report(report: ComparisonReport, output_dir: Path) -> None:
    """Save eval report to JSON and markdown."""
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "comparison.json").write_text(
        json.dumps(report.to_dict(), indent=2), encoding="utf-8"
    )

    md = _render_markdown_report(report)
    (output_dir / "comparison.md").write_text(md, encoding="utf-8")


def _render_markdown_report(report: ComparisonReport) -> str:
    """Render comparison report as markdown."""
    lines = [
        "# V3 Bug Attribution — Evaluation Report",
        "",
        f"**Cases evaluated:** {report.agent.total}",
        f"**Total cost:** ${report.agent.total_cost_usd:.4f}",
        "",
        "## Headline Metrics",
        "",
        "| Method | Hit@1 | Hit@3 | Hit@5 | MRR | Retrieval Recall | Evid. Grounding |",
        "|--------|-------|-------|-------|-----|-----------------|-----------------|",
    ]

    lines.append(
        f"| **Agent** | {report.agent.hit_at_1:.3f} | "
        f"{report.agent.hit_at_3:.3f} | "
        f"{report.agent.hit_at_5:.3f} | "
        f"{report.agent.mrr:.3f} | "
        f"{report.agent.retrieval_recall:.3f} | "
        f"{report.agent.evidence_grounding_rate:.3f} |"
    )

    for name, agg in report.baselines.items():
        lines.append(
            f"| {name} | {agg.hit_at_1:.3f} | "
            f"{agg.hit_at_3:.3f} | "
            f"{agg.hit_at_5:.3f} | "
            f"{agg.mrr:.3f} | "
            f"{agg.retrieval_recall:.3f} | "
            f"{agg.evidence_grounding_rate:.3f} |"
        )

    lines.extend([
        "",
        "## Cost",
        "",
        f"- Agent avg tokens/case: {report.agent.avg_tokens:,.0f}",
        f"- Agent avg tool calls/case: {report.agent.avg_tool_calls:.1f}",
        f"- Agent avg latency/case: {report.agent.avg_elapsed_ms:,.0f}ms",
        f"- Agent total cost: ${report.agent.total_cost_usd:.4f}",
        f"- Baselines total cost: $0.0000 (zero-LLM)",
    ])

    return "\n".join(lines)


def _print_comparison(report: ComparisonReport) -> None:
    """Print comparison table to stdout."""
    print("\n" + "=" * 70)
    print("V3 EVALUATION RESULTS")
    print("=" * 70)
    print(f"\nCases: {report.agent.total}")
    print(f"\n{'Method':<25} {'Hit@1':>6} {'Hit@3':>6} {'Hit@5':>6} {'MRR':>6}")
    print("-" * 55)
    print(
        f"{'Agent':<25} "
        f"{report.agent.hit_at_1:>6.3f} "
        f"{report.agent.hit_at_3:>6.3f} "
        f"{report.agent.hit_at_5:>6.3f} "
        f"{report.agent.mrr:>6.3f}"
    )
    for name, agg in report.baselines.items():
        print(
            f"{name:<25} "
            f"{agg.hit_at_1:>6.3f} "
            f"{agg.hit_at_3:>6.3f} "
            f"{agg.hit_at_5:>6.3f} "
            f"{agg.mrr:>6.3f}"
        )
    print("-" * 55)
    print(f"\nAgent cost: ${report.agent.total_cost_usd:.4f}")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="V3 Bug Attribution Evaluation Runner")
    parser.add_argument("--zip", required=True, help="Path to ApacheJIT replication zip")
    parser.add_argument("--repos-dir", required=True, help="Directory containing project git repos")
    parser.add_argument("--n", type=int, default=20, help="Number of eval cases")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for case selection")
    parser.add_argument("--output-dir", default="results/v3-eval", help="Output directory")
    parser.add_argument("--jira-cache", default="data/jira_cache", help="JIRA cache directory")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    zip_path = Path(args.zip)
    if not zip_path.exists():
        logger.error("Replication zip not found: %s", zip_path)
        sys.exit(1)

    repos_dir = Path(args.repos_dir)
    if not repos_dir.exists():
        logger.error("Repos directory not found: %s", repos_dir)
        sys.exit(1)

    gt = GroundTruthGraph.from_replication_zip(zip_path)
    logger.info("Ground truth loaded: %d bugs, %d fixes, %d issue links",
                gt.total_bug_commits, gt.total_fix_commits, gt.total_issue_links)

    jira = JiraClient(cache_dir=args.jira_cache)
    llm = get_provider()

    run_eval(
        gt=gt,
        jira=jira,
        repos_dir=repos_dir,
        llm_provider=llm,
        n=args.n,
        seed=args.seed,
        output_dir=Path(args.output_dir),
    )


if __name__ == "__main__":
    main()
