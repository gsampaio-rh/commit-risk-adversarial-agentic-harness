"""Tests for the XGBoost router."""

import csv
from pathlib import Path

import pytest

from tests.conftest import skip_no_data

from commit_investigator.router import Route, XGBoostRouter


@pytest.fixture
def tiny_csv(tmp_path) -> Path:
    """Create a tiny CSV fixture for router training."""
    csv_path = tmp_path / "tiny_train.csv"
    rows = [
        {"commit_id": "a1", "project": "test", "buggy": "True", "la": "100", "ld": "50", "nf": "5", "nd": "2", "ns": "3", "ent": "3.0", "ndev": "5", "age": "10", "nuc": "8", "aexp": "20", "arexp": "15", "asexp": "10"},
        {"commit_id": "a2", "project": "test", "buggy": "True", "la": "200", "ld": "80", "nf": "8", "nd": "3", "ns": "4", "ent": "4.0", "ndev": "8", "age": "5", "nuc": "12", "aexp": "10", "arexp": "8", "asexp": "5"},
        {"commit_id": "b1", "project": "test", "buggy": "False", "la": "5", "ld": "2", "nf": "1", "nd": "1", "ns": "1", "ent": "0.5", "ndev": "1", "age": "100", "nuc": "1", "aexp": "500", "arexp": "400", "asexp": "300"},
        {"commit_id": "b2", "project": "test", "buggy": "False", "la": "3", "ld": "1", "nf": "1", "nd": "1", "ns": "1", "ent": "0.3", "ndev": "1", "age": "200", "nuc": "1", "aexp": "600", "arexp": "500", "asexp": "400"},
        {"commit_id": "b3", "project": "test", "buggy": "False", "la": "2", "ld": "1", "nf": "1", "nd": "1", "ns": "1", "ent": "0.2", "ndev": "1", "age": "300", "nuc": "1", "aexp": "700", "arexp": "600", "asexp": "500"},
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


class TestXGBoostRouter:
    def test_train_on_fixture(self, tiny_csv):
        router = XGBoostRouter()
        metrics = router.train(tiny_csv)
        assert router.is_trained
        assert metrics.total == 5
        assert metrics.auc_roc >= 0.0

    def test_route_single(self, tiny_csv):
        router = XGBoostRouter()
        router.train(tiny_csv)
        decision = router.route_single({"la": 100, "ld": 50, "nf": 5})
        assert decision.route in (Route.SAFE, Route.INVESTIGATE, Route.HIGH)
        assert 0.0 <= decision.probability <= 1.0

    def test_route_split(self, tiny_csv):
        router = XGBoostRouter()
        router.train(tiny_csv)
        decisions = router.route_split(tiny_csv)
        assert len(decisions) == 5
        assert all(d.route in (Route.SAFE, Route.INVESTIGATE, Route.HIGH) for d in decisions)

    def test_save_and_load(self, tiny_csv, tmp_path):
        router = XGBoostRouter()
        router.train(tiny_csv)
        model_path = tmp_path / "model.json"
        router.save(model_path)

        router2 = XGBoostRouter()
        router2.load(model_path)
        assert router2.is_trained

    def test_untrained_router_raises(self):
        router = XGBoostRouter()
        with pytest.raises(RuntimeError):
            router.route_single({"la": 10})


@skip_no_data
class TestRouterIntegration:
    def test_train_on_real_data(self):
        router = XGBoostRouter()
        metrics = router.train("data/apachejit/apachejit_train.csv")
        assert metrics.auc_roc > 0.8
        assert metrics.total == 44834
