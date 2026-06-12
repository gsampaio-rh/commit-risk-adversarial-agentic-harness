"""Tests for run_eval commit selection helpers."""

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from commit_investigator.analysis.report import (
    CommitInvestigationReport,
    EvidenceItem,
    EvidenceType,
    RiskAssessment,
    RiskLevel,
)
from commit_investigator.runners.run_eval import (
    EvalRunner,
    _build_arg_parser,
    _resolve_commit_id,
    _save_investigation,
    _save_run_config,
    _select_by_commit_ids,
)
from commit_investigator.routing.router import Route, RoutingDecision


def _csv_rows() -> dict[str, dict[str, str]]:
    return {
        "f897d46870baf9eacf8d32d704f4bfaf13df3fd9": {"project": "apache/camel"},
        "90846b586c5160ee098f9c292d0ad1a655fe4d2a": {"project": "apache/camel"},
        "b4c933b7f958bb8378990292d14441a2bd1deea5": {"project": "apache/camel"},
    }


def _decisions() -> list[RoutingDecision]:
    return [
        RoutingDecision("f897d46870baf9eacf8d32d704f4bfaf13df3fd9", "camel", 0.55, Route.INVESTIGATE),
        RoutingDecision("90846b586c5160ee098f9c292d0ad1a655fe4d2a", "camel", 0.48, Route.INVESTIGATE),
        RoutingDecision("b4c933b7f958bb8378990292d14441a2bd1deea5", "camel", 0.62, Route.INVESTIGATE),
    ]


class TestResolveCommitId:
    def test_prefix_resolves_to_full_id(self) -> None:
        rows = _csv_rows()
        assert _resolve_commit_id("f897d46", rows) == "f897d46870baf9eacf8d32d704f4bfaf13df3fd9"

    def test_full_id_passes_through(self) -> None:
        rows = _csv_rows()
        full = "b4c933b7f958bb8378990292d14441a2bd1deea5"
        assert _resolve_commit_id(full, rows) == full

    def test_unknown_prefix_returns_none(self) -> None:
        assert _resolve_commit_id("deadbeef", _csv_rows()) is None


class TestSelectByCommitIds:
    def test_selects_requested_commits_in_order(self) -> None:
        git = MagicMock()
        selected, stats = _select_by_commit_ids(
            _decisions(),
            _csv_rows(),
            {"camel": git},
            ["b4c933b7", "f897d46"],
        )
        assert len(selected) == 2
        assert selected[0].commit_id.startswith("b4c933b7")
        assert selected[1].commit_id.startswith("f897d46")
        assert stats["commit_ids_mode"] == 1

    def test_raises_when_commit_missing(self) -> None:
        with pytest.raises(ValueError, match="Could not resolve"):
            _select_by_commit_ids(_decisions(), _csv_rows(), {"camel": MagicMock()}, ["missing"])


class TestSaveRunConfigContrastive:
    def test_enable_contrastive_written_to_run_config(self, tmp_path: Path) -> None:
        args = argparse.Namespace(enable_contrastive=True, enable_mechanism_evaluator=False)
        _save_run_config(tmp_path, args, {"enable_contrastive": True})
        config = json.loads((tmp_path / "run-config.json").read_text(encoding="utf-8"))
        assert config["enable_contrastive"] is True
        assert config["args"]["enable_contrastive"] is True


class TestNormalizeProject:
    from commit_investigator.runners.eval_common import _normalize_project

    def test_short_name_passthrough(self) -> None:
        assert TestNormalizeProject._normalize_project("camel") == "camel"

    def test_apache_prefix_stripped(self) -> None:
        assert TestNormalizeProject._normalize_project("apache/camel") == "camel"

    def test_uppercase_normalized(self) -> None:
        assert TestNormalizeProject._normalize_project("CAMEL") == "camel"

    def test_whitespace_stripped(self) -> None:
        assert TestNormalizeProject._normalize_project("  apache/camel  ") == "camel"


class TestLoadDotenvPreservesSetKeys:
    from commit_investigator.runners.eval_common import _load_dotenv

    def test_set_keys_not_overwritten_unset_keys_loaded(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "TEST_EVAL_KEY=from_file\nTEST_EVAL_UNSET=loaded_value\n",
            encoding="utf-8",
        )
        os.environ["TEST_EVAL_KEY"] = "preset"
        try:
            TestLoadDotenvPreservesSetKeys._load_dotenv(env_file)
            assert os.environ["TEST_EVAL_KEY"] == "preset"
            assert os.environ["TEST_EVAL_UNSET"] == "loaded_value"
        finally:
            os.environ.pop("TEST_EVAL_KEY", None)
            os.environ.pop("TEST_EVAL_UNSET", None)


class TestGitRevUnknownOnFailure:
    from commit_investigator.runners.eval_common import _git_rev

    def test_returns_unknown_when_git_fails(self) -> None:
        with patch("commit_investigator.runners.eval_common.subprocess.check_output", side_effect=OSError):
            assert TestGitRevUnknownOnFailure._git_rev() == "unknown"


class TestEvalCommonImportNoSideEffects:
    def test_import_eval_common_does_not_invoke_subprocess(self) -> None:
        sys.modules.pop("commit_investigator.runners.eval_common", None)
        with patch("subprocess.check_output", side_effect=AssertionError("subprocess invoked on import")):
            importlib.import_module("commit_investigator.runners.eval_common")

    def test_import_run_eval_does_not_load_dotenv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        marker = "TEST_EVAL_IMPORT_SIDE_EFFECT"
        monkeypatch.setenv(marker, "keep")
        before = os.environ.get("OPENAI_API_KEY")
        importlib.import_module("commit_investigator.runners.run_eval")
        assert os.environ.get("OPENAI_API_KEY") == before
        assert os.environ.get(marker) == "keep"


def _minimal_report() -> CommitInvestigationReport:
    return CommitInvestigationReport(
        commit_id="abc123deadbeef",
        project="camel",
        risk_assessment=RiskAssessment(level=RiskLevel.MEDIUM, confidence=0.7),
        evidence=[
            EvidenceItem(
                type=EvidenceType.DIFF_HUNK,
                source="src/Main.java",
                content="+ risky",
                relevance="test",
            )
        ],
        findings=[],
        reasoning_summary="test",
        turn_count=1,
    )


class TestSaveInvestigationHistoricalDefectStatus:
    """AC-2: forensics JSON top-level historical_defect_context_status."""

    def test_disabled_status_written_top_level(self, tmp_path: Path) -> None:
        inv_dir = tmp_path / "investigations"
        inv_dir.mkdir()
        report = _minimal_report()
        _save_investigation(
            inv_dir,
            report,
            buggy_label=True,
            elapsed=1.5,
            route="INVESTIGATE",
            historical_defect_context_status="disabled",
        )
        data = json.loads(
            (inv_dir / f"{report.commit_id[:12]}_{report.project}.json").read_text(encoding="utf-8")
        )
        assert data["historical_defect_context_status"] == "disabled"

    def test_none_status_defaults_to_disabled_in_json(self, tmp_path: Path) -> None:
        inv_dir = tmp_path / "investigations"
        inv_dir.mkdir()
        report = _minimal_report()
        _save_investigation(
            inv_dir,
            report,
            buggy_label=False,
            elapsed=0.5,
            route="INVESTIGATE",
            historical_defect_context_status=None,
        )
        data = json.loads(
            (inv_dir / f"{report.commit_id[:12]}_{report.project}.json").read_text(encoding="utf-8")
        )
        assert data["historical_defect_context_status"] == "disabled"


class TestHistoricalDefectContextCli:
    """AC-3: CLI flag exists, argparse default False, --help works without PYTHONPATH."""

    def test_parser_default_enable_historical_defect_context_false(self) -> None:
        args = _build_arg_parser().parse_args([])
        assert args.enable_historical_defect_context is False

    def test_parser_flag_sets_true(self) -> None:
        args = _build_arg_parser().parse_args(["--enable-historical-defect-context"])
        assert args.enable_historical_defect_context is True

    def test_help_shows_flag_via_script_path(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "src" / "commit_investigator" / "runners" / "run_eval.py"
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "enable-historical-defect-context" in result.stdout


class TestHistoricalDefectContextRunConfig:
    """AC-3: default off in run_config."""

    def test_default_enable_historical_defect_context_false(self, tmp_path: Path) -> None:
        args = _build_arg_parser().parse_args([])
        assert args.enable_historical_defect_context is False
        _save_run_config(
            tmp_path,
            args,
            {"enable_historical_defect_context": args.enable_historical_defect_context},
        )
        config = json.loads((tmp_path / "run-config.json").read_text(encoding="utf-8"))
        assert config["enable_historical_defect_context"] is False
        assert config["args"]["enable_historical_defect_context"] is False


class TestRunInvestigationsWiresHistoricalDefectFlag:
    """AC-6: run_investigations sets context.enable_historical_defect_context from CLI."""

    @patch("commit_investigator.runners.run_eval._print_progress")
    @patch("commit_investigator.runners.run_eval._save_investigation")
    @patch("commit_investigator.runners.run_eval._investigate_with_retry")
    @patch("commit_investigator.runners.run_eval.CommitContextBuilder")
    def test_context_flag_mirrors_runner_attr(
        self,
        mock_builder_cls,
        mock_investigate,
        mock_save,
        mock_progress,
    ) -> None:
        captured: dict = {}

        def capture_context(_orchestrator, **kwargs):
            captured["context"] = kwargs["context"]
            return _minimal_report()

        mock_investigate.side_effect = capture_context
        mock_builder = MagicMock()
        mock_builder_cls.return_value = mock_builder
        mock_builder.build.return_value = MagicMock(
            router_probability=None,
            router_route=None,
            enable_historical_defect_context=False,
            historical_defect_context_status=None,
        )

        args = argparse.Namespace(enable_historical_defect_context=True)
        runner = EvalRunner(args)
        runner.enable_historical_defect_context = True
        runner.extended_context = False
        runner.mechanism_evaluator = False
        runner.contrastive = False
        runner.target_commits = [
            RoutingDecision("abc123deadbeef", "camel", 0.55, Route.INVESTIGATE),
        ]
        runner.git_providers = {"camel": MagicMock()}
        runner.csv_rows = {"abc123deadbeef": {"project": "apache/camel"}}
        runner.buggy_lookup = {"abc123deadbeef": True}
        runner.author_stats = MagicMock()
        runner.orchestrator = MagicMock()
        runner.inv_dir = Path("/tmp/inv")
        runner.baseline_scores = {}

        runner.run_investigations()

        assert captured["context"].enable_historical_defect_context is True

    def test_setup_attr_from_parser_default(self) -> None:
        args = _build_arg_parser().parse_args([])
        runner = EvalRunner(args)
        runner.enable_historical_defect_context = getattr(
            runner.args, "enable_historical_defect_context", False
        )
        assert runner.enable_historical_defect_context is False
