#!/usr/bin/env python3
"""Confidence signal calibration script.

Reads existing investigation run output JSONs (no new LLM calls) and computes
per-signal Gini coefficients as a D1 prediction AUC proxy.

D1 correctness:
  - BUG commit with risk_level HIGH  → correct (true positive)
  - BUG commit with risk_level != HIGH → missed (false negative)
  - Clean commit with risk_level != HIGH → correct (true negative)
  - Clean commit with risk_level HIGH → false positive

Gini coefficient ∈ [0,1]: 1.0 = perfect discriminator, 0.0 = random.
Gini = 2 * AUC - 1 (rank-based AUC approximation via Mann-Whitney U).

Usage:
  python calibrate_confidence.py --run-dir output/runs/2026-06-11_14-34-34_real_n11
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Import production signal vocabulary — single source of truth, no divergence.
sys.path.insert(0, str(Path(__file__).parent / "src"))
from commit_investigator.analysis.signal_extractor import categorize_mechanism  # noqa: E402

_JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-[0-9]+\b")
_BUG_WORDS_RE = re.compile(
    r"\b(?:fix|bug|error|crash|null|npe|exception|issue|defect|fail|broken|regression|incorrect|wrong)\b",
    re.IGNORECASE,
)
_SUPPORTED_RE = re.compile(r"\bSUPPORTED\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Signal extraction from saved JSON (no live InvestigationContext available)
# ---------------------------------------------------------------------------


def _proxy_supported_count(record: dict) -> float:
    """Estimate supported count from findings length and reasoning SUPPORTED mentions."""
    reasoning = record.get("reasoning_summary", "") or ""
    supported_mentions = len(_SUPPORTED_RE.findall(reasoning))
    return float(min(supported_mentions, 3))


def _proxy_evidence_density(record: dict) -> float:
    """Proxy: fraction of findings that contain a quoted fragment (backtick or quote)."""
    findings = record.get("findings", []) or []
    if not findings:
        return 0.0
    with_quote = sum(1 for f in findings if "`" in str(f) or "'" in str(f))
    return with_quote / len(findings)


def _proxy_router_agreement(record: dict) -> float:
    """Proxy: INVESTIGATE route implies router says HIGH (1.0), else neutral (0.5)."""
    route = (record.get("route") or "").upper()
    return 1.0 if route == "INVESTIGATE" else 0.5


def _proxy_hypothesis_diversity(record: dict) -> float:
    """Proxy: distinct mechanism categories across findings[:3].

    Uses production categorize_mechanism from signal_extractor — same vocabulary,
    no divergence between calibration Gini and live inference.
    """
    findings = (record.get("findings") or [])[:3]
    cats = {categorize_mechanism(str(f)) for f in findings}
    return float(len(cats))


def _proxy_diff_signal_ratio(record: dict) -> float:
    """Proxy: total_chars from truncation_metadata as a rough signal-density proxy.

    Higher char count suggests a larger diff with more changed lines.
    Normalized to [0,1] at 5000 chars = 1.0 (typical large diff).
    """
    meta = record.get("metadata", {}) or {}
    trunc = meta.get("truncation_metadata") or {}
    total_chars = trunc.get("total_chars", 0) or 0
    return min(total_chars / 5000.0, 1.0)


def _proxy_missing_context_flags(record: dict) -> float:
    """Direct from metadata.missing_reasons."""
    meta = record.get("metadata", {}) or {}
    reasons = meta.get("missing_reasons") or []
    return float(len(reasons))


def _proxy_commit_message_clarity(record: dict) -> float:
    """Proxy from commit_id hint or findings text (actual message not stored in JSON)."""
    # Commit message is not stored in the output JSON.
    # Use findings text as a proxy — if findings mention JIRA keys or bug words,
    # the agent likely saw a clear commit message.
    findings_text = " ".join(str(f) for f in (record.get("findings") or []))
    if _JIRA_KEY_RE.search(findings_text):
        return 1.0
    if _BUG_WORDS_RE.search(findings_text):
        return 1.0
    return 0.0


_SIGNAL_EXTRACTORS = {
    "supported_count": _proxy_supported_count,
    "evidence_density": _proxy_evidence_density,
    "router_agreement": _proxy_router_agreement,
    "hypothesis_diversity": _proxy_hypothesis_diversity,
    "diff_signal_ratio": _proxy_diff_signal_ratio,
    "missing_context_flags": _proxy_missing_context_flags,
    "commit_message_clarity": _proxy_commit_message_clarity,
}


# ---------------------------------------------------------------------------
# D1 outcome and Gini calculation
# ---------------------------------------------------------------------------


def _d1_correct(record: dict) -> int:
    """1 if the agent's risk level correctly classifies the commit for D1."""
    buggy = bool(record.get("buggy_label"))
    risk = (record.get("risk_level") or "MEDIUM").upper()
    if buggy:
        return 1 if risk == "HIGH" else 0
    else:
        return 1 if risk != "HIGH" else 0


def _gini_coefficient(signal_values: list[float], outcomes: list[int]) -> float:
    """Compute Gini coefficient via Mann-Whitney U rank-AUC.

    Gini = 2 * AUC - 1 where AUC is the probability that a randomly chosen
    D1-correct commit has a higher signal than a randomly chosen D1-incorrect commit.
    Higher signal should predict D1-correct (1).
    """
    positives = [s for s, o in zip(signal_values, outcomes) if o == 1]
    negatives = [s for s, o in zip(signal_values, outcomes) if o == 0]

    if not positives or not negatives:
        return 0.0

    u_stat = sum(
        1.0 if p > n else (0.5 if p == n else 0.0)
        for p in positives
        for n in negatives
    )
    auc = u_stat / (len(positives) * len(negatives))
    # Convention: use max(auc, 1-auc) so direction-agnostic signals get credit.
    auc = max(auc, 1.0 - auc)
    return round(2.0 * auc - 1.0, 4)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def load_records(run_dir: Path) -> list[dict]:
    inv_dir = run_dir / "investigations"
    if not inv_dir.exists():
        raise FileNotFoundError(f"No investigations/ under {run_dir}")
    records = []
    for json_path in sorted(inv_dir.glob("*.json")):
        with open(json_path) as f:
            records.append(json.load(f))
    return records


def run_calibration(run_dir: Path) -> None:
    records = load_records(run_dir)
    if not records:
        print("No investigation records found.", file=sys.stderr)
        sys.exit(1)

    outcomes = [_d1_correct(r) for r in records]
    n_correct = sum(outcomes)
    print(f"Loaded {len(records)} records — D1 correct: {n_correct}/{len(records)}\n")
    print(f"{'Signal':<30} {'Gini':>8}  (higher = stronger D1 predictor)")
    print("-" * 50)

    for name, extractor in _SIGNAL_EXTRACTORS.items():
        values = [extractor(r) for r in records]
        gini = _gini_coefficient(values, outcomes)
        bar_len = int(gini * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"{name:<30} {gini:>8.4f}  {bar}")

    print()
    print("Note: Gini is direction-agnostic. 0.0 = no discriminating power, 1.0 = perfect.")
    print("      missing_context_flags is a negative predictor — high flags → lower confidence.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate confidence signal Gini coefficients")
    parser.add_argument(
        "--run-dir",
        required=True,
        help="Path to run directory containing investigations/ subdirectory",
    )
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        print(f"Error: run-dir '{run_dir}' does not exist", file=sys.stderr)
        sys.exit(1)
    run_calibration(run_dir)


if __name__ == "__main__":
    main()
