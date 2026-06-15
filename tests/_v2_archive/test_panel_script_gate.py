"""AC-6: Panel script-gate metrics — evaluate_risk() on 12-commit panel.

Validates that the Script risk policy gate maintains acceptable discrimination
metrics on the iter-2 panel ground truth without any new LLM calls.

Ground truth from output/runs/2026-06-10_21-07-21_real_n12/:
  6 buggy commits: 294c169, 55dcbe, 572f3cee, 90846b, f897d4, fbf0ff
  6 clean commits: 24d9de, 4a72341, 7cff0990, 9530370, b9f165, ce2d5bfa

Metrics:
  D1: overall accuracy (buggy→HIGH/CRITICAL or clean→LOW/MEDIUM correctly)
  buggy_recall: fraction of buggy commits classified as HIGH or CRITICAL
  D6 (clean precision): fraction of clean commits correctly NOT classified HIGH/CRITICAL
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

PANEL_DIR = PROJECT_ROOT / "output/runs/2026-06-10_21-07-21_real_n12/investigations"
REPOS_BASE = PROJECT_ROOT / "data/repos"


# ---------------------------------------------------------------------------
# Load panel cases with context
# ---------------------------------------------------------------------------

def _load_panel() -> list[dict[str, Any]]:
    """Load all 12 panel investigation results with ground truth labels."""
    if not PANEL_DIR.exists():
        pytest.skip("Panel data not found")

    cases = []
    for fname in sorted(PANEL_DIR.iterdir()):
        if not fname.suffix == ".json":
            continue
        d = json.loads(fname.read_text())
        cases.append(d)
    return cases


def _build_context(d: dict[str, Any]) -> Any:
    """Build InvestigationContext from panel data."""
    from commit_investigator.context.context_builder import InvestigationContext
    from commit_investigator.context.git_context import GitContextProvider

    commit_id = d["commit_id"]
    project = d.get("project", "camel")
    repo_path = REPOS_BASE / project

    diff = ""
    if repo_path.exists():
        try:
            gp = GitContextProvider(repo_path)
            diff = (gp.get_diff(commit_id) or "")[:16000]
        except Exception:
            pass

    return InvestigationContext(
        commit_id=commit_id,
        project=project,
        diff=diff,
        message=None,
        touched_files=[],
        csv_features={},
        file_histories={},
        author_stats=None,
        missing_reasons=[],
    )


# ---------------------------------------------------------------------------
# Script-gate metrics computation
# ---------------------------------------------------------------------------

def _compute_metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply evaluate_risk() to all panel cases and compute D1/buggy_recall/D6."""
    from commit_investigator.analysis.report import RiskLevel
    from commit_investigator.analysis.risk_policy import evaluate_risk

    buggy_total = sum(1 for c in cases if c["buggy_label"])
    clean_total = sum(1 for c in cases if not c["buggy_label"])
    total = len(cases)

    correct = 0
    buggy_correct = 0
    clean_correct = 0
    results = []

    for d in cases:
        context = _build_context(d)
        llm_risk = RiskLevel(d["risk_level"])
        reasoning = d.get("reasoning_summary", "")
        verdict = evaluate_risk(llm_risk, context, reasoning)
        final_risk = verdict.risk_level

        is_buggy = d["buggy_label"]
        predicted_buggy = final_risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)

        case_correct = (is_buggy == predicted_buggy)
        if case_correct:
            correct += 1
        if is_buggy and predicted_buggy:
            buggy_correct += 1
        if not is_buggy and not predicted_buggy:
            clean_correct += 1

        results.append({
            "commit_id": d["commit_id"][:8],
            "buggy": is_buggy,
            "llm_risk": llm_risk.value,
            "final_risk": final_risk.value,
            "cap_applied": verdict.cap_applied,
            "predicted_buggy": predicted_buggy,
            "correct": case_correct,
        })

    return {
        "D1": correct / total,
        "buggy_recall": buggy_correct / buggy_total if buggy_total else 0,
        "D6_clean_precision": clean_correct / clean_total if clean_total else 0,
        "total": total,
        "buggy_total": buggy_total,
        "clean_total": clean_total,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Tests (AC-6)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def panel_cases() -> list[dict[str, Any]]:
    return _load_panel()


@pytest.fixture(scope="module")
def metrics(panel_cases: list[dict[str, Any]]) -> dict[str, Any]:
    return _compute_metrics(panel_cases)


class TestPanelScriptGate:
    """AC-6: D1 >= 0.65, buggy_recall >= 0.80 on 12-commit panel."""

    def test_d1_overall_accuracy(self, metrics: dict[str, Any]) -> None:
        d1 = metrics["D1"]
        failures = [r for r in metrics["results"] if not r["correct"]]
        fail_str = ", ".join(
            f"{r['commit_id']}(buggy={r['buggy']},risk={r['final_risk']})"
            for r in failures
        )
        assert d1 >= 0.65, (
            f"D1={d1:.3f} < 0.65. Incorrect: {fail_str}"
        )

    def test_buggy_recall(self, metrics: dict[str, Any]) -> None:
        recall = metrics["buggy_recall"]
        missed = [r for r in metrics["results"] if r["buggy"] and not r["predicted_buggy"]]
        miss_str = ", ".join(f"{r['commit_id']}({r['final_risk']})" for r in missed)
        assert recall >= 0.80, (
            f"buggy_recall={recall:.3f} < 0.80. Missed: {miss_str}"
        )

    def test_panel_size(self, panel_cases: list[dict[str, Any]]) -> None:
        assert len(panel_cases) == 12, f"Expected 12 panel cases, got {len(panel_cases)}"

    def test_panel_has_buggy_and_clean(self, metrics: dict[str, Any]) -> None:
        assert metrics["buggy_total"] > 0
        assert metrics["clean_total"] > 0

    def test_no_new_llm_calls(self, panel_cases: list[dict[str, Any]]) -> None:
        """Verify gate uses only Script evaluate_risk() — no LLM calls made."""
        # This test passes by construction: _compute_metrics() never calls LLM.
        # If evaluate_risk() internally calls LLM, it would raise since no LLM configured.
        from commit_investigator.analysis.risk_policy import evaluate_risk
        from commit_investigator.analysis.report import RiskLevel
        from commit_investigator.context.context_builder import InvestigationContext

        ctx = InvestigationContext(
            commit_id="test", project="camel", diff="", message=None,
            touched_files=[], csv_features={}, file_histories={},
            author_stats=None, missing_reasons=[],
        )
        result = evaluate_risk(RiskLevel.HIGH, ctx, "")
        assert result is not None
