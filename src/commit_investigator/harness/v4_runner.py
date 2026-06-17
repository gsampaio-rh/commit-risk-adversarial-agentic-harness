"""V4 investigation runner — wires input pipeline + harness + trace writer.

This is the top-level entry point for running a V4 investigation on an eval case.
It replaces the V3 AgentOrchestrator path in run_eval.py.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from commit_investigator.harness.harness import InvestigationHarness, InvestigationOutcome
from commit_investigator.harness.llm_protocol import LLMProvider
from commit_investigator.harness.trace_writer import (
    InvestigationTrace,
    OutcomeRecord,
    TraceWriter,
    TurnRecord,
)
from commit_investigator.models.candidates import CandidateSet
from commit_investigator.retrieval import compute_recall_at_k, prepare_investigation


@dataclass
class V4InvestigationResult:
    """Complete result of a V4 investigation for eval scoring."""

    issue_key: str
    suspects: list[dict[str, Any]] = field(default_factory=list)
    trace: InvestigationTrace | None = None
    retrieval_recall: bool = False
    outcome: InvestigationOutcome | None = None
    error: str | None = None


def run_v4_investigation(
    title: str,
    description: str,
    project: str,
    issue_key: str,
    repo_path: str | Path,
    temporal_bound: str,
    ground_truth_sha: str,
    llm: LLMProvider,
    *,
    traces_dir: str | Path = "results/traces",
) -> V4InvestigationResult:
    """Run a complete V4 investigation: input pipeline → harness → trace.

    Args:
        title: JIRA issue title.
        description: JIRA issue description.
        project: Project identifier (e.g., "CASSANDRA").
        issue_key: JIRA issue key.
        repo_path: Path to cloned repo.
        temporal_bound: Git ref for temporal bound.
        ground_truth_sha: Bug-introducing commit SHA (for recall evaluation).
        llm: LLM provider for the investigation harness.
        traces_dir: Where to write trace files.

    Returns:
        V4InvestigationResult with suspects, trace, and recall metric.
    """
    result = V4InvestigationResult(issue_key=issue_key)
    start = time.time()

    try:
        retrieval_result = prepare_investigation(
            source=(title, description),
            repo_path=repo_path,
            temporal_bound=temporal_bound,
            project=project,
            issue_key=issue_key,
        )
    except Exception as e:
        result.error = f"Input pipeline failed: {e}"
        return result

    retrieval_time = time.time() - start
    candidate_set = retrieval_result.candidate_set
    problem = retrieval_result.problem_statement

    recall_diag = compute_recall_at_k(candidate_set, ground_truth_sha, k=100)
    result.retrieval_recall = recall_diag.found

    harness_start = time.time()
    harness = InvestigationHarness(
        llm=llm,
        problem_statement=problem,
        candidate_set=candidate_set,
    )
    outcome = harness.run()
    harness_time = time.time() - harness_start

    result.suspects = outcome.suspects
    result.outcome = outcome

    trace = InvestigationTrace(
        issue_key=issue_key,
        temporal_bound=temporal_bound,
        candidate_set_size=len(candidate_set.commits),
        retrieval_recall_100=recall_diag.found,
        stage_timings={
            "retrieval": round(retrieval_time * 1000, 1),
            "agent_total": round(harness_time * 1000, 1),
        },
        outcome=OutcomeRecord(
            suspect_count=len(outcome.suspects),
            top_confidence=outcome.top_confidence,
            degraded=outcome.degraded,
            degraded_reason=outcome.degraded_reason,
            suspects=outcome.suspects[:5],
        ),
    )

    if outcome.brief:
        trace.hypotheses = [
            {
                "id": h.id,
                "statement": h.statement,
                "status": "formed",
                "reason": "",
                "stage": 2,
                "turn": None,
            }
            for h in outcome.brief.hypotheses
        ]

    trace.examination_turns = [
        TurnRecord(
            turn=t.turn,
            tool_calls=t.tool_calls,
            completion_check=t.completion_check,
        )
        for t in outcome.examination_turns
    ]

    trace.evidence_collected = []
    for i, ev_text in enumerate(outcome.evidence):
        from commit_investigator.harness.trace_writer import EvidenceRecord
        trace.evidence_collected.append(EvidenceRecord(
            commit_id="",
            quote=ev_text[:200],
            turn=i + 1,
        ))

    writer = TraceWriter(traces_dir)
    writer.write(trace)
    result.trace = trace

    return result


def _extract_top_confidence(suspects: list[dict[str, Any]]) -> float:
    if not suspects:
        return 0.0
    confidences = [s.get("confidence", 0.0) for s in suspects]
    return max(confidences) if confidences else 0.0
