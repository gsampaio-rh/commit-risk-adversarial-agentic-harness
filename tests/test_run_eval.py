"""Tests for V3 eval runner orchestration logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.eval.ground_truth import CommitChain, GroundTruthGraph
from commit_investigator.extraction.jira_client import JiraClientError, JiraIssue
from commit_investigator.agent.orchestrator import BugAttributionReport, SuspectCommit
from commit_investigator.eval.run_eval import (
    ComparisonReport,
    EvalCase,
    _print_comparison,
    _render_markdown_report,
    run_single_case,
    select_eval_cases,
)


def _make_problem(title: str = "Test bug", desc: str = "NPE in Foo.java") -> ProblemStatement:
    return ProblemStatement(title=title, description=desc, project="TEST")


def _make_report(
    suspects: list[SuspectCommit] | None = None,
    tool_trace: list | None = None,
) -> BugAttributionReport:
    return BugAttributionReport(
        problem_title="Test bug",
        problem_description="Test description",
        suspects=suspects or [],
        reasoning_summary="Mock investigation",
        tool_trace=tool_trace or [],
        metadata={
            "turns_used": 1,
            "tool_calls": 0,
            "tokens_used": 100,
            "total_cost_usd": 0.001,
            "elapsed_ms": 50.0,
            "model": "mock",
        },
    )


class TestEvalCase:
    def test_dataclass_fields(self) -> None:
        case = EvalCase(
            bug_hash="abc123",
            fix_hash="def456",
            project="CAMEL",
            issue_key="CAMEL-1234",
        )
        assert case.bug_hash == "abc123"
        assert case.problem is None


class TestComparisonReport:
    def test_to_dict(self) -> None:
        from commit_investigator.eval.eval_metrics import (
            AggregateEvalReport,
        )

        agent_agg = AggregateEvalReport(
            total=2, hit_at_1=0.5, hit_at_3=0.5, hit_at_5=1.0,
            mrr=0.75, retrieval_recall=1.0, evidence_grounding_rate=0.8,
            total_cost_usd=0.01, avg_tool_calls=5.0,
            avg_tokens=1000.0, avg_elapsed_ms=500.0,
        )
        baseline_agg = AggregateEvalReport(
            total=2, hit_at_1=0.0, hit_at_3=0.0, hit_at_5=0.5,
            mrr=0.1, retrieval_recall=0.5, evidence_grounding_rate=0.0,
            total_cost_usd=0.0, avg_tool_calls=0.0,
            avg_tokens=0.0, avg_elapsed_ms=10.0,
        )

        report = ComparisonReport(
            agent=agent_agg,
            baselines={"git-blame-naive": baseline_agg},
        )

        d = report.to_dict()
        assert d["agent"]["total"] == 2
        assert d["agent"]["hit_at_5"] == 1.0
        assert "git-blame-naive" in d["baselines"]
        assert d["case_count"] == 0


class TestMarkdownRender:
    def test_renders_table(self) -> None:
        from commit_investigator.eval.eval_metrics import AggregateEvalReport

        agent_agg = AggregateEvalReport(
            total=5, hit_at_1=0.2, hit_at_3=0.4, hit_at_5=0.6,
            mrr=0.3, retrieval_recall=0.8, evidence_grounding_rate=0.7,
            total_cost_usd=0.05, avg_tool_calls=8.0,
            avg_tokens=2000.0, avg_elapsed_ms=1000.0,
        )
        baseline_agg = AggregateEvalReport(
            total=5, hit_at_1=0.0, hit_at_3=0.2, hit_at_5=0.4,
            mrr=0.1, retrieval_recall=0.4, evidence_grounding_rate=0.0,
            total_cost_usd=0.0, avg_tool_calls=0.0,
            avg_tokens=0.0, avg_elapsed_ms=5.0,
        )

        report = ComparisonReport(
            agent=agent_agg,
            baselines={"git-blame-naive": baseline_agg},
        )

        md = _render_markdown_report(report)
        assert "Hit@1" in md
        assert "Hit@5" in md
        assert "Agent" in md
        assert "git-blame-naive" in md
        assert "$0.0000" in md


class TestPrintComparison:
    def test_no_error(self, capsys: pytest.CaptureFixture) -> None:
        from commit_investigator.eval.eval_metrics import AggregateEvalReport

        agent_agg = AggregateEvalReport(
            total=3, hit_at_1=0.33, hit_at_3=0.33, hit_at_5=0.67,
            mrr=0.5, retrieval_recall=0.67, evidence_grounding_rate=0.5,
            total_cost_usd=0.03, avg_tool_calls=5.0,
            avg_tokens=1500.0, avg_elapsed_ms=800.0,
        )

        report = ComparisonReport(agent=agent_agg, baselines={})
        _print_comparison(report)

        out = capsys.readouterr().out
        assert "V3 EVALUATION RESULTS" in out
        assert "Agent" in out


def _make_jira_issue(
    key: str = "CAMEL-1234",
    summary: str = "Test bug",
    description: str | None = "Description text",
) -> JiraIssue:
    return JiraIssue(
        key=key,
        summary=summary,
        description=description,
        priority="Major",
        components=[],
        resolution=None,
        status="Open",
    )


class TestSelectEvalCases:
    def test_builds_problem_statement_from_jira(self) -> None:
        gt = MagicMock(spec=GroundTruthGraph)
        gt.projects = ["CAMEL"]
        gt._bug_to_fixes = {"bugabc123": ["fixdef456"]}
        gt.get_chain.return_value = CommitChain(
            bug_hash="bugabc123",
            fix_hashes=["fixdef456"],
            issue_keys=["CAMEL-9999"],
        )

        jira = MagicMock()
        jira.get_issue.return_value = _make_jira_issue(
            key="CAMEL-9999",
            summary="NPE in RouteBuilder",
            description="Stack trace shows NullPointerException in configure()",
        )

        cases = select_eval_cases(gt, jira, n=1, seed=42)

        assert len(cases) == 1
        case = cases[0]
        assert case.bug_hash == "bugabc123"
        assert case.fix_hash == "fixdef456"
        assert case.project == "CAMEL"
        assert case.issue_key == "CAMEL-9999"
        assert case.problem is not None
        assert case.problem.title == "NPE in RouteBuilder"
        assert case.problem.project == "CAMEL"
        assert case.problem.issue_key == "CAMEL-9999"
        assert "NullPointerException" in case.problem.description

    def test_skips_issues_without_description(self) -> None:
        chains = {
            "bug1": CommitChain(bug_hash="bug1", fix_hashes=["fix1"], issue_keys=["CAMEL-1"]),
            "bug2": CommitChain(bug_hash="bug2", fix_hashes=["fix2"], issue_keys=["CAMEL-2"]),
        }
        gt = MagicMock(spec=GroundTruthGraph)
        gt.projects = ["CAMEL"]
        gt._bug_to_fixes = {"bug1": ["fix1"], "bug2": ["fix2"]}
        gt.get_chain.side_effect = lambda bug_hash: chains[bug_hash]

        jira = MagicMock()

        def _get_issue(issue_key: str) -> JiraIssue:
            if issue_key == "CAMEL-1":
                return _make_jira_issue(key="CAMEL-1", summary="No desc bug", description="")
            return _make_jira_issue(
                key="CAMEL-2",
                summary="Has desc",
                description="Repro steps for the failure",
            )

        jira.get_issue.side_effect = _get_issue

        cases = select_eval_cases(gt, jira, n=2, seed=42)

        assert len(cases) == 1
        assert cases[0].issue_key == "CAMEL-2"

    def test_skips_jira_client_errors(self) -> None:
        gt = MagicMock(spec=GroundTruthGraph)
        gt.projects = ["CAMEL"]
        gt._bug_to_fixes = {"bug1": ["fix1"]}
        gt.get_chain.return_value = CommitChain(
            bug_hash="bug1",
            fix_hashes=["fix1"],
            issue_keys=["CAMEL-404"],
        )

        jira = MagicMock()
        jira.get_issue.side_effect = JiraClientError("issue not in cache")

        cases = select_eval_cases(gt, jira, n=1, seed=42)

        assert cases == []
