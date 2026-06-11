"""Generic forensics report engine for commit investigation eval runs.

Reads a delivery eval JSON + per-commit investigation JSONs from a run directory.
Produces a tagged failure-taxonomy report (JSON + Markdown) for any run.

No hardcoded commit sets. Caller supplies run-specific config (priority_matrix,
known_context_gap, targets) via parameters.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from commit_investigator.archetype import detect_archetype
from commit_investigator.context_builder import InvestigationContext
from commit_investigator.git_context import GitContextProvider, GitRepoNotFoundError

TRUNCATION_ORDER: dict[str, int] = {"heavy": 0, "partial": 1, "none": 2}

_JUDGE_ORACLE_RE = re.compile(r"\[judge_oracle=(jira|fix-diff-fallback|unavailable)\]")
_JUDGE_INFRA_RE = re.compile(r"no description|untestable|cannot score", re.IGNORECASE)
_JACCARD_RE = re.compile(r"Jaccard=([\d.]+)")
_AGENT_FILES_RE = re.compile(r"agent=\[(.*?)\]")
_FIX_FILES_RE = re.compile(r"fix=\[(.*?)\]")
_ARCH_DEPTH_RE = re.compile(r"deadlock|circular.*init|Spring Boot auto-config", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_score_value(scores: dict[str, Any], dim: str) -> float | None:
    entry = scores.get(dim)
    if not entry:
        return None
    return float(entry.get("score", 0.0))


def parse_score_details(scores: dict[str, Any], dim: str) -> str:
    entry = scores.get(dim) or {}
    return str(entry.get("details", ""))


def parse_basename_list(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [part.strip().strip("'\"") for part in raw.split(",") if part.strip()]


def parse_judge_oracle(d3_details: str) -> str | None:
    match = _JUDGE_ORACLE_RE.search(d3_details)
    return match.group(1) if match else None


def parse_jaccard(d2_details: str) -> float | None:
    match = _JACCARD_RE.search(d2_details)
    return float(match.group(1)) if match else None


def parse_agent_fix_files(
    d2_details: str,
) -> tuple[list[str], list[str]]:
    agent_match = _AGENT_FILES_RE.search(d2_details)
    fix_match = _FIX_FILES_RE.search(d2_details)
    agent_files = parse_basename_list(agent_match.group(1)) if agent_match else []
    fix_files = parse_basename_list(fix_match.group(1)) if fix_match else []
    return agent_files, fix_files


def count_supported(recommendations: list[dict[str, Any]]) -> int:
    return sum(
        1
        for rec in recommendations
        if "Hypothesis SUPPORTED" in str(rec.get("rationale", ""))
    )


def truncation_status(trunc_meta: dict[str, Any]) -> str:
    truncated = trunc_meta.get("truncated_files") or []
    total_chars = int(trunc_meta.get("total_chars") or 0)
    if not truncated:
        return "none"
    if len(truncated) >= 10 or total_chars >= 15_000:
        return "heavy"
    return "partial"


# ---------------------------------------------------------------------------
# Archetype inference
# ---------------------------------------------------------------------------

def infer_archetype(
    project: str,
    commit_id: str,
    cap_reason: str | None,
    repos_root: Path,
) -> bool:
    """Return True if commit matches a clean archetype (no production defect signals)."""
    if cap_reason and cap_reason.startswith("clean_archetype"):
        return True
    repo = repos_root / project
    if not repo.is_dir():
        return False
    try:
        provider = GitContextProvider(repo)
        diff = provider.get_diff(commit_id) or ""
        ctx = InvestigationContext(
            commit_id=commit_id,
            project=project,
            diff=diff,
            raw_diff=diff,
            message=None,
            touched_files=[],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        return detect_archetype(ctx)
    except (GitRepoNotFoundError, OSError):
        return cap_reason.startswith("clean_archetype") if cap_reason else False


# ---------------------------------------------------------------------------
# Taxonomy tagging
# ---------------------------------------------------------------------------

def tag_d3_zero(
    judge_oracle: str | None,
    d3_details: str,
    truncated_files: list[str],
    d2_jaccard: float | None,
    commit_prefix: str,
    *,
    known_context_gap: frozenset[str] = frozenset(),
) -> str:
    """Classify a D3=0 commit into wrong-mechanism | missing-context | judge-infra."""
    if judge_oracle == "unavailable" or (
        judge_oracle == "fix-diff-fallback" and _JUDGE_INFRA_RE.search(d3_details)
    ):
        return "judge-infra"
    if commit_prefix in known_context_gap or (
        bool(truncated_files) and (d2_jaccard or 0.0) < 0.25
    ):
        return "missing-context"
    return "wrong-mechanism"


def tag_d3(
    buggy: bool,
    d3_score: float | None,
    judge_oracle: str | None,
    d3_details: str,
    truncated_files: list[str],
    d2_jaccard: float | None,
    commit_prefix: str,
    *,
    known_context_gap: frozenset[str] = frozenset(),
) -> tuple[str | None, str | None]:
    """Return (d3_tag, d3_sub_tag) for a commit."""
    if not buggy or d3_score is None:
        return None, None
    if d3_score == 0.0:
        tag = tag_d3_zero(
            judge_oracle, d3_details, truncated_files, d2_jaccard, commit_prefix,
            known_context_gap=known_context_gap,
        )
        return tag, None
    if 0.25 <= d3_score < 0.50:
        if truncated_files:
            return "right-area-wrong-mechanism", "diff-truncation"
        if _ARCH_DEPTH_RE.search(d3_details):
            return "right-area-wrong-mechanism", "architectural-depth"
        return "right-area-wrong-mechanism", None
    if d3_score >= 0.75:
        return "correct", None
    if d3_score >= 0.50:
        return "partial-correct", None
    return "right-area-wrong-mechanism", None


def tag_d1(buggy: bool, risk_level: str, d1_score: float | None) -> str | None:
    high_risk = risk_level in {"HIGH", "CRITICAL"}
    if not buggy and high_risk:
        return "false_positive"
    if not buggy and risk_level in {"MEDIUM", "LOW"}:
        return "correct_clean"
    if buggy and high_risk and d1_score == 1.0:
        return "correct_high"
    if buggy and not high_risk:
        return "false_negative"
    return None


def tag_d2(
    buggy: bool,
    d2_jaccard: float | None,
    agent_files: list[str],
    fix_files: list[str],
) -> str | None:
    if not buggy:
        return None
    fix_len = max(1, len(fix_files))
    if len(agent_files) >= 2 * fix_len:
        return "localization_dilution"
    if (d2_jaccard or 0.0) < 0.25:
        return "fix_chain_low_overlap"
    return "fix_chain_ok"


# ---------------------------------------------------------------------------
# Per-commit processing
# ---------------------------------------------------------------------------

def _data_missing_entry(result: dict[str, Any]) -> dict[str, Any]:
    commit_id = result["commit_id"]
    prefix = commit_id[:12]
    scores_raw = result.get("scores") or {}
    return {
        "commit_id": commit_id,
        "commit_prefix": prefix,
        "project": result["project"],
        "buggy": result["buggy"],
        "scores": {dim: parse_score_value(scores_raw, dim) for dim in "D1 D2 D3 D4 D5 D6".split()},
        "agent": {
            "risk_level": result.get("agent", {}).get("risk_level"),
            "localization_count": result.get("agent", {}).get("localization_count"),
        },
        "tags": {"d3_tag": "data_missing", "d3_sub_tag": None, "d1_tag": "data_missing", "d2_tag": "data_missing"},
        "pipeline": {
            "supported_count": None,
            "cap_applied": None,
            "truncation_status": None,
            "judge_oracle": None,
            "archetype_detected": None,
        },
        "evidence": {
            "d2_jaccard": None,
            "d3_judge_details_excerpt": None,
            "d2_agent_files": [],
            "d2_fix_files": [],
        },
    }


def process_commit(
    result: dict[str, Any],
    run_dir: Path,
    repos_root: Path,
    *,
    known_context_gap: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    commit_id = result["commit_id"]
    prefix = commit_id[:12]
    project = result["project"]
    inv_path = run_dir / "investigations" / f"{prefix}_{project}.json"

    if not inv_path.is_file():
        print(f"WARNING: missing investigation JSON for {prefix}", file=sys.stderr)
        return _data_missing_entry(result)

    inv = json.loads(inv_path.read_text())
    eval_path = run_dir / "evaluations" / f"{prefix}_{project}.json"
    if eval_path.is_file():
        eval_data = json.loads(eval_path.read_text())
        for key in ("scores", "agent"):
            if eval_data.get(key) != result.get(key):
                print(f"INFO: {prefix} delivery/evaluation mismatch on {key}", file=sys.stderr)

    scores_raw = result.get("scores") or {}
    d3_details = parse_score_details(scores_raw, "D3")
    d2_details = parse_score_details(scores_raw, "D2")
    judge_oracle = parse_judge_oracle(d3_details)
    d2_jaccard = parse_jaccard(d2_details)
    agent_files, fix_files = parse_agent_fix_files(d2_details)

    meta = inv.get("metadata") or {}
    trunc_meta = meta.get("truncation_metadata") or {}
    truncated_files = trunc_meta.get("truncated_files") or []
    cap_applied = bool(meta.get("cap_applied") or False)
    cap_reason = meta.get("cap_reason")
    supported = count_supported(inv.get("recommendations") or [])
    trunc_status = truncation_status(trunc_meta)
    archetype = infer_archetype(project, commit_id, cap_reason, repos_root)

    buggy = bool(result["buggy"])
    d3_score = parse_score_value(scores_raw, "D3")
    d1_score = parse_score_value(scores_raw, "D1")
    risk_level = str(result.get("agent", {}).get("risk_level", ""))

    d3_tag, d3_sub_tag = tag_d3(
        buggy, d3_score, judge_oracle, d3_details, truncated_files, d2_jaccard, prefix,
        known_context_gap=known_context_gap,
    )
    d1_tag = tag_d1(buggy, risk_level, d1_score)
    d2_tag = tag_d2(buggy, d2_jaccard, agent_files, fix_files)

    excerpt = d3_details[:200] if (d3_details and buggy) else None

    return {
        "commit_id": commit_id,
        "commit_prefix": prefix,
        "project": project,
        "buggy": buggy,
        "scores": {dim: parse_score_value(scores_raw, dim) for dim in "D1 D2 D3 D4 D5 D6".split()},
        "agent": {
            "risk_level": risk_level,
            "localization_count": result.get("agent", {}).get("localization_count"),
        },
        "tags": {"d3_tag": d3_tag, "d3_sub_tag": d3_sub_tag, "d1_tag": d1_tag, "d2_tag": d2_tag},
        "pipeline": {
            "supported_count": supported,
            "cap_applied": cap_applied,
            "truncation_status": trunc_status,
            "judge_oracle": judge_oracle,
            "archetype_detected": archetype,
        },
        "evidence": {
            "d2_jaccard": d2_jaccard,
            "d3_judge_details_excerpt": excerpt,
            "d2_agent_files": agent_files,
            "d2_fix_files": fix_files,
        },
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate(
    commits: list[dict[str, Any]],
    delivery: dict[str, Any],
    *,
    priority_matrix: list[dict[str, Any]],
    task_label: str,
    targets: dict[str, float],
    source_eval: str,
) -> dict[str, Any]:
    """Build the full forensics report from tagged commits."""
    buggy = [c for c in commits if c["buggy"]]
    d3_tags = Counter(c["tags"]["d3_tag"] for c in buggy if c["tags"]["d3_tag"] not in {None, "data_missing"})
    d3_zero = [c for c in buggy if c["scores"].get("D3") == 0.0]
    d3_partial = [c for c in buggy if c["scores"].get("D3") == 0.25]

    # FPs derived from tags, not a hardcoded set
    fp_entries = [c for c in commits if c["tags"]["d1_tag"] == "false_positive"]
    fp_rate = len(fp_entries) / max(1, sum(1 for c in commits if not c["buggy"]))

    jaccards = [c["evidence"]["d2_jaccard"] for c in buggy if c["evidence"]["d2_jaccard"] is not None]
    dilution = sum(1 for c in buggy if c["tags"]["d2_tag"] == "localization_dilution")

    tier_1 = sorted(c["commit_prefix"] for c in d3_zero)
    tier_2 = sorted(c["commit_prefix"] for c in d3_partial)
    rerun = tier_1 + sorted(
        tier_2,
        key=lambda p: TRUNCATION_ORDER.get(
            next(
                (c["pipeline"]["truncation_status"] or "none" for c in commits if c["commit_prefix"] == p),
                "none",
            ),
            2,
        ),
    )

    dim_avg = delivery.get("dimension_averages") or {}
    return {
        "meta": {
            "task": task_label,
            "source_run": delivery.get("metadata", {}).get("run_dir", ""),
            "source_eval": source_eval,
            "method": "deterministic JSON aggregation + rule-based tagging",
            "generated_at": datetime.now(UTC).isoformat(),
            "n_commits": len(commits),
            "judge_model": delivery.get("judge_model"),
        },
        "aggregate_scores": {
            "dimension_averages": dim_avg,
            "v2_targets": targets,
            "gaps_vs_target": {d: round(dim_avg.get(d, 0) - targets[d], 3) for d in targets},
        },
        "d3_failure_taxonomy": {
            "distribution": dict(d3_tags),
            "d3_zero_breakdown": {
                tag: [c["commit_prefix"] for c in d3_zero if c["tags"]["d3_tag"] == tag]
                for tag in ("wrong-mechanism", "missing-context", "judge-infra")
            },
            "d3_partial_breakdown": {
                "right-area-wrong-mechanism": [c["commit_prefix"] for c in d3_partial],
            },
        },
        "clean_fp_analysis": {
            "fp_rate": round(fp_rate, 3),
            "fp_count": len(fp_entries),
            "fp_commits": [
                {
                    "commit_prefix": c["commit_prefix"],
                    "archetype_detected": c["pipeline"]["archetype_detected"],
                    "supported_count": c["pipeline"]["supported_count"],
                    "cap_applied": c["pipeline"]["cap_applied"],
                    "d1_tag": c["tags"]["d1_tag"],
                    "D6": c["scores"].get("D6"),
                }
                for c in fp_entries
            ],
        },
        "d2_gap_analysis": {
            "mean_jaccard_buggy": round(sum(jaccards) / len(jaccards), 3) if jaccards else 0.0,
            "localization_dilution_count": dilution,
            "fix_chain_subset_score": delivery.get("subset_averages", {}).get("D2_fix_chain_only"),
        },
        "priority_matrix": priority_matrix,
        "hard_commit_panel": {
            "tier_1_d3_zero": tier_1,
            "tier_2_d3_partial": tier_2,
            "union_count": len(set(tier_1) | set(tier_2)),
            "recommended_rerun_order": rerun,
        },
        "commits": commits,
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_markdown(report: dict[str, Any], *, run_label: str = "n=50") -> str:
    agg = report["aggregate_scores"]
    dim = agg["dimension_averages"]
    gaps = agg["gaps_vs_target"]
    targets = agg["v2_targets"]
    d3 = report["d3_failure_taxonomy"]
    fp = report["clean_fp_analysis"]
    d2 = report["d2_gap_analysis"]
    panel = report["hard_commit_panel"]
    n_clean = report["meta"]["n_commits"] // 2  # assumes 50/50 split
    n_buggy = report["meta"]["n_commits"] - n_clean
    tier1_count = len(panel["tier_1_d3_zero"])
    tier2_count = len(panel["tier_2_d3_partial"])

    lines = [
        f"# V2 {run_label} Failure Forensics",
        "",
        "## Executive Summary",
        "",
        "| Dimension | Score | V2 Target | Gap |",
        "|-----------|-------|-----------|-----|",
    ]
    for d in ("D1", "D2", "D3", "D4", "D5", "D6"):
        score = dim.get(d)
        score_str = f"{score:.3f}" if score is not None else "N/A"
        target = targets.get(d, 0)
        gap = gaps.get(d, 0)
        lines.append(f"| {d} | {score_str} | {target:.2f} | {gap:+.3f} |")
    lines.extend(
        [
            "",
            f"- Clean FP rate: **{fp['fp_rate']:.0%}** ({fp['fp_count']}/{n_clean} clean commits)",
            f"- Hard panel: **{panel['union_count']}** commits ({tier1_count} D3=0 + {tier2_count} D3=0.25)",
            "",
            "## D3 Failure Taxonomy",
            "",
            "### D3=0 breakdown",
        ]
    )
    for tag, prefixes in d3["d3_zero_breakdown"].items():
        lines.append(f"- **{tag}** ({len(prefixes)}): {', '.join(prefixes) or 'none'}")
    lines.extend(["", "### D3=0.25 partial panel", ""])
    partial = d3["d3_partial_breakdown"]["right-area-wrong-mechanism"]
    lines.append(f"- **right-area-wrong-mechanism** ({len(partial)}): {', '.join(partial)}")
    lines.extend(
        [
            "",
            "## D1 FP Analysis",
            "",
            "| Prefix | Supported | Cap | Archetype | D6 |",
            "|--------|-----------|-----|-----------|-----|",
        ]
    )
    for entry in fp["fp_commits"]:
        lines.append(
            f"| {entry['commit_prefix']} | {entry['supported_count']} | {entry['cap_applied']} | "
            f"{entry['archetype_detected']} | {entry['D6']} |"
        )
    lines.extend(
        [
            "",
            "Pattern: single-SUPPORTED→HIGH rule on defensive/refactor/UI commits.",
            "",
            "## D2 Gap",
            "",
            f"- Mean Jaccard (buggy): **{d2['mean_jaccard_buggy']:.3f}**",
            f"- Localization dilution count: **{d2['localization_dilution_count']}**",
        ]
    )
    if d2.get("fix_chain_subset_score") is not None:
        lines.append(f"- Fix-chain subset D2: **{d2['fix_chain_subset_score']:.3f}**")
    lines.extend(
        [
            "",
            "## Priority Matrix",
            "",
            "| Rank | Dimension | Intervention | Expected Δ | Task |",
            "|------|-----------|--------------|------------|------|",
        ]
    )
    for row in report["priority_matrix"]:
        lines.append(
            f"| {row['rank']} | {row['dimension']} | {row['intervention']} | "
            f"{row['expected_delta']} | {row['task_id']} |"
        )
    lines.extend(["", "## Hard-Commit Panel", "", "### Tier 1 — D3=0", ""])
    lines.extend(f"- {p}" for p in panel["tier_1_d3_zero"])
    lines.extend(["", "### Tier 2 — D3=0.25", ""])
    lines.extend(f"- {p}" for p in panel["tier_2_d3_partial"])
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Structural validation (generic — no count-specific assertions)
# ---------------------------------------------------------------------------

def validate_report_structure(report: dict[str, Any]) -> None:
    """Assert required keys and field types. Does not check run-specific counts."""
    required_top = {
        "meta", "aggregate_scores", "d3_failure_taxonomy",
        "clean_fp_analysis", "d2_gap_analysis", "priority_matrix",
        "hard_commit_panel", "commits",
    }
    assert required_top <= set(report), f"missing top-level keys: {required_top - set(report)}"
    assert isinstance(report["meta"]["n_commits"], int)
    assert len(report["commits"]) == report["meta"]["n_commits"]

    valid_d3_zero_tags = {"wrong-mechanism", "missing-context", "judge-infra", "data_missing"}
    for commit in report["commits"]:
        assert len(commit["commit_id"]) >= 12
        assert commit["commit_prefix"] == commit["commit_id"][:12]
        for field in ("d3_tag", "d1_tag", "d2_tag"):
            assert field in commit["tags"], f"missing {field} in {commit['commit_prefix']}"
        pipe = commit["pipeline"]
        for field in ("supported_count", "cap_applied", "truncation_status", "judge_oracle", "archetype_detected"):
            assert field in pipe, f"missing pipeline.{field} in {commit['commit_prefix']}"
        assert "d2_jaccard" in commit["evidence"]
        assert "d3_judge_details_excerpt" in commit["evidence"]
        if commit["tags"]["d3_tag"] is not None:
            assert commit["tags"]["d3_tag"] in valid_d3_zero_tags | {
                "right-area-wrong-mechanism", "correct", "partial-correct"
            }

    matrix = report["priority_matrix"]
    assert len(matrix) >= 1
    for row in matrix:
        assert "rank" in row and "expected_delta" in row and "task_id" in row

    panel = report["hard_commit_panel"]
    assert "tier_1_d3_zero" in panel and "tier_2_d3_partial" in panel
    assert panel["union_count"] == len(set(panel["tier_1_d3_zero"]) | set(panel["tier_2_d3_partial"]))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_report(
    delivery_path: Path,
    run_dir: Path,
    repos_root: Path,
    *,
    priority_matrix: list[dict[str, Any]],
    task_label: str,
    targets: dict[str, float],
    known_context_gap: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    delivery = json.loads(delivery_path.read_text())
    results = delivery.get("results") or []
    commits = [
        process_commit(r, run_dir, repos_root, known_context_gap=known_context_gap)
        for r in results
    ]
    return aggregate(
        commits,
        delivery,
        priority_matrix=priority_matrix,
        task_label=task_label,
        targets=targets,
        source_eval=str(delivery_path),
    )
