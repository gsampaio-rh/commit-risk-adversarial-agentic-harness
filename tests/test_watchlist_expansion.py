"""Tests for Phase 2b: watchlist expansion trigger, merge, prompt, and orchestration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from commit_investigator.investigation.watchlist_expansion import (
    CONFIDENCE_THRESHOLD,
    MAX_FINAL_SUSPECTS,
    PHASE2B_MAX_TOOL_CALLS,
    PHASE2B_MAX_TURNS,
    PROMOTION_MARGIN,
    merge_suspects,
    run_phase2b,
    should_trigger_phase2b,
)
from commit_investigator.investigation.tools import build_scoped_tools
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.investigation.result import (
    InvestigationExitReason,
    Phase2bResult,
    Suspect,
)
from commit_investigator.investigation.prompts import build_phase2b_system_prompt
from commit_investigator.investigation.investigator import Phase2Result
from commit_investigator.models.candidates import CandidateCommit, CandidateSet
from commit_investigator.narrowing.models import TriagedCandidate, TriageResult, TriageTier

SHA_A = "aaa111bbb222ccc333ddd444eee555fff666aaa1"
SHA_B = "bbb222ccc333ddd444eee555fff666aaa111bbb2"
SHA_C = "ccc333ddd444eee555fff666aaa111bbb222ccc3"
SHA_D = "ddd444eee555fff666aaa111bbb222ccc333ddd4"
SHA_E = "eee555fff666aaa111bbb222ccc333ddd444eee5"
SHA_F = "fff666aaa111bbb222ccc333ddd444eee555fff6"


# ---------------------------------------------------------------------------
# Phase2bResult dataclass
# ---------------------------------------------------------------------------

class TestPhase2bResult:
    def test_defaults(self):
        r = Phase2bResult()
        assert r.suspects == []
        assert r.tool_calls == 0
        assert r.turns == 0
        assert r.trigger_reason == ""

    def test_with_values(self):
        s = Suspect(commit_id=SHA_A, confidence=0.8, mechanism="test")
        r = Phase2bResult(suspects=[s], tool_calls=5, turns=3, trigger_reason="low_confidence")
        assert len(r.suspects) == 1
        assert r.suspects[0].commit_id == SHA_A
        assert r.tool_calls == 5
        assert r.trigger_reason == "low_confidence"


# ---------------------------------------------------------------------------
# should_trigger_phase2b — truth table
# ---------------------------------------------------------------------------

def _p2_result(suspects: list[dict] | None = None, **kw) -> Phase2Result:
    return Phase2Result(suspects=suspects or [], **kw)


class TestShouldTriggerPhase2b:
    def test_no_suspects_triggers(self):
        triggered, reason = should_trigger_phase2b(_p2_result([]))
        assert triggered is True
        assert reason == "no_suspects"

    def test_low_confidence_triggers(self):
        suspects = [{"commit_id": SHA_A, "confidence": 0.5, "evidence_quotes": ["x"]}]
        triggered, reason = should_trigger_phase2b(_p2_result(suspects))
        assert triggered is True
        assert reason == "low_confidence"

    def test_no_evidence_on_top_suspect_triggers(self):
        suspects = [{"commit_id": SHA_A, "confidence": 0.8, "evidence_quotes": []}]
        triggered, reason = should_trigger_phase2b(_p2_result(suspects))
        assert triggered is True
        assert reason == "no_evidence"

    def test_all_clear_skips(self):
        suspects = [{"commit_id": SHA_A, "confidence": 0.8, "evidence_quotes": ["line"]}]
        triggered, reason = should_trigger_phase2b(_p2_result(suspects))
        assert triggered is False
        assert reason == "watchlist_skipped"

    def test_multiple_triggers_returns_first(self):
        """no_suspects takes priority over low_confidence."""
        triggered, reason = should_trigger_phase2b(_p2_result([]))
        assert triggered is True
        assert reason == "no_suspects"

    def test_boundary_confidence_exactly_threshold_does_not_trigger(self):
        """EC1: confidence == 0.6 is NOT < 0.6, so should not trigger."""
        suspects = [{"commit_id": SHA_A, "confidence": CONFIDENCE_THRESHOLD, "evidence_quotes": ["x"]}]
        triggered, reason = should_trigger_phase2b(_p2_result(suspects))
        assert triggered is False
        assert reason == "watchlist_skipped"

    def test_confidence_just_below_threshold_triggers(self):
        suspects = [{"commit_id": SHA_A, "confidence": 0.599, "evidence_quotes": ["x"]}]
        triggered, reason = should_trigger_phase2b(_p2_result(suspects))
        assert triggered is True
        assert reason == "low_confidence"

    def test_top_suspect_by_confidence_checked_for_evidence(self):
        """When multiple suspects, evidence check is on the highest-confidence one."""
        suspects = [
            {"commit_id": SHA_A, "confidence": 0.5, "evidence_quotes": ["has evidence"]},
            {"commit_id": SHA_B, "confidence": 0.8, "evidence_quotes": []},
        ]
        triggered, reason = should_trigger_phase2b(_p2_result(suspects))
        assert triggered is True
        assert reason == "no_evidence"


# ---------------------------------------------------------------------------
# merge_suspects
# ---------------------------------------------------------------------------

def _s(sha: str, confidence: float = 0.5, phase: str = "investigation",
       mechanism: str = "m", evidence_quotes: list[str] | None = None,
       tools_used: list[str] | None = None) -> Suspect:
    return Suspect(
        commit_id=sha, confidence=confidence, mechanism=mechanism,
        evidence_quotes=evidence_quotes or [], phase=phase,
        tools_used=tools_used or [],
    )


class TestMergeSuspects:
    def test_empty_phase2b_returns_phase2_only(self):
        p2 = [_s(SHA_A, 0.8), _s(SHA_B, 0.7)]
        result = merge_suspects(p2, [])
        assert len(result) == 2
        assert result[0].commit_id == SHA_A
        assert result[0].rank == 1

    def test_empty_phase2_returns_phase2b_only(self):
        p2b = [_s(SHA_C, 0.9, phase="watchlist_expansion")]
        result = merge_suspects([], p2b)
        assert len(result) == 1
        assert result[0].commit_id == SHA_C
        assert result[0].rank == 1

    def test_no_overlap_appends_phase2b_below(self):
        p2 = [_s(SHA_A, 0.8, evidence_quotes=["q1"])]
        p2b = [_s(SHA_B, 0.5, phase="watchlist_expansion")]
        result = merge_suspects(p2, p2b)
        assert len(result) == 2
        assert result[0].commit_id == SHA_A

    def test_overlap_merges_confidence_max(self):
        p2 = [_s(SHA_A, 0.6)]
        p2b = [_s(SHA_A, 0.9, phase="watchlist_expansion")]
        result = merge_suspects(p2, p2b)
        assert len(result) == 1
        assert result[0].confidence == 0.9

    def test_overlap_merges_evidence_union(self):
        """EC2: same SHA, different evidence — union without duplicates."""
        p2 = [_s(SHA_A, 0.8, evidence_quotes=["line1", "line2"])]
        p2b = [_s(SHA_A, 0.7, evidence_quotes=["line2", "line3"],
                   phase="watchlist_expansion")]
        result = merge_suspects(p2, p2b)
        assert len(result) == 1
        assert set(result[0].evidence_quotes) == {"line1", "line2", "line3"}

    def test_overlap_keeps_longer_mechanism(self):
        p2 = [_s(SHA_A, 0.8, mechanism="short")]
        p2b = [_s(SHA_A, 0.7, mechanism="this is a longer mechanism",
                   phase="watchlist_expansion")]
        result = merge_suspects(p2, p2b)
        assert result[0].mechanism == "this is a longer mechanism"

    def test_overlap_sets_phase_to_both(self):
        p2 = [_s(SHA_A, 0.8)]
        p2b = [_s(SHA_A, 0.7, phase="watchlist_expansion")]
        result = merge_suspects(p2, p2b)
        assert result[0].phase == "both"

    def test_promotion_when_confidence_exceeds_top_plus_margin(self):
        """AC6: 2b suspect with very high confidence promotes above P2."""
        p2 = [_s(SHA_A, 0.5, evidence_quotes=["q"])]
        high_conf = 0.5 + PROMOTION_MARGIN + 0.01
        p2b = [_s(SHA_B, high_conf, evidence_quotes=["q1", "q2"],
                   phase="watchlist_expansion")]
        result = merge_suspects(p2, p2b)
        assert result[0].commit_id == SHA_B

    def test_no_promotion_when_confidence_below_margin(self):
        p2 = [_s(SHA_A, 0.8, evidence_quotes=["q"])]
        p2b = [_s(SHA_B, 0.7, phase="watchlist_expansion")]
        result = merge_suspects(p2, p2b)
        shas = [s.commit_id for s in result]
        assert shas.index(SHA_A) < shas.index(SHA_B)

    def test_caps_at_5(self):
        p2 = [_s(f"{'a' * 39}{i}", 0.9 - i * 0.1, evidence_quotes=["q"])
              for i in range(4)]
        p2b = [_s(f"{'b' * 39}{i}", 0.5, evidence_quotes=["q"],
                   phase="watchlist_expansion") for i in range(4)]
        result = merge_suspects(p2, p2b)
        assert len(result) <= MAX_FINAL_SUSPECTS

    def test_sort_by_quote_count_then_confidence(self):
        """AC5: grounded_quote_count DESC, confidence DESC."""
        p2 = [
            _s(SHA_A, 0.9, evidence_quotes=["q1"]),
            _s(SHA_B, 0.7, evidence_quotes=["q1", "q2", "q3"]),
        ]
        result = merge_suspects(p2, [])
        assert result[0].commit_id == SHA_B

    def test_ranks_assigned_sequentially(self):
        p2 = [_s(SHA_A, 0.8), _s(SHA_B, 0.7), _s(SHA_C, 0.6)]
        result = merge_suspects(p2, [])
        assert [s.rank for s in result] == [1, 2, 3]

    def test_tools_used_merged_on_overlap(self):
        p2 = [_s(SHA_A, 0.8, tools_used=["get_commit_diff"])]
        p2b = [_s(SHA_A, 0.7, tools_used=["get_commit_diff", "get_blame"],
                   phase="watchlist_expansion")]
        result = merge_suspects(p2, p2b)
        assert "get_commit_diff" in result[0].tools_used
        assert "get_blame" in result[0].tools_used


# ---------------------------------------------------------------------------
# Shared fixtures for prompt and orchestrator tests
# ---------------------------------------------------------------------------

SHA_ME1 = "111111111111111111111111111111111111111a"
SHA_ME2 = "222222222222222222222222222222222222222b"
SHA_ME3 = "333333333333333333333333333333333333333c"
SHA_WL1 = "444444444444444444444444444444444444444d"
SHA_WL2 = "555555555555555555555555555555555555555e"
SHA_WL3 = "666666666666666666666666666666666666666f"
SHA_WL4 = "777777777777777777777777777777777777777a"


def _make_triage() -> TriageResult:
    def _tc(sha, tier, rank, score):
        return TriagedCandidate(
            commit_id=sha, tier=tier, tier_rank=rank, pre_score=score,
            rationale=f"Rank {rank}", file_overlap=score * 0.8,
            signal_count=2, original_rank=rank,
            summary=f"Commit {sha[:8]}",
            files_changed=["src/main.java", "src/util.java"],
            date="2024-01-15", retrieval_signal="blame",
        )
    return TriageResult(
        must_examine=[
            _tc(SHA_ME1, TriageTier.MUST_EXAMINE, 1, 0.85),
            _tc(SHA_ME2, TriageTier.MUST_EXAMINE, 2, 0.72),
            _tc(SHA_ME3, TriageTier.MUST_EXAMINE, 3, 0.60),
        ],
        watchlist=[
            _tc(SHA_WL1, TriageTier.WATCHLIST, 1, 0.50),
            _tc(SHA_WL2, TriageTier.WATCHLIST, 2, 0.45),
            _tc(SHA_WL3, TriageTier.WATCHLIST, 3, 0.40),
            _tc(SHA_WL4, TriageTier.WATCHLIST, 4, 0.35),
        ],
        shortlist_size=15,
        total_scored=100,
    )


def _mock_git():
    git = MagicMock()
    git.get_diff.return_value = "diff --git a/file.java\n+added line"
    git.get_commit_message.return_value = "Fix: updated logic"
    git.get_blame.return_value = "abc123 (Author 2024-01-01) line content"
    git.get_file_at_commit.return_value = "public class Main {}"
    return git


def _make_cs(*shas: str) -> CandidateSet:
    return CandidateSet(
        commits=[
            CandidateCommit(
                commit_id=sha, rank=i + 1, retrieval_signal="test",
                summary=f"Commit {sha[:8]}", files_changed=["src/main.java"],
                date="2024-01-01",
            )
            for i, sha in enumerate(shas)
        ],
        temporal_bound="abc123~1",
    )


# ---------------------------------------------------------------------------
# build_phase2b_system_prompt
# ---------------------------------------------------------------------------

class TestBuildPhase2bPrompt:
    def test_contains_all_watchlist_shas(self):
        triage = _make_triage()
        cs = _make_cs(SHA_WL1, SHA_WL2, SHA_WL3, SHA_WL4)
        registry = build_scoped_tools(_mock_git(), cs)
        prompt = build_phase2b_system_prompt(
            ProblemStatement(title="NPE", description="Null pointer", project="TEST"),
            triage, None, registry,
        )
        for sha in [SHA_WL1, SHA_WL2, SHA_WL3, SHA_WL4]:
            assert sha in prompt, f"Watchlist SHA {sha[:12]} missing"

    def test_excludes_must_examine_shas(self):
        triage = _make_triage()
        cs = _make_cs(SHA_WL1)
        registry = build_scoped_tools(_mock_git(), cs)
        prompt = build_phase2b_system_prompt(
            ProblemStatement(title="Bug", description="desc", project="TEST"),
            triage, None, registry,
        )
        for sha in [SHA_ME1, SHA_ME2, SHA_ME3]:
            assert sha not in prompt, f"Must-examine SHA {sha[:12]} should not appear"

    def test_includes_best_suspect_reference(self):
        triage = _make_triage()
        cs = _make_cs(SHA_WL1)
        registry = build_scoped_tools(_mock_git(), cs)
        best = Suspect(commit_id=SHA_ME1, confidence=0.75, mechanism="Changed null check")
        prompt = build_phase2b_system_prompt(
            ProblemStatement(title="Bug", description="desc", project="TEST"),
            triage, best, registry,
        )
        assert SHA_ME1[:12] in prompt
        assert "0.75" in prompt
        assert "Changed null check" in prompt

    def test_omits_reference_when_no_best_suspect(self):
        triage = _make_triage()
        cs = _make_cs(SHA_WL1)
        registry = build_scoped_tools(_mock_git(), cs)
        prompt = build_phase2b_system_prompt(
            ProblemStatement(title="Bug", description="desc", project="TEST"),
            triage, None, registry,
        )
        assert "Phase 2 Best Suspect" not in prompt

    def test_includes_tool_descriptions(self):
        triage = _make_triage()
        cs = _make_cs(SHA_WL1)
        registry = build_scoped_tools(_mock_git(), cs)
        prompt = build_phase2b_system_prompt(
            ProblemStatement(title="Bug", description="desc", project="TEST"),
            triage, None, registry,
        )
        assert "get_commit_diff" in prompt
        assert "get_blame" in prompt


# ---------------------------------------------------------------------------
# Orchestrator: run_phase2b
# ---------------------------------------------------------------------------

from commit_investigator.infra.llm import LLMResponse, MockLLMProvider


class _WatchlistToolThenSuspectsLLM(MockLLMProvider):
    """Diffs each watchlist SHA, then outputs suspects."""

    def __init__(self, shas: list[str]):
        self._shas = list(shas)
        self._call_count = 0

    def complete(self, messages, **kwargs) -> LLMResponse:
        self._call_count += 1
        if self._call_count <= len(self._shas):
            sha = self._shas[self._call_count - 1]
            content = (
                f'```tool\n{{"tool": "get_commit_diff", '
                f'"args": {{"commit_id": "{sha}"}}}}\n```'
            )
        else:
            suspects = [
                {"commit_id": sha, "confidence": 0.7 - i * 0.1,
                 "mechanism": f"watchlist mechanism {i}", "evidence_quotes": ["wl line"]}
                for i, sha in enumerate(self._shas[:2])
            ]
            content = f"```suspects\n{json.dumps(suspects)}\n```"
        return LLMResponse(content=content, tokens_used=100, model="mock")


class TestRunPhase2b:
    def _problem(self):
        return ProblemStatement(title="Bug", description="desc", project="TEST")

    def test_skip_when_trigger_not_met(self):
        """AC9: returns watchlist_skipped when Phase 2 is strong enough."""
        p2 = Phase2Result(
            suspects=[{"commit_id": SHA_ME1, "confidence": 0.8,
                       "evidence_quotes": ["line"], "mechanism": "m"}],
            exit_reason=InvestigationExitReason.NORMAL,
        )
        suspects, p2b_result, exit_reason = run_phase2b(
            p2, self._problem(), _make_triage(),
            _make_cs(SHA_ME1), _mock_git(), MockLLMProvider(),
        )
        assert exit_reason == InvestigationExitReason.WATCHLIST_SKIPPED
        assert p2b_result is None
        assert len(suspects) == 1
        assert suspects[0].commit_id == SHA_ME1

    def test_skip_when_empty_watchlist(self):
        """EC3: empty watchlist → skip even if trigger fires."""
        triage = _make_triage()
        triage.watchlist = []
        p2 = Phase2Result(suspects=[], exit_reason=InvestigationExitReason.NORMAL)
        suspects, p2b_result, exit_reason = run_phase2b(
            p2, self._problem(), triage,
            _make_cs(SHA_ME1), _mock_git(), MockLLMProvider(),
        )
        assert exit_reason == InvestigationExitReason.WATCHLIST_SKIPPED
        assert p2b_result is None

    def test_trigger_runs_investigation(self):
        """AC10: triggers investigation with reduced budget."""
        p2 = Phase2Result(
            suspects=[{"commit_id": SHA_ME1, "confidence": 0.4,
                       "evidence_quotes": ["q"], "mechanism": "m"}],
            exit_reason=InvestigationExitReason.NORMAL,
        )
        llm = _WatchlistToolThenSuspectsLLM([SHA_WL1, SHA_WL2])
        cs = _make_cs(SHA_ME1, SHA_WL1, SHA_WL2, SHA_WL3, SHA_WL4)
        suspects, p2b_result, exit_reason = run_phase2b(
            p2, self._problem(), _make_triage(), cs, _mock_git(), llm,
        )
        assert p2b_result is not None
        assert p2b_result.trigger_reason == "low_confidence"
        assert len(suspects) >= 1

    def test_empty_p2b_suspects_returns_p2_only(self):
        """EC4: Phase 2b finds nothing — result is Phase 2 suspects only."""
        p2 = Phase2Result(
            suspects=[{"commit_id": SHA_ME1, "confidence": 0.4,
                       "evidence_quotes": ["q"], "mechanism": "m"}],
            exit_reason=InvestigationExitReason.NORMAL,
        )
        llm = MockLLMProvider()  # returns empty, triggers force_conclude
        cs = _make_cs(SHA_ME1, SHA_WL1, SHA_WL2, SHA_WL3, SHA_WL4)
        suspects, p2b_result, exit_reason = run_phase2b(
            p2, self._problem(), _make_triage(), cs, _mock_git(), llm,
        )
        assert any(s.commit_id == SHA_ME1 for s in suspects)

    def test_no_suspects_triggers_phase2b(self):
        p2 = Phase2Result(suspects=[], exit_reason=InvestigationExitReason.NORMAL)
        llm = _WatchlistToolThenSuspectsLLM([SHA_WL1])
        cs = _make_cs(SHA_WL1, SHA_WL2, SHA_WL3, SHA_WL4)
        suspects, p2b_result, exit_reason = run_phase2b(
            p2, self._problem(), _make_triage(), cs, _mock_git(), llm,
        )
        assert p2b_result is not None
        assert p2b_result.trigger_reason == "no_suspects"

    def test_exit_reason_exhausted_when_budget_hit(self):
        """Budget/turns hit → watchlist_expansion_exhausted."""
        p2 = Phase2Result(suspects=[], exit_reason=InvestigationExitReason.NORMAL)
        llm = MockLLMProvider()  # never produces suspects → max_turns/force_conclude
        cs = _make_cs(SHA_WL1, SHA_WL2, SHA_WL3, SHA_WL4)
        suspects, p2b_result, exit_reason = run_phase2b(
            p2, self._problem(), _make_triage(), cs, _mock_git(), llm,
        )
        assert exit_reason == InvestigationExitReason.WATCHLIST_EXPANSION_EXHAUSTED

    def test_suspects_are_suspect_objects(self):
        """Return type is list[Suspect], not list[dict]."""
        p2 = Phase2Result(
            suspects=[{"commit_id": SHA_ME1, "confidence": 0.8,
                       "evidence_quotes": ["line"], "mechanism": "m"}],
            exit_reason=InvestigationExitReason.NORMAL,
        )
        suspects, _, _ = run_phase2b(
            p2, self._problem(), _make_triage(),
            _make_cs(SHA_ME1), _mock_git(), MockLLMProvider(),
        )
        assert all(isinstance(s, Suspect) for s in suspects)
