"""Evidence tier tagger: hybrid script-based hypothesis verification.

Architecture decision (iter-3a-spike): HYBRID — LLM tier is primary label;
script verifies SUPPORTED claims by checking evidence_quote presence in diff.
Hallucinated SUPPORTED (quote absent or not in diff) is downgraded to SPECULATIVE.

See .harness/evals/spike-evidence-tagger.json for spike results:
  strict_agreement=86.1% (auto-extract), 100% (hand-curated corpus)
  decision=hybrid, supported_verification_rate=100%

Usage:
  from commit_investigator.analysis.evidence_tagger import (
      tag_hypothesis, count_supported_from_reasoning
  )
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MAX_DIFF_CHARS = 16_000
_MIN_QUOTE_LEN = 8

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TagResult:
    """Result of tagging a single hypothesis."""

    tier: str                   # SUPPORTED | SPECULATIVE | REFUTED | UNVERIFIABLE
    quote_in_diff: bool
    match_method: str           # exact | normalized | fuzzy | absent | deferred
    debug: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Quote-presence testing (3-tier cascade)
# ---------------------------------------------------------------------------

_TIER_RE = re.compile(
    r"(?:—|\u2014|:|\()\s*(SUPPORTED|SPECULATIVE|REFUTED|UNVERIFIABLE)\b",
    re.IGNORECASE,
)


def _normalize_diff(text: str) -> str:
    """Strip diff prefixes (+/-) and collapse whitespace for normalized matching."""
    text = re.sub(r"^[+\- ]", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", text).strip()


def _token_set_match(quote: str, diff: str, *, min_tokens: int = 3, window: int = 200) -> bool:
    """Token-set fuzzy match: ≥80% of quote tokens appear in order within window chars."""
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


def quote_in_diff(quote: str | None, diff_truncated: str) -> tuple[bool, str]:
    """Check if evidence_quote is verifiably present in the truncated diff.

    Returns (found: bool, match_method: str).
    match_method is one of: 'exact', 'normalized', 'fuzzy', 'absent', 'not_found'.
    """
    if not quote or len(quote.strip()) < _MIN_QUOTE_LEN:
        return False, "absent"

    diff_for_check = diff_truncated

    # Pass 1: exact substring
    if quote in diff_for_check:
        return True, "exact"

    # Pass 2: normalized (strip diff prefixes, collapse whitespace)
    norm_quote = _normalize_diff(quote)
    norm_diff = _normalize_diff(diff_for_check)
    if norm_quote and norm_quote in norm_diff:
        return True, "normalized"

    # Pass 3: token-set fuzzy (flagged as less reliable)
    if _token_set_match(quote, diff_for_check):
        return True, "fuzzy"

    return False, "not_found"


# ---------------------------------------------------------------------------
# Core hybrid tagger
# ---------------------------------------------------------------------------


def tag_hypothesis(
    evidence_quote: str | None,
    diff_truncated: str,
    *,
    diff_was_truncated: bool = False,
    llm_tier: str | None = None,
) -> TagResult:
    """Tag one hypothesis as SUPPORTED/SPECULATIVE/REFUTED/UNVERIFIABLE.

    Hybrid rule (from spike decision):
    - Quote found in diff → SUPPORTED (overrides any llm_tier)
    - No quote + llm_tier == SUPPORTED → SPECULATIVE (hallucinated claim)
    - No quote + llm_tier != SUPPORTED → defer to llm_tier
    - Quote not in diff, diff truncated → UNVERIFIABLE (may be beyond truncation)
    - Quote not in diff, diff complete → SPECULATIVE (paraphrased/hallucinated)
    """
    quote = (evidence_quote or "").strip()

    if not quote or len(quote) < _MIN_QUOTE_LEN:
        if llm_tier == "SUPPORTED":
            return TagResult(
                tier="SPECULATIVE",
                quote_in_diff=False,
                match_method="absent",
                debug={"reason": "SUPPORTED claim with no verifiable quote"},
            )
        deferred = llm_tier or "UNVERIFIABLE"
        return TagResult(
            tier=deferred,
            quote_in_diff=False,
            match_method="deferred",
            debug={"reason": "no quote, deferring to llm_tier"},
        )

    found, method = quote_in_diff(quote, diff_truncated)

    if found:
        return TagResult(tier="SUPPORTED", quote_in_diff=True, match_method=method)

    if diff_was_truncated:
        return TagResult(
            tier="UNVERIFIABLE",
            quote_in_diff=False,
            match_method="not_found",
            debug={"reason": "quote not in truncated diff", "diff_was_truncated": True},
        )

    return TagResult(
        tier="SPECULATIVE",
        quote_in_diff=False,
        match_method="not_found",
        debug={"reason": "quote not in complete diff"},
    )


# ---------------------------------------------------------------------------
# STAGE 3 extraction and count_supported_from_reasoning
# ---------------------------------------------------------------------------

_HYPO_SPLIT_RE = re.compile(
    r"(?=(?:HYPOTHESIS\s+(?:\d+|[A-Z])\b|H\d+\s*[—\u2014\-]))",
    re.IGNORECASE,
)


def _extract_stage3(reasoning: str) -> str:
    """Extract STAGE 3 section from reasoning_summary.

    Handles:
    - 'STAGE 3 — EVIDENCE:\\nHYPOTHESIS 1 ...' (hypothesis on next line)
    - 'STAGE 3 — EVIDENCE: HYPOTHESIS 1 ...' (hypothesis on same line as header)
    """
    idx3 = reasoning.find("STAGE 3")
    idx4 = reasoning.find("STAGE 4")
    if idx3 == -1:
        return ""
    end = idx4 if idx4 != -1 else len(reasoning)
    text = reasoning[idx3:end]
    text = re.sub(r"^STAGE 3[^:]*:\s*", "", text, count=1)
    return text.strip()


def _extract_evidence_quote(block_text: str) -> str:
    """Extract the most specific diff-verifiable quote from a hypothesis block.

    Multi-pass extraction (A → D), returns first non-empty result.
    Filters out tier labels and short prose fragments.
    """
    # Discard tier label lines from extraction targets
    tier_words = {"SUPPORTED", "SPECULATIVE", "REFUTED", "UNVERIFIABLE"}

    # Pass A: backtick-fenced code
    candidates = re.findall(r"`([^`]{8,80})`", block_text)
    for c in candidates:
        if not any(t in c.upper() for t in tier_words):
            return c

    # Pass B: single-quoted strings
    candidates = re.findall(r"'([^']{8,80})'", block_text)
    for c in candidates:
        if not any(t in c.upper() for t in tier_words):
            return c

    # Pass C: "diff shows/removes/adds X" pattern
    m = re.search(
        r"(?:diff\s+shows?|explicitly\s+removes?|diff\s+explicitly|explicitly\s+shows?)\s+"
        r"['\"]?([^.;,\n]{8,60})",
        block_text,
        re.IGNORECASE,
    )
    if m:
        candidate = m.group(1).strip().strip("'\"")
        if not any(t in candidate.upper() for t in tier_words):
            return candidate

    # Pass D: method-chain code tokens
    tokens = re.findall(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*\(\))+", block_text)
    if tokens:
        longest = max(tokens, key=len)
        if len(longest) >= _MIN_QUOTE_LEN:
            return longest

    return ""


def _parse_hypothesis_blocks(stage3_text: str) -> list[dict[str, Any]]:
    """Split STAGE 3 text into per-hypothesis dicts with tier and body."""
    if not stage3_text:
        return []

    parts = _HYPO_SPLIT_RE.split(stage3_text)
    blocks = [p.strip() for p in parts if p.strip()]

    results = []
    for i, block in enumerate(blocks):
        first_line = block.split("\n")[0]

        m = _TIER_RE.search(first_line)
        tier = m.group(1).upper() if m else None

        if not tier:
            m2 = re.search(
                r"[:\(]\s*(SUPPORTED|SPECULATIVE|REFUTED|UNVERIFIABLE)\b",
                first_line,
                re.IGNORECASE,
            )
            tier = m2.group(1).upper() if m2 else None

        if not tier:
            m3 = re.search(
                r"\b(SUPPORTED|SPECULATIVE|REFUTED|UNVERIFIABLE)\b",
                block[:200],
                re.IGNORECASE,
            )
            tier = m3.group(1).upper() if m3 else "UNVERIFIABLE"

        mid = re.match(r"(?:HYPOTHESIS\s+(\d+|[A-Z])|H(\d+))\b", block, re.IGNORECASE)
        hyp_id = f"H{mid.group(1) or mid.group(2)}" if mid else f"H{i + 1}"

        results.append({"hypothesis_id": hyp_id, "expected_tier": tier, "block_text": block})

    return results


def count_supported_from_reasoning(
    reasoning: str,
    diff: str,
    max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS,
) -> int:
    """Count SUPPORTED hypotheses in reasoning using hybrid evidence tagging.

    Parses STAGE 3 blocks from reasoning_summary, extracts evidence_quote per
    hypothesis, and verifies quote presence in the truncated diff.

    Returns 0 if STAGE 3 is absent (fallback to risk_policy regex).
    """
    stage3 = _extract_stage3(reasoning)
    if not stage3:
        return -1  # sentinel: caller should fall back to regex

    diff_truncated = diff[:max_diff_chars]
    diff_was_truncated = len(diff) > max_diff_chars
    blocks = _parse_hypothesis_blocks(stage3)

    count = 0
    for block in blocks:
        if block["expected_tier"] != "SUPPORTED":
            continue
        quote = _extract_evidence_quote(block["block_text"])
        result = tag_hypothesis(
            quote,
            diff_truncated,
            diff_was_truncated=diff_was_truncated,
            llm_tier=block["expected_tier"],
        )
        if result.tier == "SUPPORTED":
            count += 1

    return count
