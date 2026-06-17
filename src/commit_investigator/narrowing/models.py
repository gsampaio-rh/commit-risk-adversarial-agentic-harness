"""Data models for Phase 1 narrowing: pre-score + deterministic triage.

These types flow between Phase 0 (Retrieval) and Phase 2 (Investigation):
  CandidateSet → ScoredShortlist → TriageResult → Phase 2 input
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Self

from commit_investigator.models._serde import instance_to_dict


class TriageTier(str, Enum):
    """Assignment tier from deterministic triage."""

    MUST_EXAMINE = "must_examine"
    WATCHLIST = "watchlist"


@dataclass(frozen=True)
class ScoredCandidate:
    """A candidate with computed pre-score components."""

    commit_id: str
    original_rank: int
    pre_score: float
    file_overlap: float
    signal_count: int
    summary: str = ""
    files_changed: list[str] = field(default_factory=list)
    date: str = ""
    retrieval_signal: str = ""
    diff_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            commit_id=data["commit_id"],
            original_rank=data["original_rank"],
            pre_score=data["pre_score"],
            file_overlap=data["file_overlap"],
            signal_count=data["signal_count"],
            summary=data.get("summary", ""),
            files_changed=list(data.get("files_changed", [])),
            date=data.get("date", ""),
            retrieval_signal=data.get("retrieval_signal", ""),
            diff_summary=data.get("diff_summary", ""),
        )


@dataclass
class ScoredShortlist:
    """Top-K candidates after pre-scoring (Phase 1a output)."""

    candidates: list[ScoredCandidate] = field(default_factory=list)
    total_scored: int = 0

    @property
    def size(self) -> int:
        return len(self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            candidates=[ScoredCandidate.from_dict(c) for c in data.get("candidates", [])],
            total_scored=data.get("total_scored", 0),
        )


@dataclass(frozen=True)
class TriagedCandidate:
    """A candidate assigned to a triage tier with a template rationale."""

    commit_id: str
    tier: TriageTier
    tier_rank: int  # 1-based rank within the tier
    pre_score: float
    rationale: str
    file_overlap: float = 0.0
    signal_count: int = 0
    original_rank: int = 0
    summary: str = ""
    files_changed: list[str] = field(default_factory=list)
    date: str = ""
    retrieval_signal: str = ""
    diff_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            commit_id=data["commit_id"],
            tier=TriageTier(data["tier"]),
            tier_rank=data["tier_rank"],
            pre_score=data["pre_score"],
            rationale=data["rationale"],
            file_overlap=data.get("file_overlap", 0.0),
            signal_count=data.get("signal_count", 0),
            original_rank=data.get("original_rank", 0),
            summary=data.get("summary", ""),
            files_changed=list(data.get("files_changed", [])),
            date=data.get("date", ""),
            retrieval_signal=data.get("retrieval_signal", ""),
            diff_summary=data.get("diff_summary", ""),
        )


@dataclass
class TriageResult:
    """Phase 1 output: triaged candidates ready for Phase 2 investigation.

    must_examine (3) go into Phase 2 prompt immediately.
    watchlist (4) are held for Phase 2b conditional expansion.
    """

    must_examine: list[TriagedCandidate] = field(default_factory=list)
    watchlist: list[TriagedCandidate] = field(default_factory=list)
    shortlist_size: int = 0
    total_scored: int = 0

    @property
    def all_candidates(self) -> list[TriagedCandidate]:
        return self.must_examine + self.watchlist

    @property
    def must_examine_shas(self) -> list[str]:
        return [c.commit_id for c in self.must_examine]

    @property
    def watchlist_shas(self) -> list[str]:
        return [c.commit_id for c in self.watchlist]

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            must_examine=[TriagedCandidate.from_dict(c) for c in data.get("must_examine", [])],
            watchlist=[TriagedCandidate.from_dict(c) for c in data.get("watchlist", [])],
            shortlist_size=data.get("shortlist_size", 0),
            total_scored=data.get("total_scored", 0),
        )
