"""V2 n=50 per-commit failure taxonomy — wrapper for the generic forensics engine.

Provides n=50-run-specific defaults and count-level validation assertions.
Generic logic lives in src/eval/forensics.py.

Usage:
  python scripts/v2_forensics_n50.py
  python scripts/v2_forensics_n50.py --validate
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from eval.forensics import (
    build_report,
    render_markdown,
    validate_report_structure,
)

# ---------------------------------------------------------------------------
# n=50-run-specific configuration
# ---------------------------------------------------------------------------

DEFAULT_DELIVERY = ".harness/evals/eval-iter-n50-delivery.json"
DEFAULT_RUN_DIR = "output/runs/2026-06-11_06-13-50_real_n50"
DEFAULT_OUTPUT_JSON = ".harness/evals/v2-forensics-n50.json"
DEFAULT_OUTPUT_MD = ".harness/evals/v2-forensics-n50.md"
RUN_LABEL = "n=50"

# Manually curated set: commits where missing context is known (not derivable from truncation alone)
KNOWN_CONTEXT_GAP: frozenset[str] = frozenset({"572f3cee35fe"})

V2_TARGETS = {"D1": 0.80, "D2": 0.25, "D3": 0.35, "D4": 0.75, "D5": 0.40, "D6": 0.70}

PRIORITY_MATRIX: list[dict] = [
    {
        "rank": 1,
        "dimension": "D3",
        "intervention": "contrastive hypothesis + primary mechanism selection",
        "expected_delta": "+0.08 to +0.12 on D3",
        "task_id": "v2-d3-contrastive-hypothesis",
    },
    {
        "rank": 2,
        "dimension": "D1",
        "intervention": "risk_policy: ≥2 SUPPORTED OR defect_signal for HIGH on clean archetypes",
        "expected_delta": "+0.08 to +0.10 on D1",
        "task_id": "v2-fp-risk-tightening",
    },
    {
        "rank": 3,
        "dimension": "D2",
        "intervention": "SUPPORTED-only localization + defect-signal file ranking",
        "expected_delta": "+0.05 to +0.08 on D2",
        "task_id": "v2-d2-localization-precision",
    },
    {
        "rank": 4,
        "dimension": "D3",
        "intervention": "EXP-BUNDLE-EXPAND (test adjacency + blame) on failure subset",
        "expected_delta": "+0.04 to +0.08 on D3 hard commits",
        "task_id": "EXP-BUNDLE-EXPAND",
    },
    {
        "rank": 5,
        "dimension": "D1",
        "intervention": "stricter SUPPORTED verification in evidence_tagger",
        "expected_delta": "+0.03 to +0.05 on D1",
        "task_id": "v2-evidence-strict-supported",
    },
]


# ---------------------------------------------------------------------------
# n=50-specific count validation (on top of generic structure validation)
# ---------------------------------------------------------------------------

def validate_n50_counts(report: dict) -> None:
    """Assert n=50-specific counts derived from this run's ground truth."""
    assert report["meta"]["n_commits"] == 50, f"expected 50, got {report['meta']['n_commits']}"
    assert len(report["commits"]) == 50

    d3_zero = [c for c in report["commits"] if c["buggy"] and c["scores"].get("D3") == 0.0]
    assert len(d3_zero) == 11, f"expected 11 D3=0 buggy commits, got {len(d3_zero)}"

    data_missing = [c for c in d3_zero if c["tags"]["d3_tag"] == "data_missing"]
    for c in data_missing:
        assert c["tags"]["d1_tag"] == "data_missing"
        assert c["tags"]["d2_tag"] == "data_missing"
        assert c["pipeline"]["supported_count"] is None

    valid_d3_zero_tags = {"wrong-mechanism", "missing-context", "judge-infra"}
    for c in d3_zero:
        if c["tags"]["d3_tag"] != "data_missing":
            assert c["tags"]["d3_tag"] in valid_d3_zero_tags

    fp_entries = [c for c in report["commits"] if c["tags"]["d1_tag"] == "false_positive"]
    for entry in fp_entries:
        assert entry["pipeline"]["archetype_detected"] is not None
        assert entry["pipeline"]["supported_count"] is not None
        assert entry["pipeline"]["cap_applied"] is not None

    required_tasks = {
        "v2-d3-contrastive-hypothesis",
        "v2-fp-risk-tightening",
        "v2-d2-localization-precision",
        "EXP-BUNDLE-EXPAND",
        "v2-evidence-strict-supported",
    }
    assert len(report["priority_matrix"]) == 5
    assert {row["task_id"] for row in report["priority_matrix"]} == required_tasks
    for row in report["priority_matrix"]:
        assert row.get("expected_delta")
        assert re.search(r"D[123]", str(row["expected_delta"]))

    panel = report["hard_commit_panel"]
    assert len(panel["tier_1_d3_zero"]) == 11
    assert len(panel["tier_2_d3_partial"]) == 7
    assert panel["union_count"] == 18

    tier2_prefixes = set(panel["tier_2_d3_partial"])
    for c in report["commits"]:
        if c["commit_prefix"] in tier2_prefixes:
            d3 = c["scores"].get("D3")
            assert d3 is not None and 0.25 <= d3 < 0.50


def validate_md_sections(md_path: Path) -> None:
    headings = [ln for ln in md_path.read_text().splitlines() if ln.startswith("## ")]
    expected = [
        "## Executive Summary",
        "## D3 Failure Taxonomy",
        "## D1 FP Analysis",
        "## D2 Gap",
        "## Priority Matrix",
        "## Hard-Commit Panel",
    ]
    assert headings == expected, f"headings mismatch: {headings}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="V2 n=50 failure forensics")
    parser.add_argument("--delivery-json", default=DEFAULT_DELIVERY)
    parser.add_argument("--run-dir", default=DEFAULT_RUN_DIR)
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--repos-root", default="data/repos")
    parser.add_argument("--validate", action="store_true", help="Validate existing report")
    args = parser.parse_args()

    output_json = ROOT / args.output_json
    output_md = ROOT / args.output_md

    if args.validate:
        if not output_json.is_file():
            print(f"ERROR: {output_json} not found — run without --validate first", file=sys.stderr)
            return 1
        report = json.loads(output_json.read_text())
        validate_report_structure(report)
        validate_n50_counts(report)
        if not output_md.is_file():
            print(f"ERROR: {output_md} not found", file=sys.stderr)
            return 1
        validate_md_sections(output_md)
        print("validate: AC-1 through AC-6 PASS")
        return 0

    report = build_report(
        ROOT / args.delivery_json,
        ROOT / args.run_dir,
        ROOT / args.repos_root,
        priority_matrix=PRIORITY_MATRIX,
        task_label="v2-forensics-n50",
        targets=V2_TARGETS,
        known_context_gap=KNOWN_CONTEXT_GAP,
    )
    validate_report_structure(report)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2) + "\n")
    output_md.write_text(render_markdown(report, run_label=RUN_LABEL))
    print(f"Wrote {output_json} and {output_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
