"""Confidence signal extractor: deterministic signals from investigation artifacts.

Converts InvestigationContext + TagResults + HypothesisSpec list into a
ConfidenceSignals bundle. All extractors are purely deterministic — zero LLM
calls, no network I/O, no file I/O.

The 7 signals feed into confidence_model.compute_confidence() to produce a
scalar score and tier (HIGH/MEDIUM/LOW) that replaces the heuristic conf=0.70.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from commit_investigator.analysis.archetype import has_production_defect_signals
from commit_investigator.analysis.evidence_tagger import TagResult
from commit_investigator.context.context_builder import InvestigationContext
from commit_investigator.hypothesis.hypothesis_engine import HypothesisSpec

__all__ = ["ConfidenceSignals", "extract_confidence_signals", "ROUTER_HIGH_THRESHOLD", "categorize_mechanism"]

# Router probability threshold above which the router predicts HIGH risk.
# Single source of truth — imported by risk_policy.py and confidence_model.py.
ROUTER_HIGH_THRESHOLD = 0.70

# ---------------------------------------------------------------------------
# Mechanism category vocabulary (for hypothesis_diversity)
# ---------------------------------------------------------------------------

_MECHANISM_CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    ("null_reference", re.compile(r"\bnull\b|npe|nullpointer|null.pointer", re.IGNORECASE)),
    ("concurrency", re.compile(r"\brace\b|concurren|synchroni|thread|deadlock|volatile|atomic", re.IGNORECASE)),
    ("resource_leak", re.compile(r"\bleak\b|unclosed|resource|memory|gc\b|finaliz", re.IGNORECASE)),
    ("type_cast", re.compile(r"\bcast\b|classcast|type.conv|coerci|overflow|underflow", re.IGNORECASE)),
    ("off_by_one", re.compile(r"\boff.by.one\b|index\b|bound\b|oob\b|fencepost", re.IGNORECASE)),
    ("config_init", re.compile(r"\binit\b|config|setup|bootstrap|missing.value|default", re.IGNORECASE)),
    ("exception_handling", re.compile(r"\bexception\b|error.handl|catch\b|throw\b|swallow", re.IGNORECASE)),
    ("security", re.compile(r"\binjection\b|xss\b|csrf\b|auth\b|bypass\b|privilege", re.IGNORECASE)),
    ("logic_error", re.compile(r"\blogic\b|condition\b|branch\b|predicate\b|incorrect", re.IGNORECASE)),
]

# Patterns for commit_message_clarity
_JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-[0-9]+\b")
_BUG_WORDS_RE = re.compile(
    r"\b(?:fix|bug|error|crash|null|npe|exception|issue|defect|fail|broken|regression|incorrect|wrong)\b",
    re.IGNORECASE,
)

# Diff line classification
_CHANGED_LINE_RE = re.compile(r"^[+-](?![+-]).*$", re.MULTILINE)  # + or - but not +++ or ---
_TOTAL_CONTENT_LINE_RE = re.compile(r"^[ +\-](?![+-]).*$", re.MULTILINE)  # context + changed (not headers)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfidenceSignals:
    """Bundle of 7 deterministic signals extracted from investigation artifacts."""

    supported_count: int
    """Count of SUPPORTED (changed-line-grounded) hypotheses."""

    evidence_density: float
    """Fraction [0,1] of hypotheses with a non-empty evidence_quote."""

    router_agreement: float
    """Router-to-risk-level alignment: 1.0=agree, 0.0=disagree, 0.5=neutral."""

    hypothesis_diversity: int
    """Number of distinct mechanism categories across first 3 hypotheses (0–9)."""

    diff_signal_ratio: float
    """Fraction [0,1] of diff content lines that are changed (+/-), not context."""

    missing_context_flags: int
    """Count of missing_reasons (test_adjacency, blame, etc.) — penalizes thin context."""

    commit_message_clarity: float
    """1.0 if commit message has JIRA key or explicit bug word, 0.0 otherwise."""


# ---------------------------------------------------------------------------
# Individual signal extractors
# ---------------------------------------------------------------------------


def _extract_supported_count(tagged: list[TagResult]) -> int:
    return sum(1 for t in tagged if t.tier == "SUPPORTED")


def _extract_evidence_density(hypotheses: list[HypothesisSpec]) -> float:
    if not hypotheses:
        return 0.0
    filled = sum(1 for h in hypotheses if h.evidence_quote and h.evidence_quote.strip())
    return filled / len(hypotheses)


def _extract_router_agreement(
    context: InvestigationContext,
    supported_count: int,
) -> float:
    """1.0=agree (router HIGH and evidence-backed HIGH), 0.0=disagree, 0.5=neutral.

    Router prior is excluded from the agent-side base risk check to avoid
    circular agreement (router>=0.7 always implies HIGH if router counts).
    Agent evidence is HIGH when supported_count>=1 or defect_signals.
    """
    router_prob = context.router_probability
    if router_prob is None or router_prob < ROUTER_HIGH_THRESHOLD:
        return 0.5

    defect_signals = has_production_defect_signals(context)
    evidence_backed_high = supported_count >= 1 or defect_signals
    return 1.0 if evidence_backed_high else 0.0


def categorize_mechanism(mechanism: str) -> str:
    """Map a free-text mechanism string to the first matching category label.

    Public so calibrate_confidence.py can import the same vocabulary instead
    of maintaining a divergent copy.
    """
    for label, pattern in _MECHANISM_CATEGORIES:
        if pattern.search(mechanism):
            return label
    return "other"


def _extract_hypothesis_diversity(hypotheses: list[HypothesisSpec]) -> int:
    """Count distinct mechanism categories across first 3 hypotheses."""
    top3 = hypotheses[:3]
    categories = {categorize_mechanism(h.mechanism) for h in top3 if h.mechanism}
    return len(categories)


def _extract_diff_signal_ratio(diff: str | None) -> float:
    """Fraction of diff content lines that are +/- (changed), not context lines."""
    if not diff:
        return 0.0
    changed = len(_CHANGED_LINE_RE.findall(diff))
    total_content = len(_TOTAL_CONTENT_LINE_RE.findall(diff))
    if total_content == 0:
        return 0.0
    return changed / total_content


def _extract_missing_context_flags(context: InvestigationContext) -> int:
    return len(context.missing_reasons or [])


def _extract_commit_message_clarity(message: str | None) -> float:
    """1.0 if message has JIRA key or explicit bug-related word; 0.0 otherwise."""
    if not message or not message.strip():
        return 0.0
    if _JIRA_KEY_RE.search(message):
        return 1.0
    if _BUG_WORDS_RE.search(message):
        return 1.0
    return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_confidence_signals(
    context: InvestigationContext,
    tagged: list[TagResult],
    hypotheses: list[HypothesisSpec],
) -> ConfidenceSignals:
    """Extract all 7 deterministic confidence signals from investigation artifacts.

    Pure function: no LLM calls, no I/O, no side effects.
    """
    supported_count = _extract_supported_count(tagged)
    return ConfidenceSignals(
        supported_count=supported_count,
        evidence_density=_extract_evidence_density(hypotheses),
        router_agreement=_extract_router_agreement(context, supported_count),
        hypothesis_diversity=_extract_hypothesis_diversity(hypotheses),
        diff_signal_ratio=_extract_diff_signal_ratio(context.diff),
        missing_context_flags=_extract_missing_context_flags(context),
        commit_message_clarity=_extract_commit_message_clarity(context.message),
    )
