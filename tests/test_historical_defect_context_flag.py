"""Unit tests for --enable-historical-defect-context feature flag (td-h3a-feature-flag)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import json

from commit_investigator.context.context_builder import InvestigationContext
from commit_investigator.runners.run_eval import _save_investigation
from commit_investigator.hypothesis.hypothesis_engine import build_investigation_messages
from commit_investigator.infra.llm import MockLLMProvider
from commit_investigator.pipeline.orchestrator import AgentOrchestrator
import commit_investigator.hypothesis.historical_rag as _rag
from commit_investigator.hypothesis.historical_rag import reset_training_cache


def _ctx(**kwargs) -> InvestigationContext:
    defaults = {
        "commit_id": "abc123",
        "project": "camel",
        "diff": "- old\n+ new",
        "message": "Fix bug",
        "touched_files": ["Foo.java"],
        "csv_features": {"la": "10", "ld": "5", "nf": "2", "ent": "0.4", "ns": "1"},
        "file_histories": {},
        "author_stats": None,
        "enable_historical_defect_context": False,
    }
    defaults.update(kwargs)
    return InvestigationContext(**defaults)


def _user_content(msgs) -> str:
    return msgs[1].content


class TestHistoricalDefectContextDisabled:
    """AC-1 / EC-1: flag off — no injection, status disabled."""

    @patch("commit_investigator.hypothesis.hypothesis_engine._get_historical_defect_context")
    def test_flag_false_never_calls_rag(self, mock_get):
        ctx = _ctx(enable_historical_defect_context=False)
        msgs = build_investigation_messages(ctx)

        assert mock_get.call_count == 0
        assert "## Historical Context" not in _user_content(msgs)
        assert not any("Historical context" in r for r in ctx.missing_reasons)
        assert ctx.historical_defect_context_status == "disabled"


class TestHistoricalDefectContextEnabled:
    """AC-5: flag on + mock returns content → injected."""

    @patch("commit_investigator.hypothesis.hypothesis_engine._get_historical_defect_context")
    def test_flag_true_injected_status_and_section(self, mock_get):
        mock_get.return_value = "Historical defect pattern (3 similar camel commits):\n  - NPE: 50%"
        ctx = _ctx(enable_historical_defect_context=True)
        msgs = build_investigation_messages(ctx)

        assert mock_get.call_count == 1
        assert ctx.historical_defect_context_status == "injected"
        assert "## Historical Context" in _user_content(msgs)


class TestHistoricalDefectContextUnavailable:
    """EC-2, EC-3, EC-4: unavailable paths."""

    @patch(
        "commit_investigator.hypothesis.hypothesis_engine._HISTORICAL_DEFECT_CONTEXT_AVAILABLE",
        False,
    )
    def test_ec2_import_unavailable(self):
        ctx = _ctx(enable_historical_defect_context=True)
        msgs = build_investigation_messages(ctx)

        assert ctx.historical_defect_context_status == "unavailable"
        assert "## Historical Context" not in _user_content(msgs)

    @patch("commit_investigator.hypothesis.hypothesis_engine._get_historical_defect_context")
    def test_ec3_exception_logged_unavailable(self, mock_get, caplog):
        mock_get.side_effect = RuntimeError("simulated failure")
        ctx = _ctx(enable_historical_defect_context=True)

        with caplog.at_level("WARNING"):
            msgs = build_investigation_messages(ctx)

        assert ctx.historical_defect_context_status == "unavailable"
        assert "## Historical Context" not in _user_content(msgs)
        assert any("historical defect context failed" in r.message for r in caplog.records)

    @patch("commit_investigator.hypothesis.hypothesis_engine._get_historical_defect_context")
    def test_ec4_none_return_adds_missing_reason(self, mock_get):
        mock_get.return_value = None
        ctx = _ctx(enable_historical_defect_context=True)
        msgs = build_investigation_messages(ctx)

        assert ctx.historical_defect_context_status == "unavailable"
        assert "## Historical Context" not in _user_content(msgs)
        assert any("Historical context" in r for r in ctx.missing_reasons)


class TestOrchestratorHistoricalDefectStatus:
    """End-to-end: investigate() sets forensics status on context."""

    @patch("commit_investigator.hypothesis.hypothesis_engine._get_historical_defect_context")
    def test_investigate_sets_disabled_when_flag_off(self, mock_get):
        ctx = _ctx(enable_historical_defect_context=False)
        orchestrator = AgentOrchestrator(llm_provider=MockLLMProvider(), max_turns=1)
        orchestrator.investigate(commit_id="abc123", project="camel", context=ctx)
        assert mock_get.call_count == 0
        assert ctx.historical_defect_context_status == "disabled"

    @patch("commit_investigator.hypothesis.hypothesis_engine._get_historical_defect_context")
    def test_investigation_json_gets_status_from_context(self, mock_get, tmp_path):
        ctx = _ctx(enable_historical_defect_context=False)
        orchestrator = AgentOrchestrator(llm_provider=MockLLMProvider(), max_turns=1)
        report = orchestrator.investigate(commit_id="abc123", project="camel", context=ctx)
        inv_dir = tmp_path / "investigations"
        inv_dir.mkdir()
        _save_investigation(
            inv_dir,
            report,
            buggy_label=True,
            elapsed=1.0,
            route="INVESTIGATE",
            historical_defect_context_status=ctx.historical_defect_context_status,
        )
        data = json.loads((inv_dir / f"{report.commit_id[:12]}_{report.project}.json").read_text())
        assert data["historical_defect_context_status"] == "disabled"


class TestHistoricalDefectContextFallback:
    """EC-5: KNN sparse → fallback status and project-wide block."""

    def test_ec5_fallback_status_via_build_messages(self, tmp_path):
        csv_path = tmp_path / "apachejit_train.csv"
        rows = [
            {
                "commit_id": f"c{i}",
                "project": "apache/camel",
                "la": "10",
                "ld": "5",
                "nf": "2",
                "ent": "0.4",
                "ns": "1",
                "buggy": "True",
            }
            for i in range(5)
        ]
        _write_training_csv(csv_path, rows)

        call_count = [0]

        def smart_mock(cid, proj, repos):
            call_count[0] += 1
            if call_count[0] <= 5:
                return None
            return "Fix NullPointerException in scheduler"

        reset_training_cache()
        with patch.object(_rag, "_JIT_CSV", csv_path), \
             patch.object(_rag, "_get_commit_message", side_effect=smart_mock):
            ctx = _ctx(enable_historical_defect_context=True)
            msgs = build_investigation_messages(ctx)

        assert ctx.historical_defect_context_status == "fallback"
        user = _user_content(msgs)
        assert "## Historical Context" in user
        assert "project-wide" in user


def _write_training_csv(path: Path, rows: list[dict]) -> None:
    import csv

    fieldnames = [
        "commit_id", "project", "la", "ld", "nf", "ent", "ns", "buggy", "author_date",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
