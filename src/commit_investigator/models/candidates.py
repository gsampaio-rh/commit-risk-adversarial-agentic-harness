"""CandidateSet and CandidateCommit — input pipeline output for V4 agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

from commit_investigator.models._serde import instance_to_dict


@dataclass(frozen=True)
class CandidateCommit:
    """One ranked commit in a CandidateSet."""

    commit_id: str
    rank: int
    retrieval_signal: str
    summary: str
    files_changed: list[str] = field(default_factory=list)
    date: str = ""

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            commit_id=data["commit_id"],
            rank=data["rank"],
            retrieval_signal=data["retrieval_signal"],
            summary=data["summary"],
            files_changed=list(data.get("files_changed", [])),
            date=data.get("date", ""),
        )


@dataclass
class CandidateSet:
    """Ranked commits produced by the retrieval stage."""

    commits: list[CandidateCommit] = field(default_factory=list)
    retrieval_metadata: dict[str, Any] = field(default_factory=dict)
    temporal_bound: str = ""

    def to_dict(self) -> dict[str, Any]:
        return instance_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            commits=[CandidateCommit.from_dict(item) for item in data.get("commits", [])],
            retrieval_metadata=dict(data.get("retrieval_metadata", {})),
            temporal_bound=data.get("temporal_bound", ""),
        )
