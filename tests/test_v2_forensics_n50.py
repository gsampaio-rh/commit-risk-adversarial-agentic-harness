"""Tests for v2-forensics-n50 deterministic forensics report.

Integration tests that run the script and validate its outputs.
Unit tests for generic forensics functions live alongside src/eval/forensics.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "v2_forensics_n50.py"
OUTPUT_JSON = ROOT / ".harness" / "evals" / "v2-forensics-n50.json"
OUTPUT_MD = ROOT / ".harness" / "evals" / "v2-forensics-n50.md"

pytestmark = pytest.mark.skipif(
    not OUTPUT_JSON.is_file(),
    reason="run scripts/v2_forensics_n50.py to generate reports first",
)


def test_forensics_report_validates() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--validate"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "AC-1 through AC-6 PASS" in result.stdout


def test_forensics_json_has_fifty_commits() -> None:
    report = json.loads(OUTPUT_JSON.read_text())
    assert report["meta"]["n_commits"] == 50
    assert len(report["commits"]) == 50


def test_forensics_md_has_six_sections() -> None:
    headings = [ln for ln in OUTPUT_MD.read_text().splitlines() if ln.startswith("## ")]
    assert headings == [
        "## Executive Summary",
        "## D3 Failure Taxonomy",
        "## D1 FP Analysis",
        "## D2 Gap",
        "## Priority Matrix",
        "## Hard-Commit Panel",
    ]


def test_forensics_fp_detection_is_data_driven() -> None:
    """FP commits must be identified by d1_tag==false_positive, not hardcoded sets."""
    report = json.loads(OUTPUT_JSON.read_text())
    fp_entries = [c for c in report["commits"] if c["tags"]["d1_tag"] == "false_positive"]
    assert len(fp_entries) == 8
    for entry in fp_entries:
        assert not entry["buggy"]
        assert entry["pipeline"]["archetype_detected"] is not None


def test_forensics_d3_zero_tags_exhaustive() -> None:
    report = json.loads(OUTPUT_JSON.read_text())
    valid_tags = {"wrong-mechanism", "missing-context", "judge-infra", "data_missing"}
    d3_zero = [c for c in report["commits"] if c["buggy"] and c["scores"].get("D3") == 0.0]
    for c in d3_zero:
        assert c["tags"]["d3_tag"] in valid_tags, f"{c['commit_prefix']} has unexpected tag"
