"""V3 evaluation metrics: Hit@k, MRR, Retrieval Recall, Evidence grounding.

All metrics compare the agent's BugAttributionReport against
ground truth (bug_hash from commit_links.csv).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from commit_investigator.analysis.evidence_tagger import (
    SuspectEvidenceScore,
    score_suspect_evidence,
)
from commit_investigator.context.git_context import GitContextProvider
from commit_investigator.pipeline.orchestrator import BugAttributionReport


@dataclass
class AttributionEvalResult:
    """Evaluation result for a single bug attribution."""

    bug_hash: str
    project: str
    issue_key: str
    hit_at_1: bool
    hit_at_3: bool
    hit_at_5: bool
    mrr: float
    retrieval_recall: bool
    evidence_grounding_rate: float
    suspect_count: int
    tool_calls: int
    tokens_used: int
    cost_usd: float
    elapsed_ms: float
    model: str = ""
    suspect_details: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bug_hash": self.bug_hash,
            "project": self.project,
            "issue_key": self.issue_key,
            "hit_at_1": self.hit_at_1,
            "hit_at_3": self.hit_at_3,
            "hit_at_5": self.hit_at_5,
            "mrr": self.mrr,
            "retrieval_recall": self.retrieval_recall,
            "evidence_grounding_rate": self.evidence_grounding_rate,
            "suspect_count": self.suspect_count,
            "tool_calls": self.tool_calls,
            "tokens_used": self.tokens_used,
            "cost_usd": self.cost_usd,
            "elapsed_ms": self.elapsed_ms,
            "model": self.model,
            "suspect_details": self.suspect_details,
        }


@dataclass
class AggregateEvalReport:
    """Aggregate evaluation metrics across all cases."""

    total: int
    hit_at_1: float
    hit_at_3: float
    hit_at_5: float
    mrr: float
    retrieval_recall: float
    evidence_grounding_rate: float
    total_cost_usd: float
    avg_tool_calls: float
    avg_tokens: float
    avg_elapsed_ms: float
    results: list[AttributionEvalResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "hit_at_1": self.hit_at_1,
            "hit_at_3": self.hit_at_3,
            "hit_at_5": self.hit_at_5,
            "mrr": self.mrr,
            "retrieval_recall": self.retrieval_recall,
            "evidence_grounding_rate": self.evidence_grounding_rate,
            "total_cost_usd": self.total_cost_usd,
            "avg_tool_calls": self.avg_tool_calls,
            "avg_tokens": self.avg_tokens,
            "avg_elapsed_ms": self.avg_elapsed_ms,
            "results": [r.to_dict() for r in self.results],
        }


def _normalize_hash(h: str) -> str:
    """Normalize a commit hash to lowercase for comparison."""
    return h.strip().lower()


def _find_rank(suspects_ids: list[str], bug_hash: str) -> int | None:
    """Find the 1-based rank of bug_hash in the suspects list.

    Uses prefix matching (12+ chars) to handle short vs full SHAs.
    Returns None if not found.
    """
    bug_norm = _normalize_hash(bug_hash)
    for i, sid in enumerate(suspects_ids):
        sid_norm = _normalize_hash(sid)
        if sid_norm == bug_norm:
            return i + 1
        if len(sid_norm) >= 12 and len(bug_norm) >= 12:
            if sid_norm.startswith(bug_norm[:12]) or bug_norm.startswith(sid_norm[:12]):
                return i + 1
    return None


def _attached_evidence_scores(
    report: BugAttributionReport,
) -> dict[str, dict[str, Any]] | None:
    """Return commit_id -> score dict when pipeline attached evidence_scores."""
    raw = report.metadata.get("evidence_scores")
    if raw is None:
        return None
    return {entry["commit_id"]: entry for entry in raw}


def _score_from_attached(entry: dict[str, Any]) -> SuspectEvidenceScore:
    """Reconstruct SuspectEvidenceScore from metadata entry (no diff re-fetch)."""
    return SuspectEvidenceScore(
        commit_id=entry["commit_id"],
        total_quotes=entry["total_quotes"],
        grounded_quotes=entry["grounded_quotes"],
        grounding_rate=entry["grounding_rate"],
        per_quote=[],
    )


def evaluate_attribution(
    report: BugAttributionReport,
    bug_hash: str,
    project: str,
    issue_key: str,
    git_provider: GitContextProvider | None = None,
) -> AttributionEvalResult:
    """Evaluate a single BugAttributionReport against ground truth.

    Args:
        report: The agent's attribution output.
        bug_hash: Ground truth bug-introducing commit hash.
        project: Project name.
        issue_key: JIRA issue key.
        git_provider: For fetching suspect diffs (evidence grounding).

    Returns:
        AttributionEvalResult with all metrics.
    """
    suspect_ids = [s.commit_id for s in report.suspects]
    rank = _find_rank(suspect_ids, bug_hash)

    hit_1 = rank == 1 if rank else False
    hit_3 = rank is not None and rank <= 3
    hit_5 = rank is not None and rank <= 5
    mrr_val = 1.0 / rank if rank else 0.0

    tool_trace_commits = set()
    for t in report.tool_trace:
        cid = t.args.get("commit_id", "")
        if cid:
            tool_trace_commits.add(_normalize_hash(cid))

    retrieval = _normalize_hash(bug_hash)[:12] in {
        c[:12] for c in tool_trace_commits
    }

    attached_scores = _attached_evidence_scores(report)
    grounding_rates = []
    suspect_details = []
    for suspect in report.suspects:
        if attached_scores is not None and suspect.commit_id in attached_scores:
            evidence_score = _score_from_attached(attached_scores[suspect.commit_id])
        else:
            diff = None
            if git_provider and suspect.commit_id:
                try:
                    diff = git_provider.get_diff(suspect.commit_id)
                except Exception:
                    diff = None
            evidence_score = score_suspect_evidence(
                commit_id=suspect.commit_id,
                evidence_quotes=suspect.evidence_quotes,
                diff=diff,
            )
        grounding_rates.append(evidence_score.grounding_rate)
        suspect_details.append({
            "commit_id": suspect.commit_id,
            "rank": suspect.rank,
            "confidence": suspect.confidence,
            "grounding_rate": evidence_score.grounding_rate,
            "grounded_quotes": evidence_score.grounded_quotes,
            "total_quotes": evidence_score.total_quotes,
            "is_ground_truth": _normalize_hash(suspect.commit_id)[:12] == _normalize_hash(bug_hash)[:12],
        })

    avg_grounding = sum(grounding_rates) / len(grounding_rates) if grounding_rates else 0.0

    return AttributionEvalResult(
        bug_hash=bug_hash,
        project=project,
        issue_key=issue_key,
        hit_at_1=hit_1,
        hit_at_3=hit_3,
        hit_at_5=hit_5,
        mrr=mrr_val,
        retrieval_recall=retrieval,
        evidence_grounding_rate=avg_grounding,
        suspect_count=len(report.suspects),
        tool_calls=report.metadata.get("tool_calls", 0),
        tokens_used=report.metadata.get("tokens_used", 0),
        cost_usd=report.metadata.get("total_cost_usd", 0.0),
        elapsed_ms=report.metadata.get("elapsed_ms", 0.0),
        model=report.metadata.get("model", ""),
        suspect_details=suspect_details,
    )


def aggregate_results(results: list[AttributionEvalResult]) -> AggregateEvalReport:
    """Compute aggregate metrics from per-case eval results."""
    n = len(results)
    if n == 0:
        return AggregateEvalReport(
            total=0, hit_at_1=0, hit_at_3=0, hit_at_5=0, mrr=0,
            retrieval_recall=0, evidence_grounding_rate=0,
            total_cost_usd=0, avg_tool_calls=0, avg_tokens=0, avg_elapsed_ms=0,
        )

    return AggregateEvalReport(
        total=n,
        hit_at_1=sum(r.hit_at_1 for r in results) / n,
        hit_at_3=sum(r.hit_at_3 for r in results) / n,
        hit_at_5=sum(r.hit_at_5 for r in results) / n,
        mrr=sum(r.mrr for r in results) / n,
        retrieval_recall=sum(r.retrieval_recall for r in results) / n,
        evidence_grounding_rate=sum(r.evidence_grounding_rate for r in results) / n,
        total_cost_usd=sum(r.cost_usd for r in results),
        avg_tool_calls=sum(r.tool_calls for r in results) / n,
        avg_tokens=sum(r.tokens_used for r in results) / n,
        avg_elapsed_ms=sum(r.elapsed_ms for r in results) / n,
        results=results,
    )
