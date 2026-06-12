"""Report builder: assembles CommitInvestigationReport from HypothesisResponse + Script verdicts."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from commit_investigator.context.context_builder import InvestigationContext
from commit_investigator.analysis.evidence_tagger import TagResult, tag_hypothesis
from commit_investigator.hypothesis.hypothesis_engine import HypothesisResponse, HypothesisSpec
from commit_investigator.context.smart_diff import AssembledDiff, _file_rank, parse_file_diffs
from commit_investigator.infra.llm import LLMResponse
from commit_investigator.analysis.report import (
    CommitInvestigationReport,
    EvidenceItem,
    EvidenceType,
    LocalizationClaim,
    Recommendation,
    RecommendationPriority,
    RiskAssessment,
    RiskLevel,
)
from commit_investigator.analysis.risk_policy import PolicyVerdict

if TYPE_CHECKING:
    from commit_investigator.pipeline.orchestrator import BudgetState, TurnCheckpoint
    from commit_investigator.context.turn2_context import Turn2ContextBundle


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
    turn2_bundle: Turn2ContextBundle | None = None,
) -> CommitInvestigationReport:
    """Assemble a CommitInvestigationReport from hypothesis response + script verdicts."""
    risk_level = verdict.risk_level

    evidence_items = [EvidenceItem(
        type=EvidenceType.DIFF_HUNK if context.diff else EvidenceType.NUMERIC_FEATURE,
        source=context.commit_id,
        content=(context.diff[:500] if context.diff else "Numeric features only"),
        relevance="Primary investigation context",
    )]

    localization = _build_localization(hyp_response.hypotheses, tagged, context.raw_diff)

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
        "total_cost_usd": round(budget.total_cost, 6),
        "budget_exceeded": budget.budget_exceeded,
        "missing_reasons": list(context.missing_reasons),
        "per_stage": per_stage,
        "turn_count": turns,
    }

    if turn2_bundle is not None:
        metadata["turn2_injection"] = {
            "truncated_files": turn2_bundle.truncated_files,
            "blame_files": turn2_bundle.blame_files,
            "has_truncated_section": turn2_bundle.has_truncated_section,
            "has_blame_section": turn2_bundle.has_blame_section,
            "message_preview": turn2_bundle.message[:500],
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


def _build_localization(
    hypotheses: list[HypothesisSpec],
    tagged: list[TagResult],
    raw_diff: str | None,
) -> list[LocalizationClaim]:
    """Build localization claims from SUPPORTED hypotheses only, ranked by defect-signal.

    Filters to tag.tier==SUPPORTED with a non-empty file. Ranks by:
    (1) defect-signal file match (smart_diff rank==0 means highest signal),
    (2) production file path heuristic,
    (3) evidence quote length (longer = stronger grounding),
    (4) original index as stable tie-break.
    """
    defect_signal_files: set[str] = set()
    if raw_diff:
        for fd in parse_file_diffs(raw_diff):
            hunk_text = fd.header + "".join(fd.hunks)
            if _file_rank(fd.path, hunk_text) == 0:
                defect_signal_files.add(fd.path)

    candidates: list[tuple[int, int, int, int, HypothesisSpec]] = []
    for idx, (h, tag) in enumerate(zip(hypotheses, tagged)):
        if tag.tier != "SUPPORTED" or not h.file:
            continue
        rank_signal = 0 if h.file in defect_signal_files else 1
        rank_prod = 0 if _is_production_file(h.file) else 1
        rank_quote = -len(h.evidence_quote)
        candidates.append((rank_signal, rank_prod, rank_quote, idx, h))

    seen: set[tuple[str, tuple[int, int] | None]] = set()
    claims: list[LocalizationClaim] = []
    for _, _, _, _, h in sorted(candidates, key=lambda t: t[:4]):
        key = (h.file, _safe_lines(h.lines))
        if key in seen:
            continue
        seen.add(key)
        claims.append(LocalizationClaim(
            file=h.file,
            lines=_safe_lines(h.lines),
            rationale=h.mechanism,
        ))
    return claims


def _is_production_file(path: str) -> bool:
    """Return True when the file path looks like a production source file."""
    low = path.lower()
    if "test" in low or "spec" in low:
        return False
    return low.endswith((".java", ".py", ".go", ".ts", ".js", ".scala", ".kt"))


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
