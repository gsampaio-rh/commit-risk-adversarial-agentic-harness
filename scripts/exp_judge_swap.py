"""EXP-JUDGE-SWAP: Cross-model judge validation experiment.

Re-scores ≥10 buggy reports from iter-2 n=20 using a non-claude-sonnet judge
and computes D3 delta to determine if a split-judge protocol is required.

Decision rule:
  - Mean D3 drop ≥0.03 OR ≥2 gate-flipping commits → mandate split judge
  - Otherwise → same-model-acceptable

Usage:
  cd /path/to/workspace
  CURSOR_API_KEY=... python3 scripts/exp_judge_swap.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from commit_investigator.eval_judge import ReasoningJudge
from commit_investigator.ground_truth import GroundTruthGraph
from commit_investigator.jira_client import JiraClient, JiraClientError, JiraIssue
from commit_investigator.llm import CursorSDKProvider
from commit_investigator.report import (
    CommitInvestigationReport,
    EvidenceItem,
    EvidenceType,
    LocalizationClaim,
    RiskAssessment,
    RiskLevel,
)

# Iter-2 n=20 run with same-model (claude-sonnet-4-6) D3 scores
ITER2_RUN = "output/runs/2026-06-10_17-12-55_real_n20"
APACHEJIT_ZIP = "data/apachejit/apachejit_dataset_replication.zip"
JIRA_CACHE = "data/jira_cache"
OUTPUT_PATH = ".harness/evals/exp-judge-swap.json"

# Cross-model judge to use (must differ from claude-sonnet-4-6)
CROSS_MODEL = "claude-haiku-4-5"

# D3 gate threshold: score >= 0.5 (normalized) = pass
D3_GATE_THRESHOLD = 0.5


@dataclass
class CommitSwapResult:
    commit_id: str
    project: str
    jira_key: str | None
    has_jira_description: bool
    same_model_d3: float
    cross_model_d3: float
    d3_delta: float  # cross - same
    same_model_gate: bool  # pass/fail at threshold
    cross_model_gate: bool
    gate_flip: bool
    same_model_justification: str
    cross_model_justification: str


def _load_investigation_reports(run_dir: Path) -> dict[str, dict]:
    """Load all investigation JSON files from a run directory."""
    inv_dir = run_dir / "investigations"
    reports = {}
    for fp in inv_dir.glob("*.json"):
        d = json.load(fp.open())
        commit_id = d.get("commit_id", "")
        if commit_id:
            reports[commit_id[:12]] = d
    return reports


def _rebuild_report(d: dict) -> CommitInvestigationReport:
    """Reconstruct a CommitInvestigationReport from investigation JSON."""
    locs = [
        LocalizationClaim(
            file=loc.get("file", ""),
            lines=loc.get("lines"),
            rationale=loc.get("rationale", ""),
        )
        for loc in d.get("localization", [])
    ]

    evidence = []
    for ev in d.get("evidence", []):
        try:
            ev_type = EvidenceType(ev.get("type", "diff_hunk"))
        except ValueError:
            ev_type = EvidenceType.DIFF_HUNK
        evidence.append(EvidenceItem(
            type=ev_type,
            source=ev.get("source", ""),
            content=ev.get("content", ""),
            relevance=ev.get("relevance", ""),
        ))

    try:
        risk_level = RiskLevel(d.get("risk_level", "MEDIUM"))
    except ValueError:
        risk_level = RiskLevel.MEDIUM

    return CommitInvestigationReport(
        commit_id=d.get("commit_id", ""),
        project=d.get("project", ""),
        risk_assessment=RiskAssessment(
            level=risk_level,
            confidence=d.get("confidence", 0.5),
        ),
        evidence=evidence,
        findings=d.get("findings", []),
        localization=locs,
        reasoning_summary=d.get("reasoning_summary", ""),
        recommendations=[],
        turn_count=d.get("turn_count", 1),
    )


def _get_jira_issue(
    jira_client: JiraClient,
    gt: GroundTruthGraph,
    commit_id: str,
) -> tuple[JiraIssue | None, str | None]:
    """Fetch the JIRA issue for a commit. Returns (issue, issue_key)."""
    chain = gt.get_chain(commit_id)
    for key in chain.issue_keys:
        try:
            issue = jira_client.get_issue(key)
            return issue, key
        except JiraClientError:
            continue
    return None, None


def run_experiment() -> dict:
    api_key = os.environ.get("CURSOR_API_KEY", "")
    if not api_key:
        raise ValueError("CURSOR_API_KEY not set")

    print(f"EXP-JUDGE-SWAP: same-model=claude-sonnet-4-6 vs cross-model={CROSS_MODEL}")

    # Load iter-2 eval report (existing D3 scores from same-model judge)
    run_dir = Path(ITER2_RUN)
    eval_report = json.load((run_dir / "eval-report.json").open())
    # Get buggy commits with existing D3 scores
    buggy_results = [
        r for r in eval_report["results"]
        if r.get("buggy") and "D3" in r.get("scores", {})
    ]
    print(f"Found {len(buggy_results)} buggy commits with D3 scores")

    # Load investigation reports
    inv_reports = _load_investigation_reports(run_dir)

    # Load ground truth
    gt = GroundTruthGraph.from_replication_zip(APACHEJIT_ZIP)

    # JIRA client (cached)
    jira_client = JiraClient(cache_dir=JIRA_CACHE)

    # Build cross-model judge
    cross_judge = ReasoningJudge(CursorSDKProvider(api_key=api_key, model=CROSS_MODEL))

    swap_results: list[CommitSwapResult] = []

    for br in buggy_results:
        short_id = br["commit_id"][:12]
        same_model_d3 = br["scores"]["D3"]["score"]
        same_model_details = br["scores"]["D3"].get("details", "")
        print(f"\n  Scoring {short_id} (same={same_model_d3:.2f})...")

        # Rebuild CommitInvestigationReport
        inv_json = inv_reports.get(short_id)
        if not inv_json:
            print(f"    [SKIP] No investigation report found for {short_id}")
            continue

        report = _rebuild_report(inv_json)

        # Get JIRA issue
        full_id = inv_json.get("commit_id", short_id)
        jira_issue, jira_key = _get_jira_issue(jira_client, gt, full_id)

        if jira_issue is None:
            # No JIRA — create a stub with empty description so fallback applies
            jira_issue = JiraIssue(
                key=f"UNKNOWN-{short_id}",
                summary=f"Commit {short_id} (no JIRA link)",
                description=None,
                priority=None,
                components=[],
                resolution=None,
                status=None,
            )

        has_desc = bool(jira_issue.description and jira_issue.description.strip())

        # Score with cross-model judge
        try:
            if has_desc:
                cross_result = cross_judge.score_d3_root_cause(report, jira_issue)
            else:
                cross_result = cross_judge.score_d3_root_cause_fix_diff_fallback(
                    report, jira_issue, fix_files=set()
                )
            cross_d3 = cross_result.normalized
            cross_just = cross_result.justification
        except Exception as e:
            print(f"    [ERROR] Cross-model scoring failed: {e}")
            cross_d3 = 0.0
            cross_just = f"Error: {e}"

        delta = cross_d3 - same_model_d3
        same_gate = same_model_d3 >= D3_GATE_THRESHOLD
        cross_gate = cross_d3 >= D3_GATE_THRESHOLD
        flip = same_gate != cross_gate

        print(f"    same={same_model_d3:.2f} cross={cross_d3:.2f} Δ={delta:+.2f} flip={flip}")
        print(f"    cross: {cross_just[:80]}")

        swap_results.append(CommitSwapResult(
            commit_id=short_id,
            project=inv_json.get("project", ""),
            jira_key=jira_key,
            has_jira_description=has_desc,
            same_model_d3=same_model_d3,
            cross_model_d3=cross_d3,
            d3_delta=delta,
            same_model_gate=same_gate,
            cross_model_gate=cross_gate,
            gate_flip=flip,
            same_model_justification=same_model_details[:120],
            cross_model_justification=cross_just[:120],
        ))

        time.sleep(0.5)  # avoid rate limiting

    # Aggregate statistics
    n = len(swap_results)
    if n == 0:
        raise RuntimeError("No commits scored — check run directory and API key")

    mean_same = sum(r.same_model_d3 for r in swap_results) / n
    mean_cross = sum(r.cross_model_d3 for r in swap_results) / n
    mean_delta = mean_cross - mean_same
    flips = [r for r in swap_results if r.gate_flip]

    # Decision rule
    if mean_delta <= -0.03 or len(flips) >= 2:
        decision = "split-judge-required"
        decision_reason = (
            f"mean D3 drop {mean_delta:+.3f} ≥0.03 OR {len(flips)} gate flip(s) ≥2"
        )
    else:
        decision = "same-model-acceptable"
        decision_reason = (
            f"mean D3 delta {mean_delta:+.3f} < 0.03 AND {len(flips)} gate flip(s) < 2"
        )

    print("\n=== EXP-JUDGE-SWAP RESULTS ===")
    print(f"n={n}, mean_same={mean_same:.3f}, mean_cross={mean_cross:.3f}, Δ={mean_delta:+.3f}")
    print(f"Gate flips: {len(flips)} — {[r.commit_id for r in flips]}")
    print(f"Decision: {decision} ({decision_reason})")

    output = {
        "experiment": "EXP-JUDGE-SWAP",
        "same_model": "cursor-sdk/claude-sonnet-4-6",
        "cross_model": f"cursor-sdk/{CROSS_MODEL}",
        "n_scored": n,
        "mean_same_model_d3": round(mean_same, 4),
        "mean_cross_model_d3": round(mean_cross, 4),
        "mean_d3_delta": round(mean_delta, 4),
        "gate_flip_count": len(flips),
        "gate_flip_commits": [r.commit_id for r in flips],
        "decision": decision,
        "decision_reason": decision_reason,
        "decision_rule": "mean D3 drop ≥0.03 OR ≥2 flips → split-judge-required",
        "per_commit": [
            {
                "commit_id": r.commit_id,
                "project": r.project,
                "jira_key": r.jira_key,
                "has_jira_description": r.has_jira_description,
                "same_model_d3": round(r.same_model_d3, 4),
                "cross_model_d3": round(r.cross_model_d3, 4),
                "d3_delta": round(r.d3_delta, 4),
                "same_model_gate_pass": r.same_model_gate,
                "cross_model_gate_pass": r.cross_model_gate,
                "gate_flip": r.gate_flip,
                "same_model_justification": r.same_model_justification,
                "cross_model_justification": r.cross_model_justification,
            }
            for r in swap_results
        ],
    }

    out_path = Path(OUTPUT_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(output, out_path.open("w"), indent=2)
    print(f"\nResults written to {OUTPUT_PATH}")

    return output


if __name__ == "__main__":
    run_experiment()
