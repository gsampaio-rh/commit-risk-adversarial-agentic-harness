"""Six-dimension evaluation harness.

Compares agent investigation output to ApacheJIT ground truth across:
  D1: Prediction (risk level vs buggy label)
  D2: Localization (agent files vs fix-commit files, Jaccard)
  D3: Diagnosis (agent reasoning vs JIRA description, LLM-as-judge rubric 0-4)
  D4: Severity (agent risk vs JIRA priority, normalized)
  D5: Recommendations (agent recs vs actual fix pattern, LLM-as-judge rubric 0-3)
  D6: Evidence grounding (automated, no LLM cost — agent claims vs actual diff/files)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from commit_investigator.eval_judge import ReasoningJudge
from commit_investigator.ground_truth import GroundTruthGraph
from commit_investigator.jira_client import JiraClient, JiraClientError, JiraIssue
from commit_investigator.llm import LLMProvider
from commit_investigator.report import CommitInvestigationReport, RiskLevel
from commit_investigator.router import Route

logger = logging.getLogger(__name__)


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
    agent_risk_level: str = ""
    agent_confidence: float = 0.0
    agent_reasoning: str = ""
    localization_count: int = 0
    has_fix_chain: bool = False


@dataclass
class EvalReport:
    """Aggregate evaluation report across all sampled commits."""

    budget_tier: str
    total_sampled: int
    results: list[CommitEvalResult]
    dimension_averages: dict[str, float]
    router_baseline: dict[str, float]
    cost_actual: float
    subset_averages: dict[str, float] = field(default_factory=dict)
    baselines: dict[str, float] = field(default_factory=dict)
    stratum_averages: dict[str, dict[str, float]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


JIRA_PRIORITY_TO_RISK = {
    "Blocker": RiskLevel.CRITICAL,
    "Critical": RiskLevel.CRITICAL,
    "Major": RiskLevel.HIGH,
    "Minor": RiskLevel.MEDIUM,
    "Trivial": RiskLevel.LOW,
}


class EvalHarness:
    """Six-dimension evaluation harness with stratified sampling and budget governance."""

    def __init__(
        self,
        ground_truth: GroundTruthGraph,
        jira_client: JiraClient | None = None,
        git_providers: dict[str, Any] | None = None,
        budget_tier: str = "$50",
        max_evals: int = 300,
        judge_provider: LLMProvider | None = None,
    ) -> None:
        self._gt = ground_truth
        self._jira = jira_client
        self._git_providers = git_providers or {}
        self._budget_tier = budget_tier
        self._max_evals = max_evals
        self._judge = ReasoningJudge(judge_provider) if judge_provider else None

    def evaluate_report(
        self,
        report: CommitInvestigationReport,
        buggy_label: bool,
        route: Route = Route.INVESTIGATE,
    ) -> CommitEvalResult:
        """Evaluate a single investigation report against ground truth."""
        chain = self._gt.get_chain(report.commit_id)
        fix_files = self._get_fix_files(report.project, chain.fix_hashes)
        actual_files = self._get_commit_files(report.project, report.commit_id)
        actual_diff = self._get_commit_diff(report.project, report.commit_id)

        result = CommitEvalResult(
            commit_id=report.commit_id,
            project=report.project,
            buggy_label=buggy_label,
            route=route,
            agent_risk_level=report.risk_assessment.level.value,
            agent_confidence=report.risk_assessment.confidence,
            agent_reasoning=report.reasoning_summary,
            localization_count=len(report.localization),
            has_fix_chain=bool(chain.fix_hashes),
        )

        result.scores["D1"] = self._score_d1_prediction(report, buggy_label)
        result.scores["D2"] = self._score_d2_localization(report)

        # D6: automated evidence grounding (always, no LLM cost)
        d6_result = ReasoningJudge.score_d6_evidence_grounding(
            report, actual_diff=actual_diff, actual_files=actual_files,
        )
        result.scores["D6"] = DimensionScore(
            dimension="D6_evidence_grounding",
            score=d6_result.normalized,
            details=d6_result.justification,
            automated=True,
        )

        if self._jira and buggy_label:
            jira_issue = self._fetch_jira_for_commit(report.commit_id)
            if jira_issue:
                result.scores["D3"] = self._score_d3_diagnosis(report, jira_issue, fix_files)
                result.scores["D4"] = self._score_d4_severity(report, jira_issue)
                result.scores["D5"] = self._score_d5_recommendations(report, jira_issue, fix_files)
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
            subset_averages=self._compute_subset_averages(results),
            baselines=self._compute_baselines(results),
            stratum_averages=self._compute_stratum_averages(results),
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
        """D2: Do agent's localized files match the fix commit's files? (Jaccard)"""
        chain = self._gt.get_chain(report.commit_id)
        if not chain.fix_hashes:
            return DimensionScore(
                dimension="D2_localization", score=0.0,
                details="No fix commits in ground truth for comparison",
            )

        agent_files = {_basename(loc.file) for loc in report.localization}
        if not agent_files:
            return DimensionScore(
                dimension="D2_localization", score=0.0,
                details="Agent provided no localization claims",
            )

        fix_files = self._get_fix_files(report.project, chain.fix_hashes)
        if not fix_files:
            has_localization = len(report.localization) > 0
            return DimensionScore(
                dimension="D2_localization",
                score=0.5 if has_localization else 0.0,
                details=f"Agent localized {len(report.localization)} files (fix files unavailable from git)",
            )

        fix_basenames = {_basename(f) for f in fix_files}
        intersection = agent_files & fix_basenames
        union = agent_files | fix_basenames
        jaccard = len(intersection) / len(union) if union else 0.0

        return DimensionScore(
            dimension="D2_localization",
            score=jaccard,
            details=(
                f"Jaccard={jaccard:.3f} "
                f"(agent={sorted(agent_files)}, fix={sorted(fix_basenames)}, "
                f"overlap={sorted(intersection)})"
            ),
        )

    def _get_fix_files(self, project: str, fix_hashes: list[str]) -> set[str]:
        """Get files touched by fix commits via git provider."""
        project_key = project.strip().lower().split("/")[-1]
        provider = self._git_providers.get(project_key)
        if provider is None:
            return set()

        files: set[str] = set()
        for fix_hash in fix_hashes:
            touched = provider.get_touched_files(fix_hash)
            if touched:
                files.update(touched)
        return files

    def _score_d3_diagnosis(
        self,
        report: CommitInvestigationReport,
        jira_issue: JiraIssue,
        fix_files: set[str] | None = None,
    ) -> DimensionScore:
        """D3: Does agent reasoning align with JIRA issue description? (LLM-as-judge, rubric 0-4)"""
        if not jira_issue.description:
            return DimensionScore(
                dimension="D3_diagnosis", score=0.0,
                details="JIRA issue has no description for comparison",
                automated=False,
            )

        if self._judge:
            judge_result = self._judge.score_d3_root_cause(report, jira_issue, fix_files)
            return DimensionScore(
                dimension="D3_diagnosis",
                score=judge_result.normalized,
                details=f"[judge {judge_result.score}/{judge_result.max_score}] {judge_result.justification}",
                automated=False,
            )

        return self._score_d3_word_overlap_fallback(report, jira_issue)

    def _score_d3_word_overlap_fallback(
        self, report: CommitInvestigationReport, jira_issue: JiraIssue
    ) -> DimensionScore:
        """Fallback D3 using word overlap when no judge provider is available."""
        agent_reasoning = report.reasoning_summary.lower()
        jira_desc = (jira_issue.description or "").lower()

        overlap_words = set(agent_reasoning.split()) & set(jira_desc.split())
        common_words = {"the", "a", "is", "in", "to", "of", "and", "for", "it", "on", "that", "this"}
        meaningful_overlap = overlap_words - common_words

        jira_words = set(jira_desc.split()) - common_words
        if not jira_words:
            return DimensionScore(
                dimension="D3_diagnosis", score=0.0,
                details="JIRA description too short for meaningful comparison (word-overlap fallback)",
                automated=False,
            )

        score = min(1.0, len(meaningful_overlap) / max(1, len(jira_words) * 0.3))
        return DimensionScore(
            dimension="D3_diagnosis",
            score=score,
            details=f"Word overlap fallback: {len(meaningful_overlap)} meaningful terms ({score:.2f})",
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
        self,
        report: CommitInvestigationReport,
        jira_issue: JiraIssue,
        fix_files: set[str] | None = None,
    ) -> DimensionScore:
        """D5: Are agent recommendations relevant to the actual fix? (LLM-as-judge, rubric 0-3)"""
        if not report.recommendations:
            return DimensionScore(
                dimension="D5_recommendations", score=0.0,
                details="Agent provided no recommendations",
                automated=False,
            )

        if self._judge:
            judge_result = self._judge.score_d5_recommendations(report, jira_issue, fix_files)
            return DimensionScore(
                dimension="D5_recommendations",
                score=judge_result.normalized,
                details=f"[judge {judge_result.score}/{judge_result.max_score}] {judge_result.justification}",
                automated=False,
            )

        score = 0.3
        return DimensionScore(
            dimension="D5_recommendations",
            score=score,
            details=f"Agent provided {len(report.recommendations)} recommendations (no judge — stub score)",
            automated=False,
        )

    def _get_commit_files(self, project: str, commit_id: str) -> set[str] | None:
        """Get files touched by the commit being evaluated."""
        project_key = project.strip().lower().split("/")[-1]
        provider = self._git_providers.get(project_key)
        if provider is None:
            return None
        try:
            touched = provider.get_touched_files(commit_id)
            return set(touched) if touched else None
        except Exception:
            return None

    def _get_commit_diff(self, project: str, commit_id: str) -> str | None:
        """Get the diff for the commit being evaluated."""
        project_key = project.strip().lower().split("/")[-1]
        provider = self._git_providers.get(project_key)
        if provider is None:
            return None
        try:
            return provider.get_diff(commit_id)
        except Exception:
            return None

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

    def _compute_subset_averages(self, results: list[CommitEvalResult]) -> dict[str, float]:
        """Averages restricted to commits where a dimension is scorable."""
        d2_scorable = [
            r.scores["D2"].score
            for r in results
            if r.has_fix_chain and "D2" in r.scores
        ]
        d3_buggy = [r.scores["D3"].score for r in results if r.buggy_label and "D3" in r.scores]
        d5_buggy = [r.scores["D5"].score for r in results if r.buggy_label and "D5" in r.scores]
        d6_all = [r.scores["D6"].score for r in results if "D6" in r.scores]

        subset: dict[str, float] = {}
        if d2_scorable:
            subset["D2_fix_chain_only"] = sum(d2_scorable) / len(d2_scorable)
        if d3_buggy:
            subset["D3_buggy_only"] = sum(d3_buggy) / len(d3_buggy)
        if d5_buggy:
            subset["D5_buggy_only"] = sum(d5_buggy) / len(d5_buggy)
        if d6_all:
            subset["D6_all"] = sum(d6_all) / len(d6_all)
        return subset

    def _compute_baselines(self, results: list[CommitEvalResult]) -> dict[str, float]:
        """Reference baselines so headline D1 is not misread on imbalanced samples."""
        if not results:
            return {}

        clean_count = sum(1 for r in results if not r.buggy_label)
        total = len(results)
        always_predict_clean = clean_count / total

        correct_if_always_clean = sum(
            1 for r in results if not r.buggy_label
        )
        return {
            "D1_always_predict_clean": always_predict_clean,
            "D1_always_predict_clean_accuracy": correct_if_always_clean / total,
        }

    def _compute_stratum_averages(
        self, results: list[CommitEvalResult]
    ) -> dict[str, dict[str, float]]:
        """Per-stratum dimension averages (buggy vs clean)."""
        strata: dict[str, list[CommitEvalResult]] = {"buggy": [], "clean": []}
        for r in results:
            key = "buggy" if r.buggy_label else "clean"
            strata[key].append(r)

        out: dict[str, dict[str, float]] = {}
        for name, group in strata.items():
            if not group:
                continue
            dim_sums: dict[str, float] = {}
            dim_counts: dict[str, int] = {}
            for r in group:
                for dim_id, score in r.scores.items():
                    dim_sums[dim_id] = dim_sums.get(dim_id, 0.0) + score.score
                    dim_counts[dim_id] = dim_counts.get(dim_id, 0) + 1
            out[name] = {
                dim: dim_sums[dim] / dim_counts[dim]
                for dim in sorted(dim_sums.keys())
                if dim_counts[dim] > 0
            }
            out[name]["_count"] = float(len(group))
        return out


def _serialize_eval_result(r: CommitEvalResult) -> dict:
    """Serialize a single CommitEvalResult to a dict."""
    return {
        "commit_id": r.commit_id,
        "project": r.project,
        "buggy": r.buggy_label,
        "route": r.route.value,
        "has_fix_chain": r.has_fix_chain,
        "agent": {
            "risk_level": r.agent_risk_level,
            "confidence": r.agent_confidence,
            "reasoning_summary": r.agent_reasoning,
            "localization_count": r.localization_count,
        },
        "scores": {k: {"score": v.score, "details": v.details} for k, v in r.scores.items()},
        "errors": r.errors,
    }


def _save_per_commit_evals(results: list[CommitEvalResult], output_dir: Path) -> None:
    """Save each commit's eval result as an individual JSON in evaluations/."""
    eval_dir = output_dir / "evaluations"
    eval_dir.mkdir(parents=True, exist_ok=True)

    for r in results:
        filename = f"{r.commit_id[:12]}_{r.project}.json"
        data = _serialize_eval_result(r)
        (eval_dir / filename).write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )


def save_eval_report(report: EvalReport, output_dir: str | Path) -> None:
    """Persist evaluation report as JSON, markdown, and per-commit eval files."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    _save_per_commit_evals(report.results, output_dir)

    json_data = {
        "budget_tier": report.budget_tier,
        "total_sampled": report.total_sampled,
        "dimension_averages": report.dimension_averages,
        "subset_averages": report.subset_averages,
        "baselines": report.baselines,
        "stratum_averages": report.stratum_averages,
        "router_baseline": report.router_baseline,
        "cost_actual": report.cost_actual,
        "metadata": report.metadata,
        "results": [_serialize_eval_result(r) for r in report.results],
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

    if report.subset_averages:
        md_lines.extend(["", "## Subset Averages (scorable commits only)", ""])
        md_lines.extend(["| Subset | Score |", "|--------|-------|"])
        for dim, avg in sorted(report.subset_averages.items()):
            md_lines.append(f"| {dim} | {avg:.3f} |")

    if report.baselines:
        md_lines.extend(["", "## Reference Baselines", ""])
        md_lines.extend(["| Baseline | Score |", "|----------|-------|"])
        for name, score in sorted(report.baselines.items()):
            md_lines.append(f"| {name} | {score:.3f} |")

    if report.stratum_averages:
        md_lines.extend(["", "## Stratum Averages", ""])
        for stratum, dims in report.stratum_averages.items():
            count = int(dims.get("_count", 0))
            md_lines.append(f"### {stratum} (n={count})")
            md_lines.append("")
            md_lines.extend(["| Dimension | Score |", "|-----------|-------|"])
            for dim, avg in sorted(dims.items()):
                if dim == "_count":
                    continue
                md_lines.append(f"| {dim} | {avg:.3f} |")
            md_lines.append("")

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


def _basename(path: str) -> str:
    """Extract filename from a path for file-level Jaccard comparison."""
    return Path(path).name
