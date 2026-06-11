"""D3 Layer 1 experiment — baseline vs H1H4T3 on hard panel (n=18).

Measures whether H1 (symptom-first prompt) + H4 (changed-line forcing)
+ T3 (mechanism evaluator loop) improves mechanism grounding on the
wrong-mechanism hard panel from the v2-forensics-n50 spike.

D3 proxy: fraction of SUPPORTED mechanisms that cite a +/- changed line.
True D3 (eval judge) requires running the full eval harness separately.

Usage:
  # Validate panel without LLM calls
  python scripts/run_d3_experiment_layer1.py --dry-run

  # Run live experiment (requires LLM API key + data/repos/)
  python scripts/run_d3_experiment_layer1.py \\
    --forensics-json .harness/evals/v2-forensics-n50.json \\
    --csv data/apachejit/apachejit_test_small.csv \\
    --repos-dir data/repos

  # Compare two pre-scored eval run dirs (true D3 scores)
  python scripts/run_d3_experiment_layer1.py \\
    --baseline-run-dir output/runs/<baseline_dir> \\
    --variant-run-dir output/runs/<h1h4t3_dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
DEFAULT_FORENSICS = ROOT / ".harness/evals/v2-forensics-n50.json"
DEFAULT_CSV = ROOT / "data/apachejit/apachejit_test_small.csv"
DEFAULT_REPOS = ROOT / "data/repos"

# Project name mapping: CSV project → repos dir name
PROJECT_DIR_MAP = {
    "apache/camel": "camel",
    "apache/hadoop": "hadoop",
    "camel": "camel",
    "hadoop": "hadoop",
}


def load_hard_panel(forensics_path: Path) -> list[str]:
    """Load sorted union of tier_1 + tier_2 commit prefixes."""
    report = json.loads(forensics_path.read_text())
    panel = report["hard_commit_panel"]
    tier1 = panel["tier_1_d3_zero"]
    tier2 = panel["tier_2_d3_partial"]
    return sorted(set(tier1) | set(tier2))


def load_cached_d3_scores(forensics_path: Path) -> dict[str, float]:
    """Map commit_prefix → baseline D3 from the forensics n=50 run."""
    report = json.loads(forensics_path.read_text())
    scores: dict[str, float] = {}
    for commit in report.get("commits", []):
        prefix = commit.get("commit_prefix", "")
        d3 = commit.get("scores", {}).get("D3")
        if prefix and d3 is not None:
            scores[prefix] = float(d3)
    return scores


def load_commit_metadata(forensics_path: Path) -> dict[str, dict[str, Any]]:
    """Map commit_prefix → {commit_id, project} from forensics."""
    report = json.loads(forensics_path.read_text())
    meta: dict[str, dict[str, Any]] = {}
    for commit in report.get("commits", []):
        prefix = commit.get("commit_prefix", "")
        if prefix:
            meta[prefix] = {
                "commit_id": commit.get("commit_id", prefix),
                "project": commit.get("project", ""),
            }
    return meta


def load_csv_rows(csv_path: Path) -> dict[str, dict[str, str]]:
    """Map commit_id → CSV row dict."""
    rows: dict[str, dict[str, str]] = {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows[row["commit_id"]] = row
    return rows


def find_csv_row(
    csv_rows: dict[str, dict[str, str]], commit_id: str
) -> dict[str, str] | None:
    """Match commit_id (possibly partial prefix) to a CSV row."""
    if commit_id in csv_rows:
        return csv_rows[commit_id]
    for key in csv_rows:
        if key.startswith(commit_id) or commit_id.startswith(key):
            return csv_rows[key]
    return None


def compute_h4_compliance(report: Any) -> float:
    """Proxy D3: fraction of evidence items whose content cites a +/- line.

    Uses _has_changed_line_citation from hypothesis_engine.
    Higher compliance = more mechanisms grounded in actual changed lines.
    """
    from commit_investigator.hypothesis_engine import _has_changed_line_citation

    items = getattr(report, "evidence", [])
    if not items:
        return 0.0
    grounded = sum(
        1 for item in items
        if _has_changed_line_citation(getattr(item, "content", ""))
    )
    return grounded / len(items)


def run_variant_on_panel(
    panel: list[str],
    commit_meta: dict[str, dict[str, Any]],
    csv_rows: dict[str, dict[str, str]],
    repos_dir: Path,
    enable_mechanism_evaluator: bool,
) -> dict[str, float]:
    """Run one orchestrator variant on all hard panel commits.

    Returns dict: commit_prefix → H4-compliance score (proxy D3).
    """
    import os
    sys.path.insert(0, str(ROOT / "src"))
    from commit_investigator.context_builder import (
        AuthorStatsIndex,
        CommitContextBuilder,
    )
    from commit_investigator.git_context import GitContextProvider
    from commit_investigator.orchestrator import AgentOrchestrator

    scores: dict[str, float] = {}
    git_providers: dict[str, GitContextProvider] = {}

    orchestrator = AgentOrchestrator(
        enable_mechanism_evaluator=enable_mechanism_evaluator,
        max_turns=1,
    )

    variant_label = "h1h4t3" if enable_mechanism_evaluator else "baseline"
    print(f"  Running variant={variant_label} on {len(panel)} commits...",
          file=sys.stderr)

    for prefix in panel:
        meta = commit_meta.get(prefix, {})
        commit_id = meta.get("commit_id", prefix)
        project_csv = meta.get("project", "")
        repo_name = PROJECT_DIR_MAP.get(project_csv, project_csv.split("/")[-1])

        if repo_name not in git_providers:
            repo_path = repos_dir / repo_name
            if repo_path.exists():
                git_providers[repo_name] = GitContextProvider(str(repo_path))

        git_provider = git_providers.get(repo_name)
        csv_row = find_csv_row(csv_rows, commit_id)

        try:
            report = orchestrator.investigate(
                commit_id=commit_id,
                project=repo_name,
                csv_row=dict(csv_row) if csv_row else None,
                git_provider=git_provider,
            )
            scores[prefix] = compute_h4_compliance(report)
            print(f"    {prefix} [{variant_label}] H4={scores[prefix]:.2f}",
                  file=sys.stderr)
        except Exception as exc:
            print(f"    {prefix} [{variant_label}] ERROR: {exc}", file=sys.stderr)
            scores[prefix] = 0.0

        time.sleep(0.5)

    return scores


def load_d3_from_run_dir(run_dir: Path, panel: list[str]) -> dict[str, float]:
    """Load D3 scores from a pre-existing eval run directory."""
    eval_report_path = run_dir / "eval-report.json"
    if not eval_report_path.exists():
        print(f"ERROR: eval-report.json not found in {run_dir}", file=sys.stderr)
        return {}
    report = json.loads(eval_report_path.read_text())
    scores: dict[str, float] = {}
    for result in report.get("results", []):
        commit_id = result.get("commit_id", "")
        prefix = commit_id[:12]
        if prefix in panel:
            d3 = result.get("scores", {}).get("D3", {})
            scores[prefix] = float(d3.get("score", 0.0) if isinstance(d3, dict) else d3)
    return scores


def print_results_table(
    panel: list[str],
    baseline_scores: dict[str, float],
    variant_scores: dict[str, float],
    score_label: str = "D3",
) -> None:
    """Print commit | baseline | variant | delta table and aggregates."""
    b_label = f"baseline_{score_label}"
    v_label = f"h1h4t3_{score_label}"
    print(f"commit_prefix | {b_label} | {v_label} | delta")
    deltas: list[float] = []
    n_improved = n_regressed = 0

    for prefix in panel:
        baseline = baseline_scores.get(prefix, 0.0)
        variant = variant_scores.get(prefix, baseline)
        delta = variant - baseline
        deltas.append(delta)
        if delta > 0:
            n_improved += 1
        elif delta < 0:
            n_regressed += 1
        print(f"{prefix} | {baseline:.2f} | {variant:.2f} | {delta:+.2f}")

    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
    print(
        f"aggregate: n={len(panel)} n_improved={n_improved} "
        f"n_regressed={n_regressed} mean_delta_{score_label}={mean_delta:+.3f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="D3 Layer 1 hard-panel experiment: baseline vs H1H4T3"
    )
    parser.add_argument(
        "--forensics-json", default=str(DEFAULT_FORENSICS),
        help="Forensics report with hard_commit_panel",
    )
    parser.add_argument(
        "--csv", default=str(DEFAULT_CSV),
        help="ApacheJIT CSV with commit features",
    )
    parser.add_argument(
        "--repos-dir", default=str(DEFAULT_REPOS),
        help="Directory containing 'camel' and 'hadoop' git repos",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="No LLM calls; validate panel and print baseline-only table",
    )
    parser.add_argument(
        "--baseline-run-dir", default=None,
        help="Pre-scored eval run dir for baseline (uses true D3 scores)",
    )
    parser.add_argument(
        "--variant-run-dir", default=None,
        help="Pre-scored eval run dir for H1H4T3 variant (uses true D3 scores)",
    )
    args = parser.parse_args()

    forensics_path = Path(args.forensics_json)
    if not forensics_path.is_file():
        print(f"ERROR: forensics JSON not found: {forensics_path}", file=sys.stderr)
        return 1

    panel = load_hard_panel(forensics_path)
    if len(panel) != 18:
        print(f"ERROR: expected 18 hard-panel commits, got {len(panel)}", file=sys.stderr)
        return 1

    cached_baseline = load_cached_d3_scores(forensics_path)

    # Mode 1: dry-run — validate panel, show baseline-only table
    if args.dry_run:
        variant_scores = dict(cached_baseline)
        print_results_table(panel, cached_baseline, variant_scores)
        return 0

    # Mode 2: compare two pre-scored run directories (true D3)
    if args.baseline_run_dir and args.variant_run_dir:
        baseline_dir = Path(args.baseline_run_dir)
        variant_dir = Path(args.variant_run_dir)
        baseline_d3 = load_d3_from_run_dir(baseline_dir, panel)
        variant_d3 = load_d3_from_run_dir(variant_dir, panel)
        if not baseline_d3 or not variant_d3:
            print("ERROR: could not load D3 scores from run dirs", file=sys.stderr)
            return 1
        print_results_table(panel, baseline_d3, variant_d3, score_label="D3")
        return 0

    # Mode 3: live experiment — run both variants, use H4-compliance as D3 proxy
    csv_path = Path(args.csv)
    repos_dir = Path(args.repos_dir)

    if not csv_path.is_file():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        return 1
    if not repos_dir.is_dir():
        print(f"ERROR: repos dir not found: {repos_dir}", file=sys.stderr)
        return 1

    sys.path.insert(0, str(ROOT / "src"))

    commit_meta = load_commit_metadata(forensics_path)
    csv_rows = load_csv_rows(csv_path)

    print("Running BASELINE variant...", file=sys.stderr)
    baseline_h4 = run_variant_on_panel(
        panel, commit_meta, csv_rows, repos_dir,
        enable_mechanism_evaluator=False,
    )

    print("Running H1H4T3 variant...", file=sys.stderr)
    variant_h4 = run_variant_on_panel(
        panel, commit_meta, csv_rows, repos_dir,
        enable_mechanism_evaluator=True,
    )

    print("\n# H4-compliance proxy (fraction of evidence citing +/- changed lines)")
    print("# True D3 requires: python -m commit_investigator.run_eval --commit-ids <panel>")
    print_results_table(panel, baseline_h4, variant_h4, score_label="H4_proxy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
