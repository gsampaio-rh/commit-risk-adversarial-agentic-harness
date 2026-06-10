"""Tests for run_eval commit selection helpers."""

from unittest.mock import MagicMock

import pytest

from commit_investigator.run_eval import _resolve_commit_id, _select_by_commit_ids
from commit_investigator.router import Route, RoutingDecision


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
