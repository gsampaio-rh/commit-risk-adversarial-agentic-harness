"""Report builder: assembles CommitInvestigationReport from HypothesisResponse + Script verdicts.

Extracted from orchestrator._assemble_report() in iter-3e to keep orchestrator ≤250 lines.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from commit_investigator.context_builder import InvestigationContext
from commit_investigator.evidence_tagger import TagResult, tag_hypothesis
from commit_investigator.hypothesis_engine import HypothesisResponse, HypothesisSpec
from commit_investigator.smart_diff import AssembledDiff
from commit_investigator.llm import LLMResponse
from commit_investigator.report import (
    CommitInvestigationReport,
    EvidenceItem,
    EvidenceType,
    LocalizationClaim,
    Recommendation,
    RecommendationPriority,
    RiskAssessment,
    RiskLevel,
)
from commit_investigator.risk_policy import PolicyVerdict

if TYPE_CHECKING:
    from commit_investigator.orchestrator import BudgetState, TurnCheckpoint


def tag_hypotheses(
    hypotheses: list[HypothesisSpec],
    diff: str | None,
    truncation_metadata: AssembledDiff | None = None,
) -> list[TagResult]:
    """Tag each hypothesis against the diff using the evidence_tagger."""
    diff_truncated = bool(truncation_metadata and truncation_metadata.truncated_files)
    return [
        tag_hypothesis(h.evidence_quote, diff or "", diff_was_truncated=diff_truncated)
        for h in hypotheses
    ]


def build_report(
    hyp_response: HypothesisResponse,
    tagged: list[TagResult],
    verdict: PolicyVerdict,
    context: InvestigationContext,
    last_response: LLMResponse,
    checkpoints: list[TurnCheckpoint],
    budget: BudgetState,
    tools_used: list[str],
    turns: int,
) -> CommitInvestigationReport:
    """Assemble a CommitInvestigationReport from hypothesis response + script verdicts."""
    risk_level = verdict.risk_level

    evidence_items = [EvidenceItem(
        type=EvidenceType.DIFF_HUNK if context.diff else EvidenceType.NUMERIC_FEATURE,
        source=context.commit_id,
        content=(context.diff[:500] if context.diff else "Numeric features only"),
        relevance="Primary investigation context",
    )]

    localization = [
        LocalizationClaim(
            file=h.file,
            lines=_safe_lines(h.lines),
            rationale=h.mechanism,
        )
        for h in hyp_response.hypotheses
        if h.file
    ]

    findings = [
        h.mechanism
        for h, tag in zip(hyp_response.hypotheses, tagged)
        if tag.tier == "SUPPORTED"
    ]

    recommendations = _derive_recommendations(hyp_response.hypotheses, tagged, verdict)

    per_stage = [
        {
            "stage": cp.turn,
            "tier": "investigation",
            "tokens_used": cp.tokens_used,
            "cost_usd": round(cp.cost, 6),
            "latency_ms": round(cp.latency_ms, 1),
        }
        for cp in checkpoints
    ]

    metadata: dict[str, Any] = {
        "model": last_response.model,
        "total_tokens": budget.total_tokens,
        "total_cost": budget.total_cost,
        "budget_exceeded": budget.budget_exceeded,
        "missing_reasons": list(context.missing_reasons),
        "per_stage": per_stage,
    }

    tm = context.truncation_metadata
    if tm is not None:
        metadata["truncation_metadata"] = {
            "included_files": tm.included_files,
            "truncated_files": tm.truncated_files,
            "total_chars": tm.total_chars,
        }

    if verdict.cap_applied:
        metadata["clean_commit_risk_cap_applied"] = True
        metadata["cap_applied"] = True
        metadata["cap_reason"] = verdict.cap_reason
        metadata["applied_rules"] = list(verdict.applied_rules)

    return CommitInvestigationReport(
        commit_id=context.commit_id,
        project=context.project,
        risk_assessment=RiskAssessment(level=risk_level, confidence=0.7),
        evidence=evidence_items,
        findings=findings,
        localization=localization,
        reasoning_summary=hyp_response.summary,
        recommendations=recommendations,
        tools_used=tools_used,
        turn_count=turns,
        metadata=metadata,
    )


def _derive_recommendations(
    hypotheses: list[HypothesisSpec],
    tagged: list[TagResult],
    verdict: PolicyVerdict,
) -> list[Recommendation]:
    """Derive actionable recommendations from SUPPORTED hypotheses.

    Each SUPPORTED hypothesis contributes one recommendation using its
    suggested_action (from LLM) or a fallback derived from the mechanism.
    Speculative and refuted hypotheses are excluded.
    """
    recs: list[Recommendation] = []
    priority = (
        RecommendationPriority.HIGH
        if verdict.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        else RecommendationPriority.MEDIUM
    )

    for h, tag in zip(hypotheses, tagged):
        if tag.tier != "SUPPORTED":
            continue

        action = h.suggested_action.strip() if h.suggested_action else ""
        if not action:
            # Fallback: derive action from mechanism
            file_ref = f" in {h.file}" if h.file else ""
            action = f"Investigate and test the failure mode{file_ref}: {h.mechanism[:120]}"

        recs.append(Recommendation(
            action=action,
            priority=priority,
            rationale=f"Hypothesis SUPPORTED — evidence quote present: {bool(h.evidence_quote)}",
        ))

    return recs


def _safe_lines(lines: list[int]) -> tuple[int, int] | None:
    """Convert list[int] to tuple[int, int] or None."""
    if len(lines) >= 2:
        return (lines[0], lines[1])
    return None
