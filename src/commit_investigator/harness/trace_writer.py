"""InvestigationTrace writer — JSON file per investigation (ADR §Q4).

Produces structured traces for forensics, skill emergence, and debugging.
Storage: results/traces/{issue_key}/{run_id}.json
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class OutcomeRecord:
    """Investigation outcome metrics (ADR §Q4 OutcomeRecord)."""

    suspect_count: int = 0
    top_confidence: float = 0.0
    degraded: bool = False
    degraded_reason: str | None = None
    hit_at_5: bool | None = None
    mrr: float | None = None
    suspects: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suspect_count": self.suspect_count,
            "top_confidence": self.top_confidence,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "hit_at_5": self.hit_at_5,
            "mrr": self.mrr,
            "suspects": self.suspects,
        }


@dataclass
class TurnRecord:
    """Per-turn Stage 3 examination record (ADR §Q4 TurnRecord)."""

    turn: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    hypothesis_updates: list[str] = field(default_factory=list)
    completion_check: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "tool_calls": self.tool_calls,
            "hypothesis_updates": self.hypothesis_updates,
            "completion_check": self.completion_check,
        }


@dataclass
class EvidenceRecord:
    """Evidence quote with grounding status (ADR §Q4 EvidenceRecord)."""

    commit_id: str
    quote: str
    grounded: bool | None = None
    hypothesis_id: str | None = None
    turn: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_id": self.commit_id,
            "quote": self.quote,
            "grounded": self.grounded,
            "hypothesis_id": self.hypothesis_id,
            "turn": self.turn,
        }


@dataclass
class InvestigationTrace:
    """Structured investigation record per ADR §Q4."""

    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    issue_key: str = ""
    run_id: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    temporal_bound: str = ""
    candidate_set_size: int = 0
    retrieval_recall_100: bool | None = None
    pre_score_recall_15: bool | None = None
    triage_recall_7: bool | None = None
    exam_recall: bool | None = None
    phase2b_triggered: bool = False
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    candidates_examined: list[str] = field(default_factory=list)
    candidates_eliminated: list[dict[str, Any]] = field(default_factory=list)
    evidence_collected: list[EvidenceRecord] = field(default_factory=list)
    strategy_decisions: list[dict[str, Any]] = field(default_factory=list)
    examination_turns: list[TurnRecord] = field(default_factory=list)
    stage_timings: dict[str, float] = field(default_factory=dict)
    outcome: OutcomeRecord = field(default_factory=OutcomeRecord)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "issue_key": self.issue_key,
            "run_id": self.run_id,
            "temporal_bound": self.temporal_bound,
            "candidate_set_size": self.candidate_set_size,
            "retrieval_recall_100": self.retrieval_recall_100,
            "pre_score_recall_15": self.pre_score_recall_15,
            "triage_recall_7": self.triage_recall_7,
            "exam_recall": self.exam_recall,
            "phase2b_triggered": self.phase2b_triggered,
            "hypotheses": self.hypotheses,
            "candidates_examined": self.candidates_examined,
            "candidates_eliminated": self.candidates_eliminated,
            "evidence_collected": [e.to_dict() for e in self.evidence_collected],
            "strategy_decisions": self.strategy_decisions,
            "examination_turns": [t.to_dict() for t in self.examination_turns],
            "stage_timings": self.stage_timings,
            "outcome": self.outcome.to_dict(),
        }


class TraceWriter:
    """Writes InvestigationTrace to disk as JSON."""

    def __init__(self, traces_dir: str | Path = "results/traces") -> None:
        self._traces_dir = Path(traces_dir)

    def write(self, trace: InvestigationTrace) -> Path:
        """Write trace to results/traces/{issue_key}/{run_id}.json."""
        safe_run_id = trace.run_id.replace(":", "-").replace("+", "")
        dir_path = self._traces_dir / trace.issue_key
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"{safe_run_id}.json"
        file_path.write_text(
            json.dumps(trace.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        return file_path


def _extract_examined_shas(tool_trace: list[dict[str, Any]]) -> list[str]:
    """Extract commit SHAs that were diff'd from a tool trace."""
    return [
        tc["args"].get("commit_id", "")
        for tc in tool_trace
        if tc.get("tool") == "get_commit_diff" and tc["args"].get("commit_id")
    ]


def _build_outcome(
    suspects: list[dict[str, Any]], funnel: Any,
) -> OutcomeRecord:
    """Build OutcomeRecord from suspects and funnel metrics."""
    top_conf = max((s.get("confidence", 0.0) for s in suspects), default=0.0)
    return OutcomeRecord(
        suspect_count=len(suspects),
        top_confidence=top_conf,
        degraded=not suspects,
        degraded_reason=None if suspects else "no_suspects",
        hit_at_5=funnel.hit_at_5,
        mrr=funnel.mrr,
        suspects=suspects[:5],
    )


def build_v42_trace(
    issue_key: str,
    temporal_bound: str,
    candidate_count: int,
    suspects: list[dict[str, Any]],
    tool_trace: list[dict[str, Any]],
    funnel: Any,
    exit_reason: str = "",
    retrieval_ms: float = 0.0,
    agent_ms: float = 0.0,
) -> InvestigationTrace:
    """Build a V4.2 InvestigationTrace with funnel metrics populated.

    Args:
        funnel: FunnelMetrics instance (imported as Any to avoid circular import).
    """
    return InvestigationTrace(
        issue_key=issue_key,
        temporal_bound=temporal_bound,
        candidate_set_size=candidate_count,
        retrieval_recall_100=funnel.recall_100,
        pre_score_recall_15=funnel.pre_score_recall_15,
        triage_recall_7=funnel.triage_recall_7,
        exam_recall=funnel.exam_recall,
        phase2b_triggered=funnel.phase2b_triggered,
        candidates_examined=_extract_examined_shas(tool_trace),
        examination_turns=[
            TurnRecord(turn=i + 1, tool_calls=[{"tool": tc["tool"], "args": tc["args"]}])
            for i, tc in enumerate(tool_trace)
        ],
        stage_timings={
            "retrieval": round(retrieval_ms, 1),
            "agent_total": round(agent_ms, 1),
        },
        outcome=_build_outcome(suspects, funnel),
    )


def build_scoped_trace(
    issue_key: str,
    temporal_bound: str,
    suspects: list[dict[str, Any]],
    tool_trace: list[dict[str, Any]],
    candidate_count: int,
    recall_found: bool,
    retrieval_ms: float,
    agent_ms: float,
) -> InvestigationTrace:
    """Build an InvestigationTrace from scoped investigation tool records."""
    top_confidence = max((s.get("confidence", 0.0) for s in suspects), default=0.0)
    return InvestigationTrace(
        issue_key=issue_key,
        temporal_bound=temporal_bound,
        candidate_set_size=candidate_count,
        retrieval_recall_100=recall_found,
        candidates_examined=_extract_examined_shas(tool_trace),
        examination_turns=[
            TurnRecord(turn=i + 1, tool_calls=[{"tool": tc["tool"], "args": tc["args"]}])
            for i, tc in enumerate(tool_trace)
        ],
        stage_timings={"retrieval": round(retrieval_ms, 1), "agent_total": round(agent_ms, 1)},
        outcome=OutcomeRecord(
            suspect_count=len(suspects),
            top_confidence=top_confidence,
            degraded=not suspects,
            degraded_reason=None if suspects else "no_suspects",
            suspects=suspects[:5],
        ),
    )
