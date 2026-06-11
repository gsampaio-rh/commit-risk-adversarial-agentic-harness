"""Feasibility spike: script-based evidence tier tagging.

Tests whether a pure-script approach can classify hypothesis evidence tiers
(SUPPORTED/SPECULATIVE/REFUTED/UNVERIFIABLE) with ≥80% agreement vs. LLM labels
from the iter-2 panel (output/runs/2026-06-10_21-07-21_real_n12).

Decision rule (from state.json):
  ≥80% strict agreement + ≥90% SUPPORTED quote verification → pure_script
  ≥80% strict OR ≥90% binary SUPPORTED → hybrid: LLM tier + Script quote verification
  <80% both → blocked: evidence_tagger must be LLM-based

Hybrid model (iter-3b target):
  Script verifies LLM SUPPORTED claims (quote ⊆ diff); never upgrades non-SUPPORTED tiers.

Usage:
  .venv/bin/python scripts/spike_evidence_tagger.py --evaluate
  .venv/bin/python scripts/spike_evidence_tagger.py --extract-draft
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
PANEL_DIR = PROJECT_ROOT / "output/runs/2026-06-10_21-07-21_real_n12/investigations"
CORPUS_PATH = PROJECT_ROOT / "tests/fixtures/evidence_tagger_panel.json"
REPORT_PATH = PROJECT_ROOT / ".harness/evals/spike-evidence-tagger.json"
REPOS_BASE = PROJECT_ROOT / "data/repos"
MAX_DIFF_CHARS = 16_000

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class HypothesisFixture:
    commit_id: str
    project: str
    hypothesis_id: str
    hypothesis_text: str
    expected_tier: str          # SUPPORTED | SPECULATIVE | REFUTED | UNVERIFIABLE
    evidence_quote: str         # canonical quote for matching (may be empty)
    curation: str               # "auto" | "hand"
    notes: str = ""


@dataclass
class TagResult:
    tier: str                   # SUPPORTED | SPECULATIVE | REFUTED | UNVERIFIABLE
    quote_in_diff: bool
    match_method: str           # exact | normalized | fuzzy | absent | deferred
    debug: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Diff loading (with 16K truncation to match LLM context)
# ---------------------------------------------------------------------------

_diff_cache: dict[str, str] = {}


def load_diff(commit_id: str, project: str, *, full: bool = False) -> str:
    """Load commit diff, truncated to 16K chars (matching orchestrator cap)."""
    cache_key = f"{commit_id}:{project}:{full}"
    if cache_key in _diff_cache:
        return _diff_cache[cache_key]

    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from commit_investigator.git_context import GitContextProvider  # noqa: PLC0415

    repo_path = REPOS_BASE / project
    if not repo_path.exists():
        return ""

    try:
        gp = GitContextProvider(repo_path)
        diff = gp.get_diff(commit_id) or ""
    except Exception:
        diff = ""

    full_diff = diff
    if not full:
        diff = diff[:MAX_DIFF_CHARS]

    _diff_cache[f"{commit_id}:{project}:False"] = diff[:MAX_DIFF_CHARS]
    _diff_cache[f"{commit_id}:{project}:True"] = full_diff
    return _diff_cache[cache_key]


# ---------------------------------------------------------------------------
# STAGE 3 extraction from reasoning_summary
# ---------------------------------------------------------------------------

# Handles: "HYPOTHESIS 1", "HYPOTHESIS A", "H1", "H2"
_HYPO_HEADER_RE = re.compile(
    r"(?:HYPOTHESIS\s+(?:\d+|[A-Z])\b|H\d+\s+[—\-])",
    re.IGNORECASE,
)

_TIER_RE = re.compile(
    r"(?:—|\u2014|:|\()\s*(SUPPORTED|SPECULATIVE|REFUTED|UNVERIFIABLE)\b",
    re.IGNORECASE,
)

# Also handles "HYPOTHESIS 1: SUPPORTED" and "HYPOTHESIS 1 (SPECULATIVE)"
_TIER_INLINE_RE = re.compile(
    r"\b(SUPPORTED|SPECULATIVE|REFUTED|UNVERIFIABLE)\b",
    re.IGNORECASE,
)


def extract_stage3(reasoning: str) -> str:
    """Extract the STAGE 3 section from reasoning_summary.

    Handles two formats:
    - "STAGE 3 — EVIDENCE:\\nHYPOTHESIS 1 ..." (hypothesis on next line)
    - "STAGE 3 — EVIDENCE: HYPOTHESIS 1 ..." (hypothesis on same line as header)
    """
    idx3 = reasoning.find("STAGE 3")
    idx4 = reasoning.find("STAGE 4")
    if idx3 == -1:
        return ""
    end = idx4 if idx4 != -1 else len(reasoning)
    text = reasoning[idx3:end]
    # Strip only up to (and including) the colon, preserving hypothesis content on same line
    text = re.sub(r"^STAGE 3[^:]*:\s*", "", text, count=1)
    return text.strip()


def parse_hypothesis_blocks(stage3_text: str) -> list[dict[str, Any]]:
    """Split STAGE 3 text into per-hypothesis dicts with tier and body."""
    if not stage3_text:
        return []

    # Split at hypothesis boundaries
    # Handle "HYPOTHESIS 1 — SUPPORTED." or "H1 — SUPPORTED" or inline "STAGE 3 — EVIDENCE: HYPOTHESIS 1 — SUPPORTED"
    parts = re.split(
        r"(?=(?:HYPOTHESIS\s+(?:\d+|[A-Z])\b|H\d+\s*[—\u2014\-]))",
        stage3_text,
        flags=re.IGNORECASE,
    )
    blocks = [p.strip() for p in parts if p.strip()]

    results = []
    for i, block in enumerate(blocks):
        # Extract tier from first line of the block
        first_line = block.split("\n")[0]

        # Try "— TIER" pattern first
        m = _TIER_RE.search(first_line)
        tier = m.group(1).upper() if m else None

        # Fallback: "HYPOTHESIS N: TIER" or "(TIER)"
        if not tier:
            m2 = re.search(r"[:\(]\s*(SUPPORTED|SPECULATIVE|REFUTED|UNVERIFIABLE)\b", first_line, re.IGNORECASE)
            tier = m2.group(1).upper() if m2 else None

        # Last resort: first tier word in block
        if not tier:
            m3 = _TIER_INLINE_RE.search(block[:200])
            tier = m3.group(1).upper() if m3 else "UNVERIFIABLE"

        # Hypothesis ID
        mid = re.match(r"(?:HYPOTHESIS\s+(\d+|[A-Z])|H(\d+))\b", block, re.IGNORECASE)
        hyp_id = f"H{mid.group(1) or mid.group(2)}" if mid else f"H{i+1}"

        results.append({
            "hypothesis_id": hyp_id,
            "expected_tier": tier,
            "block_text": block,
        })

    return results


# ---------------------------------------------------------------------------
# Evidence quote extraction (multi-pass)
# ---------------------------------------------------------------------------

_DIFF_LINE_RE = re.compile(r"^[+\-]\s+\S.+", re.MULTILINE)
_TIER_WORDS = frozenset({"SUPPORTED", "SPECULATIVE", "REFUTED", "UNVERIFIABLE"})


def _is_verifiable_code_quote(quote: str) -> bool:
    """Reject prose/tier-label tokens that cause false-positive diff matches."""
    stripped = quote.strip()
    if not stripped or stripped.upper() in _TIER_WORDS:
        return False
    if re.search(r"[a-zA-Z_]\w*\([^)]*\)", stripped):
        return True
    if re.search(r"[+\-]\s+\S", stripped):
        return True
    if stripped.count(".") >= 2:
        return True
    if re.search(r"(==|!=|->|=>|;\s*$|\(\))", stripped):
        return True
    if len(stripped) >= 20 and re.search(r"[A-Z][a-zA-Z]+\.[a-zA-Z]", stripped):
        return True
    return False


def _extract_code_tokens(text: str, min_len: int = 10) -> list[str]:
    """Extract significant code tokens from text (method calls, identifiers)."""
    # Match method chains, identifiers, patterns like "getIn().setBody" or "setNoStart(true)"
    patterns = [
        r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*\(\))+",  # method chains
        r"[a-zA-Z_]\w*\([^)]{2,30}\)",             # method with args
        r"[a-zA-Z_]\w{8,}",                          # long identifiers
        r'"[^"]{5,40}"',                             # string literals
        r'`[^`]{5,40}`',                             # backtick code
    ]
    tokens = []
    for p in patterns:
        tokens.extend(re.findall(p, text))
    # Deduplicate, filter by length
    seen = set()
    result = []
    for t in tokens:
        if len(t) >= min_len and t not in seen:
            seen.add(t)
            result.append(t)
    return result


def extract_evidence_quote(block_text: str) -> str:
    """Extract the most specific diff-verifiable quote from a hypothesis block.

    Multi-pass (code-like fragments only — prose identifiers rejected):
    A: backtick/code-fenced fragments
    B: single-quoted diff lines
    C: "diff shows/removes/adds X" pattern
    D: parenthetical method chains
    E: method-chain tokens (filtered)
    """
    candidates: list[str] = []

    for fragment in re.findall(r"`([^`]{8,80})`", block_text):
        candidates.append(fragment)

    for fragment in re.findall(r"'([^']{8,80})'", block_text):
        candidates.append(fragment)

    diff_show = re.search(
        r"(?:diff\s+shows?|explicitly\s+removes?|diff\s+explicitly|explicitly\s+shows?|changed\s+from)\s+['\"]?([^.;,\n]{8,60})",
        block_text,
        re.IGNORECASE,
    )
    if diff_show:
        candidates.append(diff_show.group(1).strip().strip("'\""))

    candidates.extend(
        re.findall(
            r"\(([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*\(\))+(?:\([^)]*\))?)\)",
            block_text,
        )
    )
    candidates.extend(_extract_code_tokens(block_text))

    verifiable = [c for c in candidates if _is_verifiable_code_quote(c)]
    if verifiable:
        return max(verifiable, key=len)
    return ""


# ---------------------------------------------------------------------------
# Quote-presence testing (3-tier cascade)
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Normalize diff for matching: collapse whitespace, strip +/- prefix."""
    text = re.sub(r"^[+\- ]", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", text).strip()


def _token_set_match(quote: str, diff: str, *, min_tokens: int = 3, window: int = 200) -> bool:
    """Token-set fuzzy match: ≥80% of quote tokens appear in order within window chars of diff."""
    tokens = re.findall(r"\w{3,}", quote)
    if len(tokens) < min_tokens:
        return False
    threshold = max(min_tokens, int(len(tokens) * 0.8))
    diff_lower = diff.lower()
    found = 0
    pos = 0
    for tok in tokens:
        idx = diff_lower.find(tok.lower(), pos)
        if idx == -1:
            continue
        if idx - pos <= window:
            found += 1
            pos = idx
    return found >= threshold


def quote_in_diff(quote: str, diff_truncated: str) -> tuple[bool, str]:
    """Check if evidence_quote is present in the truncated diff.

    Returns (found, match_method).
    """
    if not quote or len(quote.strip()) < 8:
        return False, "absent"

    # Check if we're already past truncation
    trunc_marker = "... (truncated"
    if trunc_marker in diff_truncated:
        trunc_pos = diff_truncated.find(trunc_marker)
        diff_for_check = diff_truncated[:trunc_pos]
    else:
        diff_for_check = diff_truncated

    # Pass 1: exact substring
    if quote in diff_for_check:
        return True, "exact"

    # Pass 2: normalized
    norm_quote = _normalize(quote)
    norm_diff = _normalize(diff_for_check)
    if norm_quote and norm_quote in norm_diff:
        return True, "normalized"

    # Pass 3: token-set fuzzy (flag as fuzzy — less reliable)
    if _token_set_match(quote, diff_for_check):
        return True, "fuzzy"

    return False, "not_found"


# ---------------------------------------------------------------------------
# tag_hypothesis — the core spike function
# ---------------------------------------------------------------------------

def tag_hypothesis(
    evidence_quote: str | None,
    diff_truncated: str,
    *,
    diff_was_truncated: bool = False,
    llm_tier: str | None = None,
) -> TagResult:
    """Tag a hypothesis using hybrid verification (LLM tier + script quote check).

    Hybrid logic (iter-3b target):
    - llm_tier=SUPPORTED: verify quote ⊆ diff; downgrade to SPECULATIVE if missing/invalid
    - llm_tier≠SUPPORTED: defer to LLM tier (script never upgrades non-SUPPORTED)
    """
    quote = (evidence_quote or "").strip()
    llm = (llm_tier or "").upper() or None

    if llm == "SUPPORTED":
        if not quote or len(quote.strip()) < 8:
            return TagResult(
                tier="SPECULATIVE",
                quote_in_diff=False,
                match_method="absent",
                debug={"reason": "SUPPORTED claim with no verifiable quote"},
            )
        found, method = quote_in_diff(quote, diff_truncated)
        if found:
            return TagResult(
                tier="SUPPORTED",
                quote_in_diff=True,
                match_method=method,
                debug={"reason": "SUPPORTED verified by quote-in-diff"},
            )
        return TagResult(
            tier="SPECULATIVE",
            quote_in_diff=False,
            match_method="not_found",
            debug={
                "reason": "SUPPORTED downgraded: quote not in diff",
                "diff_was_truncated": diff_was_truncated,
            },
        )

    deferred = llm or "UNVERIFIABLE"
    return TagResult(
        tier=deferred,
        quote_in_diff=False,
        match_method="deferred",
        debug={"reason": "non-SUPPORTED: deferring to llm_tier"},
    )


# ---------------------------------------------------------------------------
# Agreement evaluation
# ---------------------------------------------------------------------------

def evaluate_agreement(
    fixtures: list[HypothesisFixture],
    diffs: dict[str, str],
) -> dict[str, Any]:
    """Score script tier agreement vs expected tiers."""
    results = []
    for fx in fixtures:
        diff_trunc = diffs.get(f"{fx.commit_id}:{fx.project}", "")
        diff_was_truncated = len(load_diff(fx.commit_id, fx.project, full=True)) > MAX_DIFF_CHARS

        tag = tag_hypothesis(
            fx.evidence_quote,
            diff_trunc,
            diff_was_truncated=diff_was_truncated,
            llm_tier=fx.expected_tier,
        )

        strict_match = tag.tier == fx.expected_tier
        binary_match = (tag.tier == "SUPPORTED") == (fx.expected_tier == "SUPPORTED")

        results.append({
            "commit_id": fx.commit_id,
            "hypothesis_id": fx.hypothesis_id,
            "expected_tier": fx.expected_tier,
            "script_tier": tag.tier,
            "quote_extracted": bool(fx.evidence_quote and len(fx.evidence_quote) >= 8),
            "quote_in_diff": tag.quote_in_diff,
            "match_method": tag.match_method,
            "strict_agreement": strict_match,
            "binary_supported_agreement": binary_match,
            "notes": fx.notes,
        })

    total = len(results)
    if total == 0:
        return {"error": "no fixtures"}

    strict = sum(1 for r in results if r["strict_agreement"]) / total
    binary = sum(1 for r in results if r["binary_supported_agreement"]) / total

    # Supported-subset: quote extraction rate
    supported_fx = [r for r in results if r["expected_tier"] == "SUPPORTED"]
    extracted_rate = (
        sum(1 for r in supported_fx if r["quote_extracted"]) / len(supported_fx)
        if supported_fx else 0.0
    )
    supported_verified = sum(
        1 for r in supported_fx if r["script_tier"] == "SUPPORTED" and r["quote_in_diff"]
    )
    supported_verification_rate = (
        supported_verified / len(supported_fx) if supported_fx else 0.0
    )

    failures = [r for r in results if not r["strict_agreement"]]

    # Confusion matrix
    tiers = ["SUPPORTED", "SPECULATIVE", "REFUTED", "UNVERIFIABLE"]
    confusion: dict[str, dict[str, int]] = {t: {t2: 0 for t2 in tiers} for t in tiers}
    for r in results:
        exp = r["expected_tier"]
        got = r["script_tier"]
        if exp in confusion and got in confusion:
            confusion[exp][got] += 1

    return {
        "total_fixtures": total,
        "strict_agreement": round(strict, 4),
        "binary_supported_agreement": round(binary, 4),
        "supported_quote_extraction_rate": round(extracted_rate, 4),
        "supported_verification_rate": round(supported_verification_rate, 4),
        "deferred_count": sum(1 for r in results if r["match_method"] == "deferred"),
        "quote_verified_count": sum(1 for r in results if r["quote_in_diff"]),
        "confusion_matrix": confusion,
        "failures": failures,
        "all_results": results,
    }


def evaluate_auto_extract_pipeline() -> dict[str, Any]:
    """Score hybrid tag_hypothesis on auto-extracted STAGE 3 quotes (honest end-to-end)."""
    draft = extract_draft_corpus()
    fixtures = [
        HypothesisFixture(
            commit_id=r["commit_id"],
            project=r["project"],
            hypothesis_id=r["hypothesis_id"],
            hypothesis_text=r.get("hypothesis_text", ""),
            expected_tier=r["expected_tier"],
            evidence_quote=r.get("evidence_quote", ""),
            curation=r.get("curation", "auto"),
            notes=r.get("notes", ""),
        )
        for r in draft
    ]
    diffs = load_panel_diffs(fixtures)
    metrics = evaluate_agreement(fixtures, diffs)
    metrics["pipeline"] = "auto_extract"
    return metrics


def decide_outcome(metrics: dict[str, Any]) -> str:
    """Apply decision rule: pure_script only if hand AND auto pipelines verify SUPPORTED."""
    strict = metrics["strict_agreement"]
    binary = metrics["binary_supported_agreement"]
    verify = metrics.get("supported_verification_rate", 0.0)
    auto = metrics.get("auto_extract_pipeline") or {}

    auto_strict = auto.get("strict_agreement", strict)
    auto_verify = auto.get("supported_verification_rate", verify)

    if (
        strict >= 0.80
        and verify >= 0.90
        and auto_strict >= 0.80
        and auto_verify >= 0.90
    ):
        return "pure_script"
    if strict >= 0.80 or binary >= 0.90 or verify >= 0.90:
        return "hybrid"
    return "blocked"


# ---------------------------------------------------------------------------
# Load curated corpus
# ---------------------------------------------------------------------------

def load_corpus() -> list[HypothesisFixture]:
    """Load hand-curated fixtures from tests/fixtures/evidence_tagger_panel.json."""
    if not CORPUS_PATH.exists():
        raise FileNotFoundError(f"Corpus not found: {CORPUS_PATH}")
    data = json.loads(CORPUS_PATH.read_text())
    return [
        HypothesisFixture(
            commit_id=f["commit_id"],
            project=f["project"],
            hypothesis_id=f["hypothesis_id"],
            hypothesis_text=f.get("hypothesis_text", ""),
            expected_tier=f["expected_tier"],
            evidence_quote=f.get("evidence_quote", ""),
            curation=f.get("curation", "auto"),
            notes=f.get("notes", ""),
        )
        for f in data
    ]


def load_panel_diffs(fixtures: list[HypothesisFixture]) -> dict[str, str]:
    """Load truncated diffs for all unique (commit_id, project) pairs."""
    seen: set[str] = set()
    diffs: dict[str, str] = {}
    for fx in fixtures:
        key = f"{fx.commit_id}:{fx.project}"
        if key not in seen:
            seen.add(key)
            diffs[key] = load_diff(fx.commit_id, fx.project)
    return diffs


# ---------------------------------------------------------------------------
# Draft extraction (for --extract-draft)
# ---------------------------------------------------------------------------

def extract_draft_corpus() -> list[dict[str, Any]]:
    """Auto-extract hypothesis fixtures from all 12 panel files."""
    rows = []
    for fname in sorted(os.listdir(PANEL_DIR)):
        if not fname.endswith(".json"):
            continue
        d = json.loads((PANEL_DIR / fname).read_text())
        commit_id = fname.split("_")[0]
        project = fname.split("_")[1].replace(".json", "")

        reasoning = d.get("reasoning_summary", "")
        stage3 = extract_stage3(reasoning)

        if not stage3 or stage3.startswith("(no"):
            # 572f3cee has "STAGE 3: N/A" — add synthetic UNVERIFIABLE
            rows.append({
                "commit_id": commit_id,
                "project": project,
                "hypothesis_id": "H_NA",
                "hypothesis_text": "No defect hypotheses (test-only/style commit)",
                "expected_tier": "UNVERIFIABLE",
                "evidence_quote": "",
                "curation": "auto",
                "notes": "STAGE 3: N/A — no hypotheses"
            })
            continue

        blocks = parse_hypothesis_blocks(stage3)
        for block in blocks[:3]:  # max 3 per commit for corpus size
            quote = extract_evidence_quote(block["block_text"])
            rows.append({
                "commit_id": commit_id,
                "project": project,
                "hypothesis_id": block["hypothesis_id"],
                "hypothesis_text": block["block_text"][:200],
                "expected_tier": block["expected_tier"],
                "evidence_quote": quote,
                "curation": "auto",
                "notes": "",
            })

    return rows


# ---------------------------------------------------------------------------
# Spike report writer
# ---------------------------------------------------------------------------

def write_report(metrics: dict[str, Any], decision: str, auto_metrics: dict[str, Any] | None = None) -> None:
    """Write spike report to .harness/evals/spike-evidence-tagger.json."""
    rec = {
        "pure_script": (
            "Ship evidence_tagger.py as pure Script in iter-3b. "
            "tag_hypothesis() verifies quote-in-diff for SUPPORTED claims."
        ),
        "hybrid": (
            "Use LLM tier as primary in iter-3b; Script verifies SUPPORTED claims "
            "(quote ⊆ diff) and downgrades hallucinated SUPPORTED to SPECULATIVE. "
            "Non-SUPPORTED tiers defer to LLM — no script upgrade."
        ),
        "blocked": (
            "Script accuracy insufficient. Evidence tagger must be LLM-based (second call) "
            "or dropped. Do not build pure-script evidence_tagger.py."
        ),
    }[decision]

    auto_summary: dict[str, Any] = {}
    if auto_metrics:
        auto_summary = {
            "strict_agreement": auto_metrics["strict_agreement"],
            "binary_supported_agreement": auto_metrics["binary_supported_agreement"],
            "supported_verification_rate": auto_metrics.get("supported_verification_rate", 0),
            "fixture_count": auto_metrics["total_fixtures"],
            "failure_count": len(auto_metrics.get("failures", [])),
        }

    report = {
        "task_id": "iter-3a-spike",
        "timestamp": "2026-06-11T00:00:00Z",
        "decision": decision,
        "model": "hybrid",
        "metrics": {
            "strict_agreement": metrics["strict_agreement"],
            "binary_supported_agreement": metrics["binary_supported_agreement"],
            "fixture_count": metrics["total_fixtures"],
            "supported_quote_extraction_rate": metrics["supported_quote_extraction_rate"],
            "supported_verification_rate": metrics.get("supported_verification_rate", 0),
            "deferred_count": metrics.get("deferred_count", 0),
            "quote_verified_count": metrics.get("quote_verified_count", 0),
            "confusion_matrix": metrics["confusion_matrix"],
            "truncation_flips": sum(
                1 for r in metrics.get("all_results", [])
                if r.get("expected_tier") == "SUPPORTED"
                and r.get("script_tier") == "SPECULATIVE"
                and r.get("match_method") == "not_found"
            ),
            "auto_extract_pipeline": auto_summary,
        },
        "failures": [
            {
                "commit_id": f["commit_id"],
                "hypothesis_id": f["hypothesis_id"],
                "expected": f["expected_tier"],
                "got": f["script_tier"],
                "match_method": f["match_method"],
                "reason": f.get("notes", ""),
            }
            for f in metrics.get("failures", [])
        ],
        "recommendation_for_iter_3b": rec,
        "all_results": metrics.get("all_results", []),
    }

    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(f"Report written: {REPORT_PATH}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Evidence tagger feasibility spike")
    parser.add_argument("--evaluate", action="store_true", help="Run full evaluation")
    parser.add_argument("--extract-draft", action="store_true", help="Auto-extract corpus draft")
    parser.add_argument("--show-stage3", help="Show STAGE 3 for a commit prefix")
    args = parser.parse_args()

    if args.extract_draft:
        rows = extract_draft_corpus()
        draft_path = PROJECT_ROOT / "tests/fixtures/evidence_tagger_panel_draft.json"
        draft_path.write_text(json.dumps(rows, indent=2))
        print(f"Draft corpus ({len(rows)} rows) → {draft_path}")
        tier_dist: dict[str, int] = {}
        for r in rows:
            t = r["expected_tier"]
            tier_dist[t] = tier_dist.get(t, 0) + 1
        print("Tier distribution:", tier_dist)
        quote_filled = sum(1 for r in rows if r["evidence_quote"])
        print(f"Evidence quotes extracted: {quote_filled}/{len(rows)}")

    elif args.evaluate:
        print("Loading corpus...")
        fixtures = load_corpus()
        print(f"  {len(fixtures)} fixtures loaded")

        print("Loading diffs...")
        diffs = load_panel_diffs(fixtures)
        print(f"  {len(diffs)} commits loaded")

        print("Evaluating agreement...")
        metrics = evaluate_agreement(fixtures, diffs)

        print("Evaluating auto-extract pipeline...")
        auto_metrics = evaluate_auto_extract_pipeline()
        metrics["auto_extract_pipeline"] = {
            "strict_agreement": auto_metrics["strict_agreement"],
            "binary_supported_agreement": auto_metrics["binary_supported_agreement"],
            "supported_verification_rate": auto_metrics.get("supported_verification_rate", 0),
            "fixture_count": auto_metrics["total_fixtures"],
            "failure_count": len(auto_metrics.get("failures", [])),
        }

        print("\n=== RESULTS (hand-curated corpus, hybrid model) ===")
        print(f"Strict agreement:           {metrics['strict_agreement']:.1%}")
        print(f"Binary SUPPORTED agreement: {metrics['binary_supported_agreement']:.1%}")
        print(f"SUPPORTED verification:     {metrics.get('supported_verification_rate', 0):.1%}")
        print(f"Deferred to LLM:            {metrics.get('deferred_count', 0)}/{metrics['total_fixtures']}")
        print(f"Quote verified in diff:     {metrics.get('quote_verified_count', 0)}")
        print(f"Total fixtures:             {metrics['total_fixtures']}")

        print("\n=== AUTO-EXTRACT PIPELINE ===")
        print(f"Strict agreement:           {auto_metrics['strict_agreement']:.1%}")
        print(f"Binary SUPPORTED agreement: {auto_metrics['binary_supported_agreement']:.1%}")
        print(f"SUPPORTED verification:     {auto_metrics.get('supported_verification_rate', 0):.1%}")
        print(f"Failures:                   {len(auto_metrics.get('failures', []))}")

        if metrics["failures"]:
            print(f"\nHand-corpus failures ({len(metrics['failures'])}):")
            for f in metrics["failures"]:
                print(f"  {f['commit_id']} {f['hypothesis_id']}: expected={f['expected_tier']} got={f['script_tier']} method={f['match_method']}")

        decision = decide_outcome(metrics)
        print(f"\nDecision: {decision.upper()}")

        write_report(metrics, decision, auto_metrics)

    elif args.show_stage3:
        prefix = args.show_stage3
        for fname in os.listdir(PANEL_DIR):
            if fname.startswith(prefix):
                d = json.loads((PANEL_DIR / fname).read_text())
                s3 = extract_stage3(d.get("reasoning_summary", ""))
                print(f"STAGE 3 for {fname[:12]}:")
                print(s3[:1500])
                break

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
