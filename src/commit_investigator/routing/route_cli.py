"""CLI for XGBoost routing: train and emit routing manifests.

Usage:
  python -m commit_investigator.routing.route_cli --train data/apachejit/apachejit_train.csv --split data/apachejit/apachejit_test_small.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from commit_investigator.routing.router import XGBoostRouter, emit_routing_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="XGBoost commit router")
    parser.add_argument("--train", required=True, help="Train CSV for model training")
    parser.add_argument("--split", required=False, help="Test split to route")
    parser.add_argument("--output", default="output/routing_manifest.json", help="Output manifest path")
    parser.add_argument("--model-path", default="output/router_model.json", help="Model save/load path")
    parser.add_argument("--load-model", action="store_true", help="Load existing model instead of training")

    args = parser.parse_args()

    router = XGBoostRouter()

    if args.load_model and Path(args.model_path).exists():
        print(f"Loading model from {args.model_path}...", file=sys.stderr)
        router.load(args.model_path)
    else:
        print(f"Training on {args.train}...", file=sys.stderr)
        metrics = router.train(args.train)
        print(f"  AUC-ROC: {metrics.auc_roc:.4f}", file=sys.stderr)
        print(f"  Brier score: {metrics.brier_score:.4f}", file=sys.stderr)
        print(f"  Routes: SAFE={metrics.safe_count}, INVESTIGATE={metrics.investigate_count}, HIGH={metrics.high_count}", file=sys.stderr)

        Path(args.model_path).parent.mkdir(parents=True, exist_ok=True)
        router.save(args.model_path)
        print(f"  Model saved to {args.model_path}", file=sys.stderr)

    if args.split:
        print(f"Routing {args.split}...", file=sys.stderr)
        decisions = router.route_split(args.split)

        safe = sum(1 for d in decisions if d.route.value == "SAFE")
        investigate = sum(1 for d in decisions if d.route.value == "INVESTIGATE")
        high = sum(1 for d in decisions if d.route.value == "HIGH")
        print(f"  Results: SAFE={safe}, INVESTIGATE={investigate}, HIGH={high} (total={len(decisions)})", file=sys.stderr)

        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        emit_routing_manifest(decisions, args.output)
        print(f"  Manifest written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
