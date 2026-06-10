"""Five-dimension evaluation harness.

Compares agent investigation output to ApacheJIT ground truth across:
  D1: Prediction (risk level vs buggy label)
  D2: Localization (agent files vs fix-commit files, Jaccard)
  D3: Diagnosis (agent reasoning vs JIRA description, LLM-as-judge)
  D4: Severity (agent risk vs JIRA priority, normalized)
  D5: Recommendations (agent recs vs actual fix pattern, LLM-as-judge)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from commit_investigator.ground_truth import GroundTruthGraph
from commit_investigator.jira_client import JiraClient, JiraClientError, JiraIssue
from commit_investigator.report import CommitInvestigationReport, RiskLevel
from commit_investigator.router import Route, RoutingDecision


@dataclass
class DimensionScore:
    """Score for a single evaluation dimension on a single commit."""

    dimension: str
    score: float  # 0.0 - 1.0
    details: str
    automated: bool = True


@dataclass
class CommitEvalResult:
    """Full evaluation result for a single commit."""

    commit_id: str
    project: str
    buggy_label: bool
    route: Route
    scores: dict[str, DimensionScore] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    """Aggregate evaluation report across all sampled commits."""

    budget_tier: str
    total_sampled: int
    results: list[CommitEvalResult]
    dimension_averages: dict[str, float]
    router_baseline: dict[str, float]
    cost_actual: float
    metadata: dict[str, Any] = field(default_factory=dict)


JIRA_PRIORITY_TO_RISK = {
    "Blocker": RiskLevel.CRITICAL,
    "Critical": RiskLevel.CRITICAL,
    "Major": RiskLevel.HIGH,
    "Minor": RiskLevel.MEDIUM,
    "Trivial": RiskLevel.LOW,
}


class EvalHarness:
    """Five-dimension evaluation harness with stratified sampling and budget governance."""

    def __init__(
        self,
        ground_truth: GroundTruthGraph,
        jira_client: JiraClient | None = None,
        budget_tier: str = "$50",
        max_evals: int = 300,
    ) -> None:
        self._gt = ground_truth
        self._jira = jira_client
        self._budget_tier = budget_tier
        self._max_evals = max_evals

    def evaluate_report(
        self,
        report: CommitInvestigationReport,
        buggy_label: bool,
        route: Route = Route.INVESTIGATE,
    ) -> CommitEvalResult:
        """Evaluate a single investigation report against ground truth."""
        result = CommitEvalResult(
            commit_id=report.commit_id,
            project=report.project,
            buggy_label=buggy_label,
            route=route,
        )

        result.scores["D1"] = self._score_d1_prediction(report, buggy_label)
        result.scores["D2"] = self._score_d2_localization(report)

        if self._jira:
            jira_issue = self._fetch_jira_for_commit(report.commit_id)
            if jira_issue:
                result.scores["D3"] = self._score_d3_diagnosis(report, jira_issue)
                result.scores["D4"] = self._score_d4_severity(report, jira_issue)
                result.scores["D5"] = self._score_d5_recommendations(report, jira_issue)
            else:
                result.errors.append("No JIRA issue found for D3-D5 scoring")

        return result

    def evaluate_batch(
        self,
        reports: list[tuple[CommitInvestigationReport, bool, Route]],
    ) -> EvalReport:
        """Evaluate a batch of reports and produce aggregate metrics."""
        results = []
        for report, buggy_label, route in reports[:self._max_evals]:
            eval_result = self.evaluate_report(report, buggy_label, route)
            results.append(eval_result)

        dim_averages = self._compute_averages(results)
        router_baseline = self._compute_router_baseline(results)

        return EvalReport(
            budget_tier=self._budget_tier,
            total_sampled=len(results),
            results=results,
            dimension_averages=dim_averages,
            router_baseline=router_baseline,
            cost_actual=0.0,  # filled by caller with actual LLM cost
        )

    def _score_d1_prediction(
        self, report: CommitInvestigationReport, buggy_label: bool
    ) -> DimensionScore:
        """D1: Does the agent's risk level align with the buggy label?"""
        agent_says_risky = report.risk_assessment.level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        correct = agent_says_risky == buggy_label

        score = 1.0 if correct else 0.0
        details = (
            f"Agent={report.risk_assessment.level.value}, "
            f"Label={'buggy' if buggy_label else 'clean'}, "
            f"{'correct' if correct else 'incorrect'}"
        )
        return DimensionScore(dimension="D1_prediction", score=score, details=details)

    def _score_d2_localization(self, report: CommitInvestigationReport) -> DimensionScore:
        """D2: Do agent's localized files match the fix commit's files?"""
        chain = self._gt.get_chain(report.commit_id)
        if not chain.fix_hashes:
            return DimensionScore(
                dimension="D2_localization", score=0.0,
                details="No fix commits in ground truth for comparison"
            )

        agent_files = set(loc.file for loc in report.localization)
        if not agent_files:
            agent_files = set(report.evidence[0].source for _ in [1] if report.evidence)

        # TODO: when git repos are available, get actual fix diff files
        # For now, score based on whether agent provided localization at all
        has_localization = len(report.localization) > 0
        score = 0.5 if has_localization else 0.0

        return DimensionScore(
            dimension="D2_localization",
            score=score,
            details=f"Agent localized {len(report.localization)} files (fix files comparison requires git clone)",
        )

    def _score_d3_diagnosis(
        self, report: CommitInvestigationReport, jira_issue: JiraIssue
    ) -> DimensionScore:
        """D3: Does agent reasoning align with JIRA issue description?"""
        if not jira_issue.description:
            return DimensionScore(
                dimension="D3_diagnosis", score=0.0,
                details="JIRA issue has no description for comparison",
                automated=False,
            )

        agent_reasoning = report.reasoning_summary.lower()
        jira_desc = jira_issue.description.lower()

        overlap_words = set(agent_reasoning.split()) & set(jira_desc.split())
        common_words = {"the", "a", "is", "in", "to", "of", "and", "for", "it", "on", "that", "this"}
        meaningful_overlap = overlap_words - common_words

        jira_words = set(jira_desc.split()) - common_words
        if not jira_words:
            return DimensionScore(
                dimension="D3_diagnosis", score=0.0,
                details="JIRA description too short for meaningful comparison",
                automated=False,
            )

        score = min(1.0, len(meaningful_overlap) / max(1, len(jira_words) * 0.3))

        return DimensionScore(
            dimension="D3_diagnosis",
            score=score,
            details=f"Word overlap: {len(meaningful_overlap)} meaningful terms ({score:.2f})",
            automated=False,
        )

    def _score_d4_severity(
        self, report: CommitInvestigationReport, jira_issue: JiraIssue
    ) -> DimensionScore:
        """D4: Does agent risk level match JIRA priority?"""
        if not jira_issue.priority:
            return DimensionScore(
                dimension="D4_severity", score=0.0,
                details="JIRA issue has no priority",
                automated=False,
            )

        expected_risk = JIRA_PRIORITY_TO_RISK.get(jira_issue.priority)
        if expected_risk is None:
            return DimensionScore(
                dimension="D4_severity", score=0.0,
                details=f"Unknown JIRA priority: {jira_issue.priority}",
                automated=False,
            )

        risk_order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        agent_idx = risk_order.index(report.risk_assessment.level)
        expected_idx = risk_order.index(expected_risk)

        distance = abs(agent_idx - expected_idx)
        score = max(0.0, 1.0 - (distance * 0.33))

        return DimensionScore(
            dimension="D4_severity",
            score=score,
            details=f"Agent={report.risk_assessment.level.value}, JIRA={jira_issue.priority} (mapped to {expected_risk.value}), distance={distance}",
            automated=False,
        )

    def _score_d5_recommendations(
        self, report: CommitInvestigationReport, jira_issue: JiraIssue
    ) -> DimensionScore:
        """D5: Are agent recommendations relevant to the actual fix?"""
        if not report.recommendations:
            return DimensionScore(
                dimension="D5_recommendations", score=0.0,
                details="Agent provided no recommendations",
                automated=False,
            )

        has_recs = len(report.recommendations) > 0
        score = 0.3 if has_recs else 0.0

        return DimensionScore(
            dimension="D5_recommendations",
            score=score,
            details=f"Agent provided {len(report.recommendations)} recommendations (full scoring requires LLM-as-judge)",
            automated=False,
        )

    def _fetch_jira_for_commit(self, commit_id: str) -> JiraIssue | None:
        """Fetch JIRA issue for a commit via ground truth graph."""
        if not self._jira:
            return None

        issue_keys = self._gt.get_issue_keys(commit_id)
        if not issue_keys:
            chain = self._gt.get_chain(commit_id)
            for fh in chain.fix_hashes:
                issue_keys.extend(self._gt.get_issue_keys(fh))

        if not issue_keys:
            return None

        try:
            return self._jira.get_issue(issue_keys[0])
        except JiraClientError:
            return None

    def _compute_averages(self, results: list[CommitEvalResult]) -> dict[str, float]:
        """Compute average score per dimension across all results."""
        dim_sums: dict[str, float] = {}
        dim_counts: dict[str, int] = {}

        for r in results:
            for dim_id, score in r.scores.items():
                dim_sums[dim_id] = dim_sums.get(dim_id, 0.0) + score.score
                dim_counts[dim_id] = dim_counts.get(dim_id, 0) + 1

        return {
            dim: dim_sums[dim] / dim_counts[dim]
            for dim in sorted(dim_sums.keys())
            if dim_counts[dim] > 0
        }

    def _compute_router_baseline(self, results: list[CommitEvalResult]) -> dict[str, float]:
        """Compute D1 baseline using router-only (no investigation)."""
        correct = 0
        total = 0

        for r in results:
            router_says_risky = r.route in (Route.INVESTIGATE, Route.HIGH)
            if router_says_risky == r.buggy_label:
                correct += 1
            total += 1

        return {"D1_router_only": correct / total if total > 0 else 0.0}


def save_eval_report(report: EvalReport, output_dir: str | Path) -> None:
    """Persist evaluation report as JSON and markdown."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_data = {
        "budget_tier": report.budget_tier,
        "total_sampled": report.total_sampled,
        "dimension_averages": report.dimension_averages,
        "router_baseline": report.router_baseline,
        "cost_actual": report.cost_actual,
        "metadata": report.metadata,
        "results": [
            {
                "commit_id": r.commit_id,
                "project": r.project,
                "buggy": r.buggy_label,
                "route": r.route.value,
                "scores": {k: {"score": v.score, "details": v.details} for k, v in r.scores.items()},
                "errors": r.errors,
            }
            for r in report.results
        ],
    }

    (output_dir / "eval-report.json").write_text(
        json.dumps(json_data, indent=2), encoding="utf-8"
    )

    md_lines = [
        "# Evaluation Report",
        "",
        f"**Budget tier:** {report.budget_tier}",
        f"**Commits evaluated:** {report.total_sampled}",
        f"**Cost actual:** ${report.cost_actual:.4f}",
        "",
        "## Dimension Averages",
        "",
        "| Dimension | Score |",
        "|-----------|-------|",
    ]
    for dim, avg in sorted(report.dimension_averages.items()):
        md_lines.append(f"| {dim} | {avg:.3f} |")

    md_lines.extend([
        "",
        "## Router-Only Baseline",
        "",
        "| Metric | Score |",
        "|--------|-------|",
    ])
    for metric, score in report.router_baseline.items():
        md_lines.append(f"| {metric} | {score:.3f} |")

    (output_dir / "eval-report.md").write_text("\n".join(md_lines), encoding="utf-8")
