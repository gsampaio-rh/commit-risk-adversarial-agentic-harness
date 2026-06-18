#!/usr/bin/env python3
"""Scaled multi-model evaluation via Cursor SDK.

Runs the V4.2 pipeline on all available eval cases across multiple cloud models.
Includes early-stopping (5 hard-stop rules), checkpoint/resume, and a
side-by-side comparison report.

Usage:
    python scripts/run_scaled_eval.py
    python scripts/run_scaled_eval.py --models claude-sonnet-4-6,claude-haiku-4-5
    python scripts/run_scaled_eval.py --max-n 5 --models claude-haiku-4-5
    python scripts/run_scaled_eval.py --resume results/2026-06-18T14-00-scaled-eval
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from commit_investigator.eval.ground_truth import GroundTruthGraph
from commit_investigator.eval.helpers import (
    ZIP_PATH,
    RESULTS_DIR,
    build_eval_cases,
    create_run_folder,
    write_run_config,
)
from commit_investigator.eval.run_monitor import EarlyStopMonitor, Verdict
from commit_investigator.infra.llm import LLMMessage, LLMProvider, LLMResponse, get_provider
from commit_investigator.investigation.trace_writer import TraceWriter
from run_scoped_eval import run_single_case, print_results


class _CostTracker(LLMProvider):
    """Thin wrapper that accumulates estimated_cost across LLM calls."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.total_cost: float = 0.0
        self.call_count: int = 0

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    def complete(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        response = self._inner.complete(messages, **kwargs)
        self.total_cost += response.estimated_cost
        self.call_count += 1
        return response

    def cost_since(self, prev_cost: float) -> float:
        return self.total_cost - prev_cost

DEFAULT_MODELS = [
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "gpt-5.4-nano",
    "gpt-5.3-codex",
    "gemini-3-flash",
    "composer-2.5",
]


def _load_checkpoint(run_dir: Path) -> dict:
    """Load checkpoint from a previous run, or return empty state."""
    cp_path = run_dir / "checkpoint.json"
    if cp_path.exists():
        return json.loads(cp_path.read_text(encoding="utf-8"))
    return {"completed_models": {}, "global_cost": 0.0}


def _save_checkpoint(run_dir: Path, state: dict) -> None:
    """Persist checkpoint state for resume capability."""
    cp_path = run_dir / "checkpoint.json"
    cp_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _run_model(
    model_id: str,
    cases: list,
    run_dir: Path,
    monitor: EarlyStopMonitor,
    checkpoint: dict,
) -> tuple[dict, bool]:
    """Run all cases for a single model.

    Returns (model_summary, global_abort) where global_abort is True
    if the monitor triggered ABORT_ALL.
    """
    model_cp = checkpoint.get("completed_models", {}).get(model_id, {})
    saved_dir = model_cp.get("model_dir")

    if saved_dir and Path(saved_dir).exists():
        model_dir = Path(saved_dir)
    else:
        model_dir = create_run_folder(
            f"scaled-eval-{model_id.replace('/', '-')}",
            model=model_id,
            n=len(cases),
        )
        write_run_config(
            model_dir, label="scaled-eval", model=model_id,
            n=len(cases), seed=42, pipeline="v4.2",
            parent_run=run_dir.name,
        )

    writer = TraceWriter(model_dir / "traces", flat=True)

    completed_keys = set(model_cp.get("completed_keys", []))
    prior_results = model_cp.get("case_results", [])
    case_results: list[dict] = [
        r for r in prior_results if r.get("issue_key") in completed_keys
    ]

    raw_llm = get_provider(model=model_id, fail_fast=True)
    llm = _CostTracker(raw_llm)
    print(f"\n{'='*72}")
    print(f"MODEL: {llm.model_name}  ({len(cases)} cases, {len(completed_keys)} already done)")
    print(f"  Run folder: {model_dir.name}")
    print(f"{'='*72}")

    abort_reason = ""
    is_global_abort = False
    for i, case in enumerate(cases, 1):
        if case.issue_key in completed_keys:
            print(f"  [{i:2d}/{len(cases)}] {case.issue_key:16s}  SKIP (checkpoint)")
            continue

        cost_before = llm.total_cost
        print(f"  [{i:2d}/{len(cases)}] {case.issue_key:16s}", end="  ", flush=True)
        result = run_single_case(case, llm, writer)
        result["estimated_cost"] = llm.cost_since(cost_before)
        case_results.append(result)

        _print_case_result(result)
        completed_keys.add(case.issue_key)

        decision = monitor.update(model_id, result)
        if decision.verdict == Verdict.ABORT_ALL:
            abort_reason = decision.reason
            is_global_abort = True
            print(f"\n  *** ABORT ALL: {decision.reason}")
            break
        if decision.verdict == Verdict.ABORT_MODEL:
            abort_reason = decision.reason
            print(f"\n  *** ABORT MODEL: {decision.reason}")
            break

    aggregates = print_results(case_results)
    summary = _build_model_summary(model_id, aggregates, case_results, abort_reason, monitor)
    summary["_model_dir"] = str(model_dir)

    public_summary = {k: v for k, v in summary.items() if not k.startswith("_")}
    (model_dir / "summary.json").write_text(
        json.dumps(public_summary, indent=2) + "\n", encoding="utf-8",
    )

    return summary, is_global_abort


def _print_case_result(result: dict) -> None:
    """Print one-line case outcome."""
    status = result.get("status", "?")
    if status == "completed":
        hit = "HIT" if result.get("hit_at_5") else "---"
        rank = result.get("hit_rank", "")
        rank_str = f"@{rank}" if rank else ""
        p2b = " +2b" if result.get("phase2b_triggered") else ""
        print(f"{hit}{rank_str}{p2b}  ({result.get('elapsed_ms', 0):.0f}ms)")
    else:
        print(f"{status.upper()} ({result.get('reason', '')})")


def _build_model_summary(
    model_id: str, aggregates: dict, case_results: list[dict],
    abort_reason: str, monitor: EarlyStopMonitor,
) -> dict:
    """Build the per-model summary dict."""
    stats = monitor.model_summary(model_id)
    return {
        "checkpoint": "scaled-eval-v42",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": model_id,
        "abort_reason": abort_reason,
        "aggregates": aggregates,
        "monitor_stats": stats,
        "cases": case_results,
    }


# ---------------------------------------------------------------------------
# Comparison report (subtask 4)
# ---------------------------------------------------------------------------

def generate_comparison(model_summaries: list[dict], run_dir: Path) -> None:
    """Produce comparison.json and print ASCII table."""
    rows = []
    for s in model_summaries:
        agg = s.get("aggregates", {})
        stats = s.get("monitor_stats", {})
        n = agg.get("n_completed", 0)
        rows.append({
            "model": s["model"],
            "n_completed": n,
            "hit_at_5": agg.get("hit_at_5", 0) / n if n else 0,
            "hit_at_5_count": agg.get("hit_at_5", 0),
            "mrr": agg.get("mrr", 0),
            "avg_latency_s": stats.get("avg_latency_s", 0),
            "total_cost": stats.get("total_cost", 0),
            "cost_per_case": stats.get("total_cost", 0) / n if n else 0,
            "abort_reason": s.get("abort_reason", ""),
        })

    comparison = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "models_evaluated": len(rows),
        "results": rows,
    }
    comp_path = run_dir / "comparison.json"
    comp_path.write_text(json.dumps(comparison, indent=2) + "\n", encoding="utf-8")

    _print_comparison_table(rows)
    print(f"\nComparison saved to {comp_path}")


def _print_comparison_table(rows: list[dict]) -> None:
    """Print formatted ASCII comparison table."""
    print(f"\n{'='*100}")
    print("MULTI-MODEL COMPARISON")
    print(f"{'='*100}")
    header = (
        f"{'Model':<25s} {'N':>4s} {'Hit@5':>7s} {'MRR':>7s} "
        f"{'Lat(s)':>7s} {'$/case':>8s} {'Total$':>8s} {'Abort':>20s}"
    )
    print(header)
    print("-" * 100)
    for r in rows:
        hit_str = f"{r['hit_at_5']:.3f}" if r['n_completed'] else "N/A"
        mrr_str = f"{r['mrr']:.4f}" if r['n_completed'] else "N/A"
        abort = r.get("abort_reason", "")[:20]
        print(
            f"{r['model']:<25s} {r['n_completed']:>4d} {hit_str:>7s} {mrr_str:>7s} "
            f"{r['avg_latency_s']:>7.1f} {r['cost_per_case']:>8.4f} "
            f"{r['total_cost']:>8.4f} {abort:>20s}"
        )
    print(f"{'='*100}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scaled multi-model V4.2 eval")
    parser.add_argument(
        "--models", type=str, default=None,
        help=f"Comma-separated model IDs (default: {','.join(DEFAULT_MODELS)})",
    )
    parser.add_argument("--max-n", type=int, default=None, help="Max cases per model")
    parser.add_argument("--resume", type=str, default=None, help="Path to previous run to resume")
    args = parser.parse_args()

    models = args.models.split(",") if args.models else DEFAULT_MODELS

    if args.resume:
        run_dir = Path(args.resume)
        if not run_dir.is_absolute():
            run_dir = RESULTS_DIR / args.resume
    else:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
        run_dir = RESULTS_DIR / f"{ts}-scaled-eval"
        run_dir.mkdir(parents=True, exist_ok=True)

    checkpoint = _load_checkpoint(run_dir)
    monitor = EarlyStopMonitor()
    if checkpoint.get("completed_models"):
        monitor.replay_checkpoint(checkpoint["completed_models"])
        print(f"  Resumed monitor state: ${monitor.global_cost:.4f} global cost")

    print("Loading ground truth graph...")
    gt = GroundTruthGraph.from_replication_zip(str(ZIP_PATH))

    print("Building eval cases...")
    cases = build_eval_cases(gt, max_n=args.max_n)
    print(f"  {len(cases)} cases resolved")
    print(f"  Models: {', '.join(models)}")
    print(f"  Run dir: {run_dir}")

    write_run_config(
        run_dir, label="scaled-eval", models=models,
        n=len(cases), seed=42, pipeline="v4.2",
    )

    model_summaries: list[dict] = []
    global_abort = False

    for model_id in models:
        if model_id in checkpoint.get("completed_models", {}) \
                and checkpoint["completed_models"][model_id].get("finished"):
            print(f"\n  SKIP {model_id} (completed in checkpoint)")
            summary_path = _find_model_summary(model_id)
            if summary_path:
                model_summaries.append(json.loads(summary_path.read_text(encoding="utf-8")))
            continue

        try:
            summary, hit_global = _run_model(model_id, cases, run_dir, monitor, checkpoint)
        except Exception as exc:
            print(f"\n  *** MODEL {model_id} FAILED: {exc}")
            summary = {
                "model": model_id, "abort_reason": f"exception: {exc}",
                "aggregates": {}, "monitor_stats": {}, "cases": [],
            }
            hit_global = False

        model_summaries.append(summary)

        checkpoint.setdefault("completed_models", {})[model_id] = {
            "finished": not summary.get("abort_reason"),
            "model_dir": str(summary.get("_model_dir", "")),
            "completed_keys": [c.get("issue_key") for c in summary.get("cases", [])
                               if c.get("status") == "completed"],
            "case_results": summary.get("cases", []),
        }
        checkpoint["global_cost"] = monitor.global_cost
        _save_checkpoint(run_dir, checkpoint)

        if hit_global:
            global_abort = True
            print("\n*** GLOBAL BUDGET EXCEEDED — skipping remaining models ***")
            break

    generate_comparison(model_summaries, run_dir)

    if global_abort:
        print(f"\nRun aborted due to global budget. Resume with: --resume {run_dir.name}")


def _find_model_summary(model_id: str) -> Path | None:
    """Find the most recent summary.json for a model in results/."""
    safe = model_id.replace("/", "-").replace(":", "-")
    candidates = sorted(RESULTS_DIR.glob(f"*scaled-eval-{safe}*/summary.json"), reverse=True)
    return candidates[0] if candidates else None


if __name__ == "__main__":
    main()
