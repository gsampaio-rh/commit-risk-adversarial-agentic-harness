"""Unit tests for core model dataclasses."""

from __future__ import annotations

import json
from typing import Any

from commit_investigator.models import CandidateCommit, CandidateSet


def _round_trip(model: Any) -> dict[str, Any]:
    """Serialize to JSON and back; return final dict for identity check."""
    json_str = json.dumps(model.to_dict())
    restored = type(model).from_dict(json.loads(json_str))
    return restored.to_dict()


class TestCandidateModels:
    def test_empty_candidate_set_round_trip(self) -> None:
        candidate_set = CandidateSet(
            commits=[],
            retrieval_metadata={"strategy": "combined"},
            temporal_bound="abc123~1",
        )
        assert _round_trip(candidate_set) == candidate_set.to_dict()

    def test_candidate_commit_fields(self) -> None:
        commit = CandidateCommit(
            commit_id="a" * 40,
            rank=1,
            retrieval_signal="file_log",
            summary="Fix null pointer",
            files_changed=["src/Foo.java"],
            date="2024-01-01",
        )
        restored = CandidateCommit.from_dict(_round_trip(commit))
        assert restored.commit_id == commit.commit_id
        assert restored.rank == 1
        assert restored.files_changed == ["src/Foo.java"]

    def test_candidate_set_with_commits_round_trip(self) -> None:
        candidate_set = CandidateSet(
            commits=[
                CandidateCommit(
                    commit_id="def456",
                    rank=2,
                    retrieval_signal="keyword_grep",
                    summary="Remove guard",
                    files_changed=["Parser.java"],
                    date="2024-02-01",
                )
            ],
            retrieval_metadata={"recall_estimate": 0.45},
            temporal_bound="abc123~1",
        )
        assert _round_trip(candidate_set) == candidate_set.to_dict()
