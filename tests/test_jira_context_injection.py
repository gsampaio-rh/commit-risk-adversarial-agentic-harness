"""Unit tests for v2-jira-context-injection: JIRA title+type in hypothesis prompt."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from commit_investigator.context.context_builder import InvestigationContext
from commit_investigator.hypothesis.hypothesis_engine import build_investigation_messages
from commit_investigator.runners.run_eval import _load_jira_csv, _save_investigation, JiraContextEntry


def _ctx(**kwargs) -> InvestigationContext:
    defaults = {
        "commit_id": "abc123def456",
        "project": "camel",
        "diff": "- old\n+ new",
        "message": "CAMEL-1234: Fix NPE in route builder",
        "touched_files": ["Foo.java"],
        "csv_features": {"la": "10", "ld": "5", "nf": "2"},
        "file_histories": {},
        "author_stats": None,
    }
    defaults.update(kwargs)
    return InvestigationContext(**defaults)


def _user_content(msgs) -> str:
    return msgs[1].content


class TestJiraContextInjected:
    """AC-1: When jira_summary is present, prompt contains ## Ticket Context."""

    def test_ticket_context_section_present(self):
        ctx = _ctx(jira_summary="NullPointerException in CamelRouteBuilder", jira_issue_type="Bug")
        msgs = build_investigation_messages(ctx)
        content = _user_content(msgs)

        assert "## Ticket Context" in content
        assert "NullPointerException in CamelRouteBuilder" in content
        assert "Bug" in content
        assert ctx.jira_context_status == "injected"

    def test_ticket_context_has_type_and_title(self):
        ctx = _ctx(jira_summary="Improve error handling", jira_issue_type="Improvement")
        msgs = build_investigation_messages(ctx)
        content = _user_content(msgs)

        assert "- Issue type: Improvement" in content
        assert "- Title: Improve error handling" in content

    def test_ticket_context_without_type(self):
        ctx = _ctx(jira_summary="Fix serialization bug", jira_issue_type="")
        msgs = build_investigation_messages(ctx)
        content = _user_content(msgs)

        assert "## Ticket Context" in content
        assert "- Title: Fix serialization bug" in content
        assert "- Issue type:" not in content
        assert ctx.jira_context_status == "injected"


class TestJiraContextAbsent:
    """AC-2: When jira_summary is absent, prompt unchanged (no regression)."""

    def test_no_ticket_context_when_absent(self):
        ctx = _ctx()
        msgs = build_investigation_messages(ctx)
        content = _user_content(msgs)

        assert "## Ticket Context" not in content
        assert ctx.jira_context_status == "unavailable"

    def test_no_ticket_context_when_none(self):
        ctx = _ctx(jira_summary=None)
        msgs = build_investigation_messages(ctx)
        content = _user_content(msgs)

        assert "## Ticket Context" not in content
        assert ctx.jira_context_status == "unavailable"

    def test_no_ticket_context_when_empty_string(self):
        ctx = _ctx(jira_summary="")
        msgs = build_investigation_messages(ctx)
        content = _user_content(msgs)

        assert "## Ticket Context" not in content

    def test_prompt_structure_unchanged_without_jira(self):
        """Baseline prompt should have same sections with/without JIRA (minus Ticket Context)."""
        ctx_no_jira = _ctx()
        ctx_with_jira = _ctx(jira_summary="Fix bug", jira_issue_type="Bug")

        msgs_no = build_investigation_messages(ctx_no_jira)
        msgs_with = build_investigation_messages(ctx_with_jira)

        content_no = _user_content(msgs_no)
        content_with = _user_content(msgs_with)

        for section in ["## Commit", "## Diff", "## Touched Files", "## Numeric Features"]:
            assert section in content_no
            assert section in content_with


class TestJiraContextCsvLoader:
    """AC-3: --jira-csv loads mapping correctly."""

    def test_load_valid_csv(self, tmp_path):
        csv_path = tmp_path / "jira.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["commit_id", "jira_key", "jira_title", "jira_type"])
            writer.writerow(["abc123", "CAMEL-1234", "Fix NPE in router", "Bug"])
            writer.writerow(["def456", "HADOOP-5678", "Improve HDFS throughput", "Improvement"])

        mapping = _load_jira_csv(str(csv_path))

        assert len(mapping) == 2
        assert mapping["abc123"].jira_key == "CAMEL-1234"
        assert mapping["abc123"].jira_title == "Fix NPE in router"
        assert mapping["abc123"].jira_type == "Bug"
        assert mapping["def456"].jira_type == "Improvement"

    def test_load_csv_skips_empty_title(self, tmp_path):
        csv_path = tmp_path / "jira.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["commit_id", "jira_key", "jira_title", "jira_type"])
            writer.writerow(["abc123", "CAMEL-1234", "", "Bug"])
            writer.writerow(["def456", "HADOOP-5678", "Valid title", "Bug"])

        mapping = _load_jira_csv(str(csv_path))

        assert len(mapping) == 1
        assert "abc123" not in mapping
        assert "def456" in mapping

    def test_load_csv_skips_empty_commit_id(self, tmp_path):
        csv_path = tmp_path / "jira.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["commit_id", "jira_key", "jira_title", "jira_type"])
            writer.writerow(["", "CAMEL-1234", "Has title", "Bug"])

        mapping = _load_jira_csv(str(csv_path))
        assert len(mapping) == 0


class TestJiraContextForensics:
    """AC-3: jira_context_status logged in investigation JSON."""

    def test_save_investigation_includes_jira_status_injected(self, tmp_path):
        from commit_investigator.infra.llm import MockLLMProvider
        from commit_investigator.pipeline.orchestrator import AgentOrchestrator

        ctx = _ctx(jira_summary="Fix NPE", jira_issue_type="Bug")
        orchestrator = AgentOrchestrator(llm_provider=MockLLMProvider(), max_turns=1)
        report = orchestrator.investigate(commit_id="abc123def456", project="camel", context=ctx)

        inv_dir = tmp_path / "investigations"
        inv_dir.mkdir()
        _save_investigation(
            inv_dir,
            report,
            buggy_label=True,
            elapsed=1.0,
            route="INVESTIGATE",
            jira_context_status=ctx.jira_context_status,
        )

        files = list(inv_dir.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["jira_context_status"] == "injected"

    def test_save_investigation_includes_jira_status_unavailable(self, tmp_path):
        from commit_investigator.infra.llm import MockLLMProvider
        from commit_investigator.pipeline.orchestrator import AgentOrchestrator

        ctx = _ctx()
        orchestrator = AgentOrchestrator(llm_provider=MockLLMProvider(), max_turns=1)
        report = orchestrator.investigate(commit_id="abc123def456", project="camel", context=ctx)

        inv_dir = tmp_path / "investigations"
        inv_dir.mkdir()
        _save_investigation(
            inv_dir,
            report,
            buggy_label=True,
            elapsed=1.0,
            route="INVESTIGATE",
            jira_context_status=ctx.jira_context_status,
        )

        files = list(inv_dir.glob("*.json"))
        data = json.loads(files[0].read_text())
        assert data["jira_context_status"] == "unavailable"


class TestJiraContextCliFlag:
    """AC-3: --jira-csv flag accepted by arg parser."""

    def test_arg_parser_accepts_jira_csv(self):
        from commit_investigator.runners.run_eval import _build_arg_parser

        parser = _build_arg_parser()
        args = parser.parse_args(["--jira-csv", "data/jira_context.csv"])
        assert args.jira_csv == "data/jira_context.csv"

    def test_arg_parser_default_none(self):
        from commit_investigator.runners.run_eval import _build_arg_parser

        parser = _build_arg_parser()
        args = parser.parse_args([])
        assert args.jira_csv is None
