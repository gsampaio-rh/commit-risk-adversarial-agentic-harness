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
from commit_investigator.context.context_builder import InvestigationContext
from commit_investigator.analysis.evidence_tagger import TagResult, count_supported_from_reasoning
from commit_investigator.analysis.report import RiskLevel

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
    orchestrator.     cap_reason and applied_rules are audit fields for transparency/debugging.
    """

    risk_level: RiskLevel
    cap_applied: bool
    cap_reason: str
    applied_rules: list[str] = field(default_factory=list)
    supported_count: int = 0


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
) -> PolicyVerdict:
    """Evaluate risk from tagged hypotheses + context signals.

    Derives base risk from script signals:
    - supported_count >= 1 → HIGH (capped if clean archetype + no defect signals)
    - production_defect_signals → HIGH (no cap override)
    - router_prior >= 0.70 → HIGH (subject to cap)
    - otherwise → MEDIUM
    """
    supported_count = sum(1 for t in tagged if t.tier == "SUPPORTED")
    defect_signals = has_production_defect_signals(context)
    router_prior = context.router_probability or 0.0

    if supported_count >= 1 or defect_signals or router_prior >= 0.70:
        base_risk = RiskLevel.HIGH
    else:
        base_risk = RiskLevel.MEDIUM

    if base_risk not in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return PolicyVerdict(
            risk_level=base_risk,
            cap_applied=False,
            cap_reason="",
            applied_rules=[],
            supported_count=supported_count,
        )

    if defect_signals:
        return PolicyVerdict(
            risk_level=base_risk,
            cap_applied=False,
            cap_reason="",
            applied_rules=[],
            supported_count=supported_count,
        )

    is_clean_archetype = detect_archetype(context)
    is_speculative_only = supported_count == 0

    if not is_clean_archetype and not is_speculative_only:
        return PolicyVerdict(
            risk_level=base_risk,
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
