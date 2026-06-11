"""Risk policy: deterministic risk level evaluation and clean-commit capping.

This module is the single source of risk_level truth. No LLM logic here.
The policy evaluates commit archetype signals and reasoning quality to
determine whether a HIGH/CRITICAL LLM verdict should be capped to MEDIUM.

iter-3a: bridge signature (llm_risk_level, context, reasoning) — matches the
current call site in orchestrator._assemble_report(). The full iter-3 target
signature (archetype, hypotheses: list[TaggedHypothesis], ...) is introduced
in iter-3b once evidence_tagger ships.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from commit_investigator.archetype import detect_archetype, has_production_defect_signals
from commit_investigator.context_builder import InvestigationContext
from commit_investigator.evidence_tagger import count_supported_from_reasoning
from commit_investigator.report import RiskLevel

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
    orchestrator. cap_reason and applied_rules are audit fields — populated
    in iter-3a and used for transparency/debugging; full rule-based population
    is wired in iter-3b when the evidence_tagger feeds structured hypotheses.
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

    Behavior-preserving bridge for iter-3a: same logic as the former
    _apply_clean_commit_risk_cap() in orchestrator, now returning a
    richer PolicyVerdict instead of a (RiskLevel, bool) tuple.

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
