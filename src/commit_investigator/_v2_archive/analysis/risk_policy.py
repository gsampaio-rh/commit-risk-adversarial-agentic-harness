"""Risk policy: deterministic risk level evaluation and clean-commit capping.

This module is the single source of risk_level truth. No LLM logic here.
The policy evaluates commit archetype signals and reasoning quality to
determine whether a HIGH/CRITICAL LLM verdict should be capped to MEDIUM.

Bridge signature (llm_risk_level, context, reasoning) — matches the
current call site in orchestrator._assemble_report().
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from commit_investigator.analysis.archetype import detect_archetype, has_production_defect_signals
from commit_investigator.analysis.confidence_model import ConfidenceResult, compute_confidence
from commit_investigator.analysis.signal_extractor import (
    ROUTER_HIGH_THRESHOLD,
    extract_confidence_signals,
)
from commit_investigator.context.context_builder import InvestigationContext
from commit_investigator.analysis.evidence_tagger import TagResult, count_supported_from_reasoning
from commit_investigator.analysis.report import RiskLevel
from commit_investigator.hypothesis.hypothesis_engine import HypothesisSpec

# ---------------------------------------------------------------------------
# Reasoning quality helpers
# ---------------------------------------------------------------------------

_SUPPORTED_HYPOTHESIS_RE = re.compile(
    r"(?:HYPOTHESIS\s+[A-Z0-9]+\s*[—-]\s*SUPPORTED|"
    r"HYPOTHESIS[^.\n]*?(?:—|:)\s*SUPPORTED\b|"
    r"\bSUPPORTED\s*—|\(\s*SUPPORTED\s*\))",
    re.IGNORECASE,
)


def _reasoning_has_supported_hypothesis(reasoning: str) -> bool:
    if not reasoning:
        return False
    if _SUPPORTED_HYPOTHESIS_RE.search(reasoning):
        return True
    return bool(
        re.search(
            r"STAGE 3[^STAGE]*\bHYPOTHESIS\b[^STAGE]*\bSUPPORTED\b",
            reasoning,
            re.IGNORECASE | re.DOTALL,
        )
    )


def _reasoning_all_speculative_or_unverifiable(reasoning: str) -> bool:
    if _reasoning_has_supported_hypothesis(reasoning):
        return False
    return bool(re.search(r"SPECULATIVE|UNVERIFIABLE", reasoning, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Policy verdict
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PolicyVerdict:
    """Result of risk policy evaluation.

    risk_level and cap_applied are the primary outputs consumed by the
    orchestrator. cap_reason and applied_rules are audit fields for transparency/debugging.
    confidence_score is the scalar [0,1] from the confidence equation. Defaults
    to 0.70 (the historical flat prior) when the legacy evaluate_risk() path is used.
    """

    risk_level: RiskLevel
    cap_applied: bool
    cap_reason: str
    applied_rules: list[str] = field(default_factory=list)
    supported_count: int = 0
    # 0.70 preserves the legacy flat confidence emitted before the confidence equation.
    confidence_score: float = 0.70


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_risk(
    llm_risk_level: RiskLevel,
    context: InvestigationContext,
    reasoning: str,
) -> PolicyVerdict:
    """Evaluate and potentially cap the LLM-assigned risk level.

    Evaluates and potentially caps the LLM-assigned risk level, returning
    a richer PolicyVerdict instead of a (RiskLevel, bool) tuple.

    Cap rules:
    - Only HIGH and CRITICAL are ever capped (LOW/MEDIUM pass through).
    - If production defect signals exist (guard removal, lifecycle, concurrency),
      the cap is skipped — defect signals always win.
    - If the commit matches a clean archetype (version bump, label rename, etc.),
      cap to MEDIUM regardless of reasoning content.
    - If reasoning contains only SPECULATIVE/UNVERIFIABLE hypotheses (no SUPPORTED),
      cap to MEDIUM globally — even on non-archetype diffs.
    """
    # Use evidence_tagger for accurate SUPPORTED count; fall back to regex when
    # STAGE 3 is absent (tagger returns -1 sentinel).
    tagger_count = count_supported_from_reasoning(reasoning, context.diff or "")
    if tagger_count == -1:
        supported_count = 1 if _reasoning_has_supported_hypothesis(reasoning) else 0
    else:
        supported_count = tagger_count

    if llm_risk_level not in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return PolicyVerdict(
            risk_level=llm_risk_level,
            cap_applied=False,
            cap_reason="",
            applied_rules=[],
            supported_count=supported_count,
        )

    if has_production_defect_signals(context):
        return PolicyVerdict(
            risk_level=llm_risk_level,
            cap_applied=False,
            cap_reason="",
            applied_rules=[],
            supported_count=supported_count,
        )

    is_clean_archetype = detect_archetype(context)
    is_speculative_only = _reasoning_all_speculative_or_unverifiable(reasoning)

    if not is_clean_archetype and not is_speculative_only:
        return PolicyVerdict(
            risk_level=llm_risk_level,
            cap_applied=False,
            cap_reason="",
            applied_rules=[],
            supported_count=supported_count,
        )

    if is_clean_archetype:
        cap_reason = "clean_archetype_no_production_defect_signals"
        applied_rules = ["cap_to_MEDIUM:clean_archetype"]
    else:
        cap_reason = "speculative_or_unverifiable_only"
        applied_rules = ["cap_to_MEDIUM:speculative_only"]

    return PolicyVerdict(
        risk_level=RiskLevel.MEDIUM,
        cap_applied=True,
        cap_reason=cap_reason,
        applied_rules=applied_rules,
        supported_count=supported_count,
    )


def evaluate_risk_from_hypotheses(
    tagged: list[TagResult],
    context: InvestigationContext,
    hypotheses: list[HypothesisSpec] | None = None,
) -> PolicyVerdict:
    """Evaluate risk from tagged hypotheses + context signals.

    Script-layer risk evaluation (no LLM calls):
    - supported_count >= 1, defect signals, or router_prior >= threshold → HIGH
    - Otherwise → MEDIUM; subject to archetype cap and LOW-confidence cap.
    """
    hypothesis_list = hypotheses or []
    supported_count = sum(1 for tag in tagged if tag.tier == "SUPPORTED")
    signals = extract_confidence_signals(context, tagged, hypothesis_list)
    confidence = compute_confidence(signals)
    defect_signals = has_production_defect_signals(context)

    base_risk = _derive_base_risk(supported_count, defect_signals, context)
    verdict = _apply_archetype_cap(base_risk, supported_count, defect_signals, context, confidence)
    return _apply_low_confidence_cap(verdict, confidence, defect_signals)


def _derive_base_risk(
    supported_count: int,
    defect_signals: bool,
    context: InvestigationContext,
) -> RiskLevel:
    """Determine base risk from script signals before any capping."""
    router_prior = context.router_probability or 0.0
    if supported_count >= 1 or defect_signals or router_prior >= ROUTER_HIGH_THRESHOLD:
        return RiskLevel.HIGH
    return RiskLevel.MEDIUM


def _apply_archetype_cap(
    base_risk: RiskLevel,
    supported_count: int,
    defect_signals: bool,
    context: InvestigationContext,
    confidence: ConfidenceResult,
) -> PolicyVerdict:
    """Apply clean-archetype and speculative-only caps; return PolicyVerdict."""
    if base_risk not in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return _make_verdict(base_risk, supported_count, confidence.score)

    if defect_signals:
        return _make_verdict(base_risk, supported_count, confidence.score)

    is_clean_archetype = detect_archetype(context)
    is_speculative_only = supported_count == 0

    if not is_clean_archetype and not is_speculative_only:
        return _make_verdict(base_risk, supported_count, confidence.score)

    if is_clean_archetype:
        cap_reason = "clean_archetype_no_production_defect_signals"
        rules = ["cap_to_MEDIUM:clean_archetype"]
    else:
        cap_reason = "speculative_or_unverifiable_only"
        rules = ["cap_to_MEDIUM:speculative_only"]

    return PolicyVerdict(
        risk_level=RiskLevel.MEDIUM,
        cap_applied=True,
        cap_reason=cap_reason,
        applied_rules=rules,
        supported_count=supported_count,
        confidence_score=confidence.score,
    )


def _make_verdict(
    risk_level: RiskLevel,
    supported_count: int,
    confidence_score: float,
) -> PolicyVerdict:
    """Build a no-cap PolicyVerdict with common defaults."""
    return PolicyVerdict(
        risk_level=risk_level,
        cap_applied=False,
        cap_reason="",
        applied_rules=[],
        supported_count=supported_count,
        confidence_score=confidence_score,
    )


def _apply_low_confidence_cap(
    verdict: PolicyVerdict,
    confidence: ConfidenceResult,
    defect_signals: bool,
) -> PolicyVerdict:
    """Cap HIGH/CRITICAL to MEDIUM when confidence tier is LOW.

    Bypass conditions (cap does NOT fire):
    - Defect signals present — structural defect signals override uncertainty.
    - supported_count >= 1 — diff-grounded evidence is sufficient proof; confidence
      tier reflects metadata quality, not evidence absence. Capping SUPPORTED commits
      would turn true positives into false negatives (the opposite of the cap's goal).
    - Risk level is not HIGH/CRITICAL — cap only makes sense for high-risk verdicts.
    """
    if confidence.tier != "LOW" or defect_signals:
        return verdict
    if verdict.supported_count >= 1:
        return verdict
    if verdict.risk_level not in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return verdict
    applied_rules = list(verdict.applied_rules)
    applied_rules.append("cap_to_MEDIUM:low_confidence")
    return PolicyVerdict(
        risk_level=RiskLevel.MEDIUM,
        cap_applied=True,
        cap_reason="low_confidence_tier",
        applied_rules=applied_rules,
        supported_count=verdict.supported_count,
        confidence_score=confidence.score,
    )
