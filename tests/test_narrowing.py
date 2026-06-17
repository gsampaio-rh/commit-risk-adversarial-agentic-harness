"""Unit tests for the Phase 1 narrowing module (pre-score + deterministic triage)."""

from __future__ import annotations

import json

import pytest

from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.models.candidates import CandidateCommit, CandidateSet
from commit_investigator.narrowing import (
    MUST_EXAMINE_SIZE,
    WATCHLIST_SIZE,
    ScoredCandidate,
    ScoredShortlist,
    TriagedCandidate,
    TriageResult,
    TriageTier,
    assign_tiers,
    compute_file_overlap,
    compute_pre_scores,
    get_signal_count,
    narrow_candidates,
)


# --- Fixtures ---


def _make_candidate(
    commit_id: str = "a" * 40,
    rank: int = 1,
    signal: str = "file_log",
    summary: str = "Some commit",
    files: list[str] | None = None,
    date: str = "2024-01-01",
) -> CandidateCommit:
    return CandidateCommit(
        commit_id=commit_id,
        rank=rank,
        retrieval_signal=signal,
        summary=summary,
        files_changed=files or [],
        date=date,
    )


def _make_candidate_set(n: int = 20) -> CandidateSet:
    """Create a fixture set with n candidates, varying signals and files."""
    commits = []
    for i in range(n):
        sha = f"{i:040d}"
        signal_parts = ["file_log"]
        if i % 3 == 0:
            signal_parts.append("blame")
        if i % 5 == 0:
            signal_parts.append("pickaxe")
        files = [f"src/pkg/Class{i}.java"]
        if i < 5:
            files.append("src/pkg/TargetFile.java")
        commits.append(_make_candidate(
            commit_id=sha,
            rank=i + 1,
            signal=",".join(signal_parts),
            summary=f"Commit {i}",
            files=files,
            date=f"2024-01-{i+1:02d}",
        ))
    return CandidateSet(commits=commits, temporal_bound="fix123~1")


def _make_problem(extracted_files: list[str] | None = None) -> ProblemStatement:
    return ProblemStatement(
        title="NullPointerException in TargetFile",
        description="When calling process(), NPE is thrown",
        project="testproject",
        issue_key="TEST-123",
        extracted_files=extracted_files or ["TargetFile.java"],
        extracted_symbols=["TargetFile"],
        extracted_keywords=["nullpointerexception", "targetfile"],
    )


# --- Pre-score: file_overlap ---


class TestFileOverlap:
    def test_no_extracted_files_returns_zero(self) -> None:
        assert compute_file_overlap(["src/Foo.java"], []) == 0.0

    def test_no_changed_files_returns_zero(self) -> None:
        assert compute_file_overlap([], ["Foo.java"]) == 0.0

    def test_exact_match(self) -> None:
        assert compute_file_overlap(["Foo.java"], ["Foo.java"]) == 1.0

    def test_suffix_match(self) -> None:
        assert compute_file_overlap(
            ["src/main/java/Foo.java"], ["Foo.java"]
        ) == 1.0

    def test_partial_overlap(self) -> None:
        result = compute_file_overlap(
            ["src/Foo.java", "src/Bar.java"],
            ["Foo.java", "Baz.java"],
        )
        assert result == pytest.approx(0.5)

    def test_case_insensitive(self) -> None:
        assert compute_file_overlap(["src/foo.java"], ["Foo.java"]) == 1.0


# --- Pre-score: signal_count ---


class TestSignalCount:
    def test_single_signal(self) -> None:
        c = _make_candidate(signal="file_log")
        assert get_signal_count(c) == 1

    def test_multiple_signals(self) -> None:
        c = _make_candidate(signal="file_log,blame,pickaxe")
        assert get_signal_count(c) == 3

    def test_recency_fallback_excluded(self) -> None:
        c = _make_candidate(signal="file_log,recency_fallback")
        assert get_signal_count(c) == 1

    def test_empty_signal(self) -> None:
        c = _make_candidate(signal="")
        assert get_signal_count(c) == 0


# --- Pre-score: compute_pre_scores ---


class TestComputePreScores:
    def test_empty_candidate_set(self) -> None:
        cs = CandidateSet(commits=[])
        problem = _make_problem()
        result = compute_pre_scores(cs, problem)
        assert result.size == 0
        assert result.total_scored == 0

    def test_single_candidate(self) -> None:
        cs = CandidateSet(commits=[_make_candidate(files=["src/TargetFile.java"])])
        problem = _make_problem()
        result = compute_pre_scores(cs, problem)
        assert result.size == 1
        sc = result.candidates[0]
        assert sc.file_overlap == 1.0
        assert sc.signal_count == 1
        # single candidate: norm_rank=0, norm_sc=1
        # 0.5*1.0 + 0.3*1.0 + 0.2*1.0 = 1.0
        assert sc.pre_score == pytest.approx(1.0)

    def test_shortlist_size_respected(self) -> None:
        cs = _make_candidate_set(50)
        problem = _make_problem()
        result = compute_pre_scores(cs, problem, shortlist_size=15)
        assert result.size == 15
        assert result.total_scored == 50

    def test_sorted_descending_by_prescore(self) -> None:
        cs = _make_candidate_set(20)
        problem = _make_problem()
        result = compute_pre_scores(cs, problem, shortlist_size=20)
        scores = [c.pre_score for c in result.candidates]
        assert scores == sorted(scores, reverse=True)

    def test_file_overlap_boosts_rank(self) -> None:
        """Candidates with file overlap should score higher than those without."""
        cs = _make_candidate_set(20)
        problem = _make_problem(["TargetFile.java"])
        result = compute_pre_scores(cs, problem, shortlist_size=20)
        top5_ids = {c.commit_id for c in result.candidates[:5]}
        # Candidates 0-4 have TargetFile.java in their files
        expected_ids = {f"{i:040d}" for i in range(5)}
        assert top5_ids & expected_ids, "File-overlapping candidates should rank high"

    def test_custom_weights(self) -> None:
        cs = CandidateSet(commits=[
            _make_candidate(commit_id="a" * 40, rank=1, signal="file_log,blame",
                            files=["TargetFile.java"]),
            _make_candidate(commit_id="b" * 40, rank=2, signal="file_log",
                            files=[]),
        ])
        problem = _make_problem()
        # Weight file_overlap=1.0, others=0 → first candidate wins
        result = compute_pre_scores(
            cs, problem,
            weights={"file_overlap": 1.0, "signal_count": 0.0, "rank": 0.0},
        )
        assert result.candidates[0].commit_id == "a" * 40


# --- Triage: assign_tiers ---


class TestAssignTiers:
    def _make_shortlist(self, n: int = 15) -> ScoredShortlist:
        candidates = [
            ScoredCandidate(
                commit_id=f"{i:040d}",
                original_rank=i + 1,
                pre_score=1.0 - i * 0.05,
                file_overlap=0.5 if i < 5 else 0.0,
                signal_count=3 - min(i, 2),
                summary=f"Commit {i}",
            )
            for i in range(n)
        ]
        return ScoredShortlist(candidates=candidates, total_scored=100)

    def test_default_tier_sizes(self) -> None:
        shortlist = self._make_shortlist(15)
        result = assign_tiers(shortlist)
        assert len(result.must_examine) == MUST_EXAMINE_SIZE
        assert len(result.watchlist) == WATCHLIST_SIZE

    def test_must_examine_gets_top3(self) -> None:
        shortlist = self._make_shortlist(15)
        result = assign_tiers(shortlist)
        must_ids = [c.commit_id for c in result.must_examine]
        expected = [f"{i:040d}" for i in range(3)]
        assert must_ids == expected

    def test_watchlist_gets_next4(self) -> None:
        shortlist = self._make_shortlist(15)
        result = assign_tiers(shortlist)
        watch_ids = [c.commit_id for c in result.watchlist]
        expected = [f"{i:040d}" for i in range(3, 7)]
        assert watch_ids == expected

    def test_tier_enum_values(self) -> None:
        shortlist = self._make_shortlist(15)
        result = assign_tiers(shortlist)
        for c in result.must_examine:
            assert c.tier == TriageTier.MUST_EXAMINE
        for c in result.watchlist:
            assert c.tier == TriageTier.WATCHLIST

    def test_tier_rank_sequential(self) -> None:
        shortlist = self._make_shortlist(15)
        result = assign_tiers(shortlist)
        assert [c.tier_rank for c in result.must_examine] == [1, 2, 3]
        assert [c.tier_rank for c in result.watchlist] == [1, 2, 3, 4]

    def test_rationale_is_template_string(self) -> None:
        shortlist = self._make_shortlist(15)
        result = assign_tiers(shortlist)
        for c in result.must_examine:
            assert "must-examine" in c.rationale
            assert "Pre-score rank" in c.rationale
        for c in result.watchlist:
            assert "watchlist" in c.rationale

    def test_small_shortlist_partial_fill(self) -> None:
        """If shortlist has fewer than 7, fill what's available."""
        shortlist = self._make_shortlist(5)
        result = assign_tiers(shortlist)
        assert len(result.must_examine) == 3
        assert len(result.watchlist) == 2

    def test_shortlist_smaller_than_must_examine(self) -> None:
        shortlist = self._make_shortlist(2)
        result = assign_tiers(shortlist)
        assert len(result.must_examine) == 2
        assert len(result.watchlist) == 0

    def test_custom_tier_sizes(self) -> None:
        shortlist = self._make_shortlist(15)
        result = assign_tiers(shortlist, must_examine_size=5, watchlist_size=3)
        assert len(result.must_examine) == 5
        assert len(result.watchlist) == 3

    def test_metadata_preserved(self) -> None:
        shortlist = self._make_shortlist(15)
        result = assign_tiers(shortlist)
        assert result.shortlist_size == 15
        assert result.total_scored == 100


# --- End-to-end: narrow_candidates ---


class TestNarrowCandidates:
    def test_full_pipeline(self) -> None:
        cs = _make_candidate_set(50)
        problem = _make_problem()
        result = narrow_candidates(cs, problem)
        assert len(result.must_examine) == 3
        assert len(result.watchlist) == 4
        assert result.total_scored == 50
        assert result.shortlist_size == 15

    def test_empty_input(self) -> None:
        cs = CandidateSet(commits=[])
        problem = _make_problem()
        result = narrow_candidates(cs, problem)
        assert len(result.must_examine) == 0
        assert len(result.watchlist) == 0
        assert result.total_scored == 0

    def test_must_examine_shas_property(self) -> None:
        cs = _make_candidate_set(20)
        problem = _make_problem()
        result = narrow_candidates(cs, problem)
        assert len(result.must_examine_shas) == 3
        assert all(len(sha) == 40 for sha in result.must_examine_shas)

    def test_watchlist_shas_property(self) -> None:
        cs = _make_candidate_set(20)
        problem = _make_problem()
        result = narrow_candidates(cs, problem)
        assert len(result.watchlist_shas) == 4
        assert all(len(sha) == 40 for sha in result.watchlist_shas)

    def test_no_overlap_between_tiers(self) -> None:
        cs = _make_candidate_set(50)
        problem = _make_problem()
        result = narrow_candidates(cs, problem)
        must_set = set(result.must_examine_shas)
        watch_set = set(result.watchlist_shas)
        assert must_set.isdisjoint(watch_set)

    def test_custom_shortlist_and_tier_sizes(self) -> None:
        cs = _make_candidate_set(50)
        problem = _make_problem()
        result = narrow_candidates(
            cs, problem,
            shortlist_size=10,
            must_examine_size=2,
            watchlist_size=3,
        )
        assert len(result.must_examine) == 2
        assert len(result.watchlist) == 3
        assert result.shortlist_size == 10


# --- Serialization ---


class TestNarrowingSerialization:
    def test_scored_candidate_round_trip(self) -> None:
        sc = ScoredCandidate(
            commit_id="a" * 40,
            original_rank=3,
            pre_score=0.75,
            file_overlap=0.5,
            signal_count=2,
            summary="Test commit",
            files_changed=["src/Foo.java"],
            date="2024-01-01",
            retrieval_signal="file_log,blame",
        )
        restored = ScoredCandidate.from_dict(json.loads(json.dumps(sc.to_dict())))
        assert restored.commit_id == sc.commit_id
        assert restored.pre_score == sc.pre_score
        assert restored.file_overlap == sc.file_overlap

    def test_triage_result_round_trip(self) -> None:
        cs = _make_candidate_set(20)
        problem = _make_problem()
        result = narrow_candidates(cs, problem)
        data = result.to_dict()
        restored = TriageResult.from_dict(json.loads(json.dumps(data)))
        assert len(restored.must_examine) == 3
        assert len(restored.watchlist) == 4
        assert restored.must_examine[0].tier == TriageTier.MUST_EXAMINE
        assert restored.watchlist[0].tier == TriageTier.WATCHLIST

    def test_scored_shortlist_round_trip(self) -> None:
        cs = _make_candidate_set(20)
        problem = _make_problem()
        shortlist = compute_pre_scores(cs, problem, shortlist_size=10)
        data = shortlist.to_dict()
        restored = ScoredShortlist.from_dict(json.loads(json.dumps(data)))
        assert restored.size == 10
        assert restored.total_scored == 20
