"""Phase 2b: watchlist expansion — trigger, merge, and orchestration.

Conditional phase that investigates watchlist candidates when Phase 2
produces weak results. See agent-loop.md §Phase 2b and V4.2 ADR.
"""

from __future__ import annotations

from typing import Any

from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.harness.result import (
    InvestigationExitReason,
    Phase2bResult,
    Suspect,
)
from commit_investigator.harness.scoped_runner import Phase2Result, RevisedScopedInvestigator
from commit_investigator.infra.git_context import GitContextProvider
from commit_investigator.infra.llm import LLMProvider
from commit_investigator.models.candidates import CandidateSet
from commit_investigator.narrowing.models import TriageResult


CONFIDENCE_THRESHOLD = 0.6
PROMOTION_MARGIN = 0.15
MAX_FINAL_SUSPECTS = 5


def should_trigger_phase2b(phase2: Phase2Result) -> tuple[bool, str]:
    """Decide whether Phase 2b watchlist expansion is needed.

    Returns (triggered, reason). Trigger conditions (ANY):
      (a) no suspects
      (b) max confidence < 0.6
      (c) top suspect has no evidence_quotes
    """
    if not phase2.suspects:
        return True, "no_suspects"

    confidences = [s.get("confidence", 0.0) for s in phase2.suspects]
    max_conf = max(confidences) if confidences else 0.0
    if max_conf < CONFIDENCE_THRESHOLD:
        return True, "low_confidence"

    top_idx = confidences.index(max_conf)
    top_quotes = phase2.suspects[top_idx].get("evidence_quotes", [])
    if not top_quotes:
        return True, "no_evidence"

    return False, "watchlist_skipped"


def merge_suspects(
    phase2: list[Suspect], phase2b: list[Suspect],
) -> list[Suspect]:
    """Merge Phase 2 and Phase 2b suspects per agent-loop.md §Merge Logic.

    Steps:
      1. Dedup by full SHA
      2. Duplicates: confidence=max, evidence_quotes=union, mechanism=longer
      3. 2b-only suspects: insert below P2 unless confidence > top_p2 + 0.15
      4. Sort by (grounded_quote_count DESC, confidence DESC)
      5. Cap at 5, assign rank 1..N
    """
    p2_by_sha: dict[str, Suspect] = {s.commit_id: s for s in phase2}
    p2b_by_sha: dict[str, Suspect] = {s.commit_id: s for s in phase2b}
    top_p2_conf = max((s.confidence for s in phase2), default=0.0)

    merged: list[Suspect] = []
    promoted: list[Suspect] = []
    demoted: list[Suspect] = []

    all_shas = list(dict.fromkeys(
        [s.commit_id for s in phase2] + [s.commit_id for s in phase2b]
    ))

    for sha in all_shas:
        p2_s = p2_by_sha.get(sha)
        p2b_s = p2b_by_sha.get(sha)

        if p2_s and p2b_s:
            merged.append(_merge_duplicate(p2_s, p2b_s))
        elif p2_s:
            merged.append(p2_s)
        elif p2b_s:
            if p2b_s.confidence > top_p2_conf + PROMOTION_MARGIN:
                promoted.append(p2b_s)
            else:
                demoted.append(p2b_s)

    result = promoted + merged + demoted
    result.sort(key=lambda s: (-len(s.evidence_quotes), -s.confidence))
    return _assign_ranks(result[:MAX_FINAL_SUSPECTS])


def _merge_duplicate(p2: Suspect, p2b: Suspect) -> Suspect:
    """Merge two suspects for the same SHA: max conf, union quotes, longer mechanism."""
    quotes = list(dict.fromkeys(p2.evidence_quotes + p2b.evidence_quotes))
    mechanism = p2.mechanism if len(p2.mechanism) >= len(p2b.mechanism) else p2b.mechanism
    tools = list(dict.fromkeys(p2.tools_used + p2b.tools_used))

    return Suspect(
        commit_id=p2.commit_id,
        confidence=max(p2.confidence, p2b.confidence),
        mechanism=mechanism,
        evidence_quotes=quotes,
        phase="both",
        tools_used=tools,
    )


def _assign_ranks(suspects: list[Suspect]) -> list[Suspect]:
    for i, s in enumerate(suspects):
        s.rank = i + 1
    return suspects


PHASE2B_MAX_TOOL_CALLS = 8
PHASE2B_MAX_TURNS = 4


def run_phase2b(
    phase2_result: Phase2Result,
    problem: ProblemStatement,
    triage: TriageResult,
    candidate_set: CandidateSet,
    git: GitContextProvider,
    llm: LLMProvider,
) -> tuple[list[Suspect], Phase2bResult | None, InvestigationExitReason]:
    """Run Phase 2b watchlist expansion if triggered.

    Returns (final_suspects, phase2b_result_or_None, exit_reason).
    """
    triggered, reason = should_trigger_phase2b(phase2_result)
    p2_suspects = _dicts_to_suspects(phase2_result.suspects, phase="investigation")

    if not triggered:
        return p2_suspects, None, InvestigationExitReason.WATCHLIST_SKIPPED

    if not triage.watchlist:
        return p2_suspects, None, InvestigationExitReason.WATCHLIST_SKIPPED

    synthetic_triage = _build_watchlist_triage(triage)
    inv = RevisedScopedInvestigator(
        llm=llm,
        problem=problem,
        triage=synthetic_triage,
        candidate_set=candidate_set,
        git=git,
        max_tool_calls=PHASE2B_MAX_TOOL_CALLS,
        max_turns=PHASE2B_MAX_TURNS,
    )
    p2b_raw = inv.investigate()

    p2b_suspects = _dicts_to_suspects(p2b_raw.suspects, phase="watchlist_expansion")
    p2b_result = Phase2bResult(
        suspects=p2b_suspects,
        tool_calls=len(p2b_raw.tool_trace),
        turns=p2b_raw.metadata.get("tool_calls", 0),
        trigger_reason=reason,
    )

    merged = merge_suspects(p2_suspects, p2b_suspects)

    exit_reason = (
        InvestigationExitReason.WATCHLIST_EXPANSION_EXHAUSTED
        if p2b_raw.exit_reason in (
            InvestigationExitReason.BUDGET_EXHAUSTED,
            InvestigationExitReason.MAX_TURNS,
            InvestigationExitReason.FORCED_CONCLUDE,
        )
        else p2b_raw.exit_reason
    )

    return merged, p2b_result, exit_reason


def _dicts_to_suspects(
    raw: list[dict[str, Any]], *, phase: str = "investigation",
) -> list[Suspect]:
    """Convert raw suspect dicts (from Phase 2 output) to Suspect objects."""
    suspects = []
    for i, d in enumerate(raw):
        suspects.append(Suspect(
            commit_id=d.get("commit_id", ""),
            rank=i + 1,
            confidence=d.get("confidence", 0.0),
            mechanism=d.get("mechanism", ""),
            evidence_quotes=list(d.get("evidence_quotes", [])),
            phase=phase,
            tools_used=list(d.get("tools_used", [])),
        ))
    return suspects


def _build_watchlist_triage(triage: TriageResult) -> TriageResult:
    """Build a synthetic TriageResult promoting watchlist to must_examine."""
    from commit_investigator.narrowing.models import TriageTier, TriagedCandidate

    promoted = []
    for i, wc in enumerate(triage.watchlist):
        promoted.append(TriagedCandidate(
            commit_id=wc.commit_id,
            tier=TriageTier.MUST_EXAMINE,
            tier_rank=i + 1,
            pre_score=wc.pre_score,
            rationale=f"Watchlist promoted for Phase 2b (original watchlist rank {wc.tier_rank})",
            file_overlap=wc.file_overlap,
            signal_count=wc.signal_count,
            original_rank=wc.original_rank,
            summary=wc.summary,
            files_changed=list(wc.files_changed),
            date=wc.date,
            retrieval_signal=wc.retrieval_signal,
            diff_summary=wc.diff_summary,
        ))
    return TriageResult(
        must_examine=promoted,
        watchlist=[],
        shortlist_size=triage.shortlist_size,
        total_scored=triage.total_scored,
    )
