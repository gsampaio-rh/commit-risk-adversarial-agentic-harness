"""Investigation result — data structures used by eval scoring.

Includes Suspect (typed attribution output), InvestigationExitReason (explicit
exit tracking per C6), and InvestigationResult (eval-facing container).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Self

from commit_investigator.investigation.trace_writer import InvestigationTrace


class InvestigationExitReason(str, Enum):
    """Why an investigation ended — per V4.2 ADR §Exit Conditions / C6."""

    NORMAL = "normal"
    BUDGET_EXHAUSTED = "budget_exhausted"
    MAX_TURNS = "max_turns"
    FORCED_CONCLUDE = "forced_conclude"
    STALL = "stall"
    PROVIDER_ERROR = "provider_error"
    EMPTY_CANDIDATES = "empty_candidates"
    WATCHLIST_EXPANSION_EXHAUSTED = "watchlist_expansion_exhausted"
    WATCHLIST_SKIPPED = "watchlist_skipped"


@dataclass
class Suspect:
    """A ranked attribution suspect — unified type replacing raw dicts.

    Produced by Phase 2 investigation. to_dict() outputs the shape
    consumed by D3/D6 scoring and trace writers (backward compat).
    """

    commit_id: str
    rank: int = 0
    confidence: float = 0.0
    mechanism: str = ""
    evidence_quotes: list[str] = field(default_factory=list)
    phase: str = "investigation"
    tools_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_id": self.commit_id,
            "rank": self.rank,
            "confidence": self.confidence,
            "mechanism": self.mechanism,
            "evidence_quotes": list(self.evidence_quotes),
            "phase": self.phase,
            "tools_used": list(self.tools_used),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            commit_id=data["commit_id"],
            rank=data.get("rank", 0),
            confidence=data.get("confidence", 0.0),
            mechanism=data.get("mechanism", ""),
            evidence_quotes=list(data.get("evidence_quotes", [])),
            phase=data.get("phase", "investigation"),
            tools_used=list(data.get("tools_used", [])),
        )


@dataclass
class Phase2bResult:
    """Phase 2b watchlist expansion output — per V4.2 ADR §Phase 2b."""

    suspects: list[Suspect] = field(default_factory=list)
    tool_calls: int = 0
    turns: int = 0
    trigger_reason: str = ""


@dataclass
class InvestigationResult:
    """Complete result of an investigation for eval scoring."""

    issue_key: str
    suspects: list[dict[str, Any]] = field(default_factory=list)
    trace: InvestigationTrace | None = None
    retrieval_recall: bool = False
    error: str | None = None
    exit_reason: InvestigationExitReason | None = None
    elapsed_s: float = 0.0
