"""Tests for run_eval commit selection helpers."""

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from commit_investigator.runners.run_eval import _resolve_commit_id, _save_run_config, _select_by_commit_ids
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
