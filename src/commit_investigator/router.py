"""XGBoost routing layer: scores commits and routes to SAFE/INVESTIGATE/HIGH.

Trained on ApacheJIT numeric features from the train split.
Routes commits based on probability thresholds: <0.3 SAFE, 0.3-0.7 INVESTIGATE, >0.7 HIGH.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score, brier_score_loss


class Route(str, Enum):
    """Routing decision for a commit."""

    SAFE = "SAFE"
    INVESTIGATE = "INVESTIGATE"
    HIGH = "HIGH"


NUMERIC_FEATURES = [
    "la", "ld", "nf", "nd", "ns", "ent", "ndev", "age", "nuc",
    "aexp", "arexp", "asexp",
]


@dataclass
class RoutingDecision:
    """Routing result for a single commit."""

    commit_id: str
    project: str
    probability: float
    route: Route


@dataclass
class RouterMetrics:
    """Performance metrics for the trained router."""

    auc_roc: float
    brier_score: float
    safe_count: int
    investigate_count: int
    high_count: int
    total: int


class XGBoostRouter:
    """Routes commits based on XGBoost probability scores on numeric features.

    Thresholds: P<0.3 → SAFE, 0.3≤P≤0.7 → INVESTIGATE, P>0.7 → HIGH.
    """

    def __init__(
        self,
        safe_threshold: float = 0.3,
        high_threshold: float = 0.7,
    ) -> None:
        self._safe_threshold = safe_threshold
        self._high_threshold = high_threshold
        self._model: Any = None
        self._feature_names = NUMERIC_FEATURES

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    def train(self, csv_path: str | Path) -> RouterMetrics:
        """Train XGBoost on numeric features from the train CSV."""
        import xgboost as xgb

        X, y, _ = self._load_features(csv_path)

        self._model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=6,
            learning_rate=0.1,
            eval_metric="logloss",
            random_state=42,
        )
        self._model.fit(X, y)

        probas = self._model.predict_proba(X)[:, 1]
        auc = roc_auc_score(y, probas)
        brier = brier_score_loss(y, probas)

        routes = [self._classify(p) for p in probas]
        return RouterMetrics(
            auc_roc=auc,
            brier_score=brier,
            safe_count=routes.count(Route.SAFE),
            investigate_count=routes.count(Route.INVESTIGATE),
            high_count=routes.count(Route.HIGH),
            total=len(routes),
        )

    def route_split(self, csv_path: str | Path) -> list[RoutingDecision]:
        """Score and route all commits in a CSV split."""
        if not self.is_trained:
            raise RuntimeError("Router not trained. Call train() first.")

        X, _, meta = self._load_features(csv_path)
        probas = self._model.predict_proba(X)[:, 1]

        decisions = []
        for i, (commit_id, project) in enumerate(meta):
            prob = float(probas[i])
            decisions.append(RoutingDecision(
                commit_id=commit_id,
                project=project,
                probability=prob,
                route=self._classify(prob),
            ))

        return decisions

    def route_single(self, features: dict[str, float]) -> RoutingDecision:
        """Route a single commit given its numeric features."""
        if not self.is_trained:
            raise RuntimeError("Router not trained. Call train() first.")

        X = np.array([[features.get(f, 0.0) for f in self._feature_names]])
        prob = float(self._model.predict_proba(X)[0, 1])

        return RoutingDecision(
            commit_id=features.get("commit_id", "unknown"),
            project=features.get("project", "unknown"),
            probability=prob,
            route=self._classify(prob),
        )

    def save(self, path: str | Path) -> None:
        """Persist trained model to disk."""
        if not self.is_trained:
            raise RuntimeError("No model to save")
        self._model.save_model(str(path))

    def load(self, path: str | Path) -> None:
        """Load a previously trained model."""
        import xgboost as xgb

        self._model = xgb.XGBClassifier()
        self._model.load_model(str(path))

    def _classify(self, probability: float) -> Route:
        if probability < self._safe_threshold:
            return Route.SAFE
        elif probability > self._high_threshold:
            return Route.HIGH
        return Route.INVESTIGATE

    def _load_features(
        self, csv_path: str | Path
    ) -> tuple[np.ndarray, np.ndarray, list[tuple[str, str]]]:
        """Load numeric features and labels from CSV."""
        rows_X = []
        rows_y = []
        meta = []

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                features = []
                for feat in self._feature_names:
                    try:
                        features.append(float(row.get(feat, 0)))
                    except (ValueError, TypeError):
                        features.append(0.0)

                buggy = row.get("buggy", "False") in ("True", "true", "1")
                rows_X.append(features)
                rows_y.append(1 if buggy else 0)
                meta.append((row.get("commit_id", ""), row.get("project", "")))

        return np.array(rows_X), np.array(rows_y), meta


def emit_routing_manifest(decisions: list[RoutingDecision], output_path: str | Path) -> None:
    """Write routing manifest as JSON for downstream processing."""
    manifest = {
        "total": len(decisions),
        "safe": sum(1 for d in decisions if d.route == Route.SAFE),
        "investigate": sum(1 for d in decisions if d.route == Route.INVESTIGATE),
        "high": sum(1 for d in decisions if d.route == Route.HIGH),
        "decisions": [
            {
                "commit_id": d.commit_id,
                "project": d.project,
                "probability": round(d.probability, 4),
                "route": d.route.value,
            }
            for d in decisions
        ],
    }
    Path(output_path).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
