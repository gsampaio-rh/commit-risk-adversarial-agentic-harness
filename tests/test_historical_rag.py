"""Unit tests for historical_rag.py (historical defect context).

Coverage:
  AC-1  _classify_message() returns expected category for representative messages
  AC-2  _extract_metrics() handles missing/invalid CSV fields gracefully
  AC-3  get_historical_defect_context() returns None when csv_features is empty
  AC-4  Fallback activates when fewer than _MIN_CLASSIFIED_FOR_KNN neighbors classified
  AC-5  Project-level distribution is cached (subprocess not re-invoked on 2nd call)
  AC-6  Module import does not fail when apachejit_train.csv is absent
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import commit_investigator.hypothesis.historical_rag as _rag
from commit_investigator.context.context_builder import InvestigationContext
from commit_investigator.hypothesis.historical_rag import (
    _MIN_CLASSIFIED_FOR_KNN,
    _TrainingRow,
    _classify_message,
    _extract_metrics,
    get_historical_defect_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(csv_features: dict | None = None, project: str = "camel") -> InvestigationContext:
    """Return a minimal InvestigationContext for testing."""
    return InvestigationContext(
        commit_id="deadbeef",
        project=project,
        diff="diff --git a/Foo.java b/Foo.java\n+int x = null;",
        message="fix NPE",
        touched_files=["Foo.java"],
        csv_features=csv_features or {},
        file_histories={},
        author_stats=None,
    )


_VALID_CSV_ROW = {"la": "10", "ld": "3", "nf": "2", "ent": "0.5", "ns": "1"}

_CSV_FIELDNAMES = ["commit_id", "project", "la", "ld", "nf", "ent", "ns", "buggy"]


def _write_training_csv(path: Path, rows: list[dict]) -> None:
    """Write a minimal ApacheJIT-style CSV for testing."""
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


@pytest.fixture(autouse=True)
def _reset_caches():
    """Reset all module-level singletons before/after each test."""
    _rag._TRAINING_CACHE = None
    _rag._TRAINING_LOAD_ATTEMPTED = False
    _rag._PROJECT_DIST_CACHE = {}
    _rag._PROJECT_DIST_ATTEMPTED = set()
    yield
    _rag._TRAINING_CACHE = None
    _rag._TRAINING_LOAD_ATTEMPTED = False
    _rag._PROJECT_DIST_CACHE = {}
    _rag._PROJECT_DIST_ATTEMPTED = set()


# ---------------------------------------------------------------------------
# AC-1: _classify_message() — representative messages
# ---------------------------------------------------------------------------

class TestClassifyMessage:
    def test_null_dereference(self):
        assert _classify_message("Fix NullPointerException in Foo") == "null-dereference"

    def test_concurrency(self):
        assert _classify_message("Fix race condition in thread pool") == "concurrency"

    def test_resource_leak(self):
        assert _classify_message("Close unclosed socket connection") == "resource-leak"

    def test_error_handling(self):
        assert _classify_message("Handle timeout exception with retry") == "error-handling"

    def test_api_contract(self):
        assert _classify_message("Deprecate old API endpoint parameter") == "api-contract"

    def test_lifecycle_ordering(self):
        assert _classify_message("Fix startup ordering during initialization") == "lifecycle-ordering"

    def test_logic_error(self):
        assert _classify_message("Fix off-by-one in loop boundary") == "logic-error"

    def test_input_validation(self):
        assert _classify_message("Validate charset encoding in request parser") == "input-validation"

    def test_configuration(self):
        # "timeout" → error-handling, "wrong default" → logic-error both appear earlier;
        # use autowir which only appears in the configuration pattern
        assert _classify_message("Fix autowired spring.boot configuration") == "configuration"

    def test_unclassified_returns_none(self):
        assert _classify_message("Refactor build system dependencies") is None

    def test_first_match_wins(self):
        # "null" matches null-dereference before concurrency even if thread is present
        assert _classify_message("Fix null thread state") == "null-dereference"

    def test_empty_string_returns_none(self):
        assert _classify_message("") is None


# ---------------------------------------------------------------------------
# AC-2: _extract_metrics() — missing/invalid CSV fields
# ---------------------------------------------------------------------------

class TestExtractMetrics:
    def test_valid_row_returns_tuple(self):
        result = _extract_metrics(_VALID_CSV_ROW)
        assert result is not None
        assert len(result) == 5

    def test_log_transform_applied_to_la_ld_nf(self):
        result = _extract_metrics(_VALID_CSV_ROW)
        assert result is not None
        la, ld, nf, ent, ns = result
        assert math.isclose(la, math.log1p(10))
        assert math.isclose(ld, math.log1p(3))
        assert math.isclose(nf, math.log1p(2))
        assert math.isclose(ent, 0.5)
        assert math.isclose(ns, 1.0)

    def test_missing_key_returns_none(self):
        incomplete = {"la": "5", "ld": "2"}  # missing nf, ent, ns
        assert _extract_metrics(incomplete) is None

    def test_non_numeric_value_returns_none(self):
        bad = {**_VALID_CSV_ROW, "la": "not_a_number"}
        assert _extract_metrics(bad) is None

    def test_empty_string_field_treated_as_zero(self):
        row = {**_VALID_CSV_ROW, "ent": "", "ns": ""}
        result = _extract_metrics(row)
        assert result is not None
        # ent and ns use float(row.get(k, 0) or 0), so empty string → 0
        _, _, _, ent, ns = result
        assert ent == 0.0
        assert ns == 0.0

    def test_zero_values_allowed(self):
        row = {"la": "0", "ld": "0", "nf": "0", "ent": "0", "ns": "0"}
        result = _extract_metrics(row)
        assert result is not None
        assert result == (math.log1p(0),) * 3 + (0.0, 0.0)


# ---------------------------------------------------------------------------
# AC-3: returns None when csv_features is empty
# ---------------------------------------------------------------------------

class TestReturnNoneOnEmptyCsvFeatures:
    def test_empty_dict(self):
        assert get_historical_defect_context(_ctx(csv_features={})) is None

    def test_none_csv_features_equivalent(self):
        ctx = _ctx()
        ctx.csv_features = {}
        assert get_historical_defect_context(ctx) is None

    def test_invalid_metrics_returns_none(self):
        ctx = _ctx(csv_features={"la": "bad", "ld": "0", "nf": "0", "ent": "0", "ns": "0"})
        assert get_historical_defect_context(ctx) is None


# ---------------------------------------------------------------------------
# AC-4: fallback activates when KNN hit rate < _MIN_CLASSIFIED_FOR_KNN
# ---------------------------------------------------------------------------

class TestFallbackActivation:
    def test_fallback_used_when_knn_sparse(self, tmp_path):
        """KNN returns all None (no repo) → fallback project distribution used."""
        csv_path = tmp_path / "apachejit_train.csv"
        _write_training_csv(csv_path, [
            {"commit_id": f"c{i}", "project": "apache/camel",
             "la": "10", "ld": "5", "nf": "2", "ent": "0.4", "ns": "1", "buggy": "True"}
            for i in range(5)
        ])

        def fake_get_message(commit_id, project_key, repos_base):
            # Project-level distribution calls: return classifiable messages
            # KNN calls (first 5): return None so classified < _MIN_CLASSIFIED_FOR_KNN
            if commit_id.startswith("c"):
                return "Fix NullPointerException in service"
            return None

        with patch.object(_rag, "_JIT_CSV", csv_path), \
             patch.object(_rag, "_get_commit_message") as mock_msg:
            # KNN call: None for all neighbors (< threshold classified)
            mock_msg.side_effect = lambda cid, proj, repos: None
            # Reset, then run with project fallback providing messages
            # Re-prime: project dist must return classified msgs
            # We need two phases: KNN phase = None, fallback phase = classifiable
            call_count = [0]
            def smart_mock(cid, proj, repos):
                call_count[0] += 1
                # First _MIN_CLASSIFIED_FOR_KNN calls (KNN phase): return None
                if call_count[0] <= 5:
                    return None
                return "Fix NullPointerException in scheduler"

            mock_msg.side_effect = smart_mock
            ctx = _ctx(csv_features={"la": "8", "ld": "4", "nf": "2", "ent": "0.4", "ns": "1"})
            result = get_historical_defect_context(ctx, repos_base=tmp_path / "repos")

        assert result is not None
        assert "project-wide camel base rate" in result

    def test_no_fallback_when_knn_sufficient(self, tmp_path):
        """KNN returns enough classified messages → no fallback phrasing."""
        csv_path = tmp_path / "apachejit_train.csv"
        _write_training_csv(csv_path, [
            {"commit_id": f"c{i}", "project": "apache/camel",
             "la": "10", "ld": "5", "nf": "2", "ent": "0.4", "ns": "1", "buggy": "True"}
            for i in range(5)
        ])

        with patch.object(_rag, "_JIT_CSV", csv_path), \
             patch.object(_rag, "_get_commit_message",
                          return_value="Fix NullPointerException"):
            ctx = _ctx(csv_features={"la": "8", "ld": "4", "nf": "2", "ent": "0.4", "ns": "1"})
            result = get_historical_defect_context(ctx, repos_base=tmp_path / "repos")

        assert result is not None
        assert "similar camel commits" in result
        assert "project-wide" not in result


# ---------------------------------------------------------------------------
# AC-5: project-level distribution is cached (no double subprocess)
# ---------------------------------------------------------------------------

class TestProjectDistributionCaching:
    def test_cached_on_second_call(self, tmp_path):
        """_get_project_distribution should not call _get_commit_message twice for same project."""
        csv_path = tmp_path / "apachejit_train.csv"
        rows = [
            {"commit_id": f"c{i}", "project": "apache/camel",
             "la": "5", "ld": "2", "nf": "1", "ent": "0.3", "ns": "1", "buggy": "True"}
            for i in range(4)
        ]
        _write_training_csv(csv_path, rows)

        training = [
            _TrainingRow(r["commit_id"], r["project"],
                         (math.log1p(5), math.log1p(2), math.log1p(1), 0.3, 1.0))
            for r in rows
        ]

        with patch.object(_rag, "_get_commit_message",
                          return_value="Fix race condition") as mock_msg:
            _rag._get_project_distribution("apache/camel", training, tmp_path / "repos")
            first_call_count = mock_msg.call_count

            # Second call — should hit cache, not invoke subprocess
            _rag._get_project_distribution("apache/camel", training, tmp_path / "repos")
            second_call_count = mock_msg.call_count

        assert first_call_count > 0, "Expected at least one subprocess call on first invocation"
        assert second_call_count == first_call_count, "Cache should prevent second invocation"

    def test_cache_stores_result_in_module_dict(self, tmp_path):
        training = [
            _TrainingRow("abc", "apache/camel", (math.log1p(3), math.log1p(1), math.log1p(1), 0.2, 1.0))
        ]
        with patch.object(_rag, "_get_commit_message", return_value="Fix NullPointerException"):
            _rag._get_project_distribution("apache/camel", training, tmp_path / "repos")

        assert "apache/camel" in _rag._PROJECT_DIST_CACHE


# ---------------------------------------------------------------------------
# AC-6: module import does not fail when CSV is absent
# ---------------------------------------------------------------------------

class TestImportWithMissingCsv:
    def test_load_training_data_returns_empty_when_csv_missing(self, tmp_path):
        """_load_training_data gracefully returns [] when the CSV path does not exist."""
        missing = tmp_path / "no_such_file.csv"
        with patch.object(_rag, "_JIT_CSV", missing):
            result = _rag._load_training_data()
        assert result == []

    def test_get_context_returns_none_when_training_empty(self, tmp_path):
        """Full pipeline returns None gracefully when training data is absent."""
        missing = tmp_path / "no_such_file.csv"
        with patch.object(_rag, "_JIT_CSV", missing):
            ctx = _ctx(csv_features={"la": "5", "ld": "2", "nf": "1", "ent": "0.3", "ns": "1"})
            result = get_historical_defect_context(ctx)
        assert result is None

    def test_module_importable(self):
        """Smoke test: the module is importable without side effects."""
        import importlib
        import commit_investigator.hypothesis.historical_rag as m
        assert hasattr(m, "get_historical_defect_context")
        assert hasattr(m, "_classify_message")
