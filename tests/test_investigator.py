"""Tests for V4.2 scoped tools and RevisedScopedInvestigator."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from commit_investigator.investigation.tools import build_scoped_tools
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.investigation.prompts import (
    build_phase2_system_prompt,
    parse_suspects,
    parse_tool_calls,
)
from commit_investigator.narrowing.models import TriagedCandidate, TriageResult, TriageTier
from commit_investigator.investigation.investigator import (
    MustExamineGate,
    NudgeAction,
    NudgeLadder,
    NudgeResult,
    Phase2Result,
    RevisedScopedInvestigator,
    RollingSummary,
    ToolCallCache,
)
from commit_investigator.investigation.result import (
    InvestigationExitReason,
    InvestigationResult,
    Suspect,
)
from commit_investigator.infra.llm import (
    LLMResponse,
    MockLLMProvider,
    ProviderUnavailableError,
    get_provider,
)
from commit_investigator.models.candidates import CandidateCommit, CandidateSet


SHA_IN = "aaa111bbb222ccc333ddd444eee555fff666aaa1"
SHA_OUT = "zzz999yyy888xxx777www666vvv555uuu444ttt3"


def _make_candidate_set(*shas: str) -> CandidateSet:
    return CandidateSet(
        commits=[
            CandidateCommit(
                commit_id=sha,
                rank=i + 1,
                retrieval_signal="test",
                summary=f"Commit {sha[:8]}",
                files_changed=["src/main.java"],
                date="2024-01-01",
            )
            for i, sha in enumerate(shas)
        ],
        temporal_bound="abc123~1",
    )


def _mock_git():
    git = MagicMock()
    git.get_diff.return_value = "diff --git a/file.java\n+added line"
    git.get_commit_message.return_value = "Fix: updated logic"
    git.get_blame.return_value = "abc123 (Author 2024-01-01) line content"
    git.get_file_at_commit.return_value = "public class Main {}"
    return git


class TestBuildScopedTools:
    def test_only_examination_tools(self):
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_IN))
        names = registry.tool_names
        assert "get_commit_diff" in names
        assert "search_commits_by_file" not in names

    def test_rejects_out_of_set_sha(self):
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_IN))
        result = registry.execute("get_commit_diff", commit_id=SHA_OUT)
        assert "not in the CandidateSet" in result

    def test_accepts_short_sha_prefix(self):
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_IN))
        assert "Error" not in registry.execute("get_commit_diff", commit_id=SHA_IN[:12])

    def test_get_file_at_commit_rejects_out_of_set(self):
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_IN))
        result = registry.execute("get_file_at_commit", commit_id=SHA_OUT, path="file.java")
        assert "not in the CandidateSet" in result

    def test_get_commit_message_scoped(self):
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_IN))
        assert "Error" not in registry.execute("get_commit_message", commit_id=SHA_IN)
        assert "not in the CandidateSet" in registry.execute("get_commit_message", commit_id=SHA_OUT)

    def test_empty_candidate_set_rejects_all_shas(self):
        registry = build_scoped_tools(_mock_git(), CandidateSet())
        result = registry.execute("get_commit_diff", commit_id=SHA_IN)
        assert "not in the CandidateSet" in result

    def test_blame_does_not_validate_sha(self):
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_IN))
        assert "Error" not in registry.execute("get_blame", path="src/main.java")


class _CountingLLM(MockLLMProvider):
    """Tracks how many times complete() is invoked."""

    def __init__(self, content: str = "Still thinking..."):
        self._content = content
        self.complete_count = 0

    def complete(self, messages, **kwargs) -> LLMResponse:
        self.complete_count += 1
        return LLMResponse(
            content=self._content, tokens_used=10, estimated_cost=0.0, model="mock",
        )


class _OosThenValidLLM(MockLLMProvider):
    """Turn 1: OOS SHA diff; turn 2: valid diff; turn 3: suspects."""

    def __init__(self, valid_sha: str, invalid_sha: str = SHA_OUT):
        self._valid_sha = valid_sha
        self._invalid_sha = invalid_sha
        self._call_count = 0

    def complete(self, messages, **kwargs) -> LLMResponse:
        self._call_count += 1
        if self._call_count == 1:
            content = (
                f'```tool\n{{"tool": "get_commit_diff", '
                f'"args": {{"commit_id": "{self._invalid_sha}"}}}}\n```'
            )
        elif self._call_count == 2:
            content = (
                f'```tool\n{{"tool": "get_commit_diff", '
                f'"args": {{"commit_id": "{self._valid_sha}"}}}}\n```'
            )
        else:
            content = (
                f'```suspects\n[{{"commit_id": "{self._valid_sha}", '
                f'"confidence": 0.9, "mechanism": "test", "evidence_quotes": ["line"]}}]\n```'
            )
        return LLMResponse(content=content, tokens_used=50, estimated_cost=0.001, model="mock")


class TestParsing:
    def test_parse_tool_calls(self):
        text = '```tool\n{"tool": "get_commit_diff", "args": {"commit_id": "abc"}}\n```'
        assert parse_tool_calls(text)[0]["tool"] == "get_commit_diff"

    def test_parse_suspects(self):
        text = '```suspects\n[{"commit_id": "abc", "confidence": 0.9}]\n```'
        assert parse_suspects(text)[0]["commit_id"] == "abc"


SHA_ME1 = "111111111111111111111111111111111111111a"
SHA_ME2 = "222222222222222222222222222222222222222b"
SHA_ME3 = "333333333333333333333333333333333333333c"
SHA_WL1 = "444444444444444444444444444444444444444d"


def _make_triage_result() -> TriageResult:
    """Build a TriageResult with 3 must-examine + 1 watchlist for testing."""
    def _tc(sha: str, tier: TriageTier, rank: int, score: float) -> TriagedCandidate:
        return TriagedCandidate(
            commit_id=sha,
            tier=tier,
            tier_rank=rank,
            pre_score=score,
            rationale=f"Rank {rank} by pre-score ({score:.3f})",
            file_overlap=score * 0.8,
            signal_count=2,
            original_rank=rank,
            summary=f"Commit {sha[:8]}",
            files_changed=["src/main.java", "src/util.java"],
            date="2024-01-15",
            retrieval_signal="blame",
        )

    return TriageResult(
        must_examine=[
            _tc(SHA_ME1, TriageTier.MUST_EXAMINE, 1, 0.85),
            _tc(SHA_ME2, TriageTier.MUST_EXAMINE, 2, 0.72),
            _tc(SHA_ME3, TriageTier.MUST_EXAMINE, 3, 0.60),
        ],
        watchlist=[_tc(SHA_WL1, TriageTier.WATCHLIST, 1, 0.50)],
        shortlist_size=15,
        total_scored=100,
    )


class TestNudgeLadder:
    def test_first_idle_returns_directive(self):
        ladder = NudgeLadder([SHA_ME1, SHA_ME2])
        result = ladder.evaluate_idle(0, 15, [SHA_ME1, SHA_ME2])
        assert result.action == NudgeAction.DIRECTIVE
        assert SHA_ME1 in result.message

    def test_second_idle_returns_warning(self):
        ladder = NudgeLadder([SHA_ME1])
        ladder.evaluate_idle(5, 15, [SHA_ME1])
        result = ladder.evaluate_idle(5, 15, [SHA_ME1])
        assert result.action == NudgeAction.WARNING
        assert "5/15" in result.message

    def test_third_idle_returns_force_conclude(self):
        ladder = NudgeLadder([SHA_ME1])
        ladder.evaluate_idle(0, 15, [SHA_ME1])
        ladder.evaluate_idle(0, 15, [SHA_ME1])
        result = ladder.evaluate_idle(0, 15, [SHA_ME1])
        assert result.action == NudgeAction.FORCE_CONCLUDE

    def test_reset_idle_restarts_ladder(self):
        ladder = NudgeLadder([SHA_ME1])
        ladder.evaluate_idle(0, 15, [SHA_ME1])
        ladder.evaluate_idle(0, 15, [SHA_ME1])
        ladder.reset_idle()
        result = ladder.evaluate_idle(0, 15, [SHA_ME1])
        assert result.action == NudgeAction.DIRECTIVE

    def test_suspects_without_diff_rejected(self):
        ladder = NudgeLadder([SHA_ME1])
        result = ladder.evaluate_suspects_without_diff()
        assert result.action == NudgeAction.REJECT_SUSPECTS
        assert "no diff examined" in result.message

    def test_directive_targets_first_unexamined(self):
        ladder = NudgeLadder([SHA_ME1, SHA_ME2, SHA_ME3])
        result = ladder.evaluate_idle(0, 15, [SHA_ME2, SHA_ME3])
        assert SHA_ME2 in result.message


class TestMustExamineGate:
    def test_empty_initially(self):
        gate = MustExamineGate([SHA_ME1, SHA_ME2])
        assert not gate.is_satisfied()
        assert gate.coverage == 0.0
        assert len(gate.unexamined_shas) == 2

    def test_satisfied_after_one_diff(self):
        gate = MustExamineGate([SHA_ME1, SHA_ME2])
        gate.record_diff(SHA_ME1, success=True)
        assert gate.is_satisfied()
        assert SHA_ME1 in gate.examined_shas

    def test_failed_diff_not_counted(self):
        gate = MustExamineGate([SHA_ME1])
        gate.record_diff(SHA_ME1, success=False)
        assert not gate.is_satisfied()

    def test_non_must_examine_sha_ignored(self):
        gate = MustExamineGate([SHA_ME1])
        gate.record_diff(SHA_OUT, success=True)
        assert not gate.is_satisfied()

    def test_coverage_partial(self):
        gate = MustExamineGate([SHA_ME1, SHA_ME2, SHA_ME3])
        gate.record_diff(SHA_ME1, success=True)
        assert gate.coverage == pytest.approx(1.0 / 3.0)
        gate.record_diff(SHA_ME2, success=True)
        assert gate.coverage == pytest.approx(2.0 / 3.0)

    def test_unexamined_shas_decreases(self):
        gate = MustExamineGate([SHA_ME1, SHA_ME2])
        assert len(gate.unexamined_shas) == 2
        gate.record_diff(SHA_ME1, success=True)
        assert len(gate.unexamined_shas) == 1
        assert SHA_ME2 in gate.unexamined_shas

    def test_prefix_match(self):
        gate = MustExamineGate([SHA_ME1])
        gate.record_diff(SHA_ME1[:12], success=True)
        assert gate.is_satisfied()

    def test_empty_required_always_satisfied(self):
        gate = MustExamineGate([])
        assert gate.is_satisfied()
        assert gate.coverage == 1.0


class TestToolCallCache:
    def test_miss_returns_none(self):
        cache = ToolCallCache()
        assert cache.get("get_commit_diff", {"commit_id": SHA_IN}) is None

    def test_hit_after_put(self):
        cache = ToolCallCache()
        cache.put("get_commit_diff", {"commit_id": SHA_IN}, "diff output here")
        assert cache.get("get_commit_diff", {"commit_id": SHA_IN}) == "diff output here"

    def test_different_args_different_entries(self):
        cache = ToolCallCache()
        cache.put("get_commit_diff", {"commit_id": SHA_IN}, "result-a")
        cache.put("get_commit_diff", {"commit_id": SHA_OUT}, "result-b")
        assert cache.get("get_commit_diff", {"commit_id": SHA_IN}) == "result-a"
        assert cache.get("get_commit_diff", {"commit_id": SHA_OUT}) == "result-b"
        assert cache.size == 2

    def test_dedup_message(self):
        cache = ToolCallCache()
        msg = cache.dedup_message("get_commit_diff", {"commit_id": SHA_IN})
        assert "Already examined" in msg
        assert "get_commit_diff" in msg

    def test_different_tools_same_args(self):
        cache = ToolCallCache()
        cache.put("get_commit_diff", {"commit_id": SHA_IN}, "diff")
        assert cache.get("get_commit_message", {"commit_id": SHA_IN}) is None


class TestRollingSummary:
    def test_empty_initially(self):
        rs = RollingSummary()
        assert rs.text == ""
        assert rs.line_count == 0

    def test_add_tool_result(self):
        rs = RollingSummary()
        rs.add_tool_result("get_commit_diff", {"commit_id": SHA_IN}, "diff --git a/file.java")
        assert rs.line_count == 1
        assert "get_commit_diff" in rs.text
        assert SHA_IN[:12] in rs.text

    def test_multiple_results_accumulate(self):
        rs = RollingSummary()
        rs.add_tool_result("get_commit_diff", {"commit_id": SHA_IN}, "diff output")
        rs.add_tool_result("get_blame", {"path": "src/Main.java"}, "blame output")
        assert rs.line_count == 2

    def test_trims_to_max_chars(self):
        rs = RollingSummary(max_chars=100)
        for i in range(50):
            rs.add_tool_result("get_commit_diff", {"commit_id": f"sha{i:040d}"}, "x" * 50)
        assert len(rs.text) <= 100

    def test_oldest_lines_dropped_first(self):
        rs = RollingSummary(max_chars=200)
        rs.add_tool_result("get_commit_diff", {"commit_id": "first_sha"}, "first result")
        rs.add_tool_result("get_commit_diff", {"commit_id": "second_sha"}, "second result")
        for i in range(20):
            rs.add_tool_result("get_blame", {"path": f"file{i}.java"}, "x" * 30)
        assert "first_sha" not in rs.text


class TestBuildPhase2Prompt:
    def test_contains_all_must_examine_shas(self):
        triage = _make_triage_result()
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_ME1, SHA_ME2, SHA_ME3))
        prompt = build_phase2_system_prompt(
            ProblemStatement(title="NPE in parse()", description="Null pointer", project="TEST"),
            triage, registry,
        )
        for sha in [SHA_ME1, SHA_ME2, SHA_ME3]:
            assert sha in prompt, f"Must-examine SHA {sha[:12]} missing from prompt"

    def test_does_not_contain_watchlist_shas(self):
        triage = _make_triage_result()
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_ME1, SHA_ME2, SHA_ME3))
        prompt = build_phase2_system_prompt(
            ProblemStatement(title="Bug", description="desc", project="TEST"),
            triage, registry,
        )
        assert SHA_WL1 not in prompt

    def test_includes_tool_descriptions(self):
        triage = _make_triage_result()
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_ME1))
        prompt = build_phase2_system_prompt(
            ProblemStatement(title="Bug", description="desc", project="TEST"),
            triage, registry,
        )
        assert "get_commit_diff" in prompt
        assert "get_blame" in prompt

    def test_includes_bug_report(self):
        triage = _make_triage_result()
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_ME1))
        prompt = build_phase2_system_prompt(
            ProblemStatement(title="NPE in parse()", description="Stack trace here", project="TEST"),
            triage, registry,
        )
        assert "NPE in parse()" in prompt
        assert "Stack trace here" in prompt

    def test_includes_pre_score_and_file_overlap(self):
        triage = _make_triage_result()
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_ME1))
        prompt = build_phase2_system_prompt(
            ProblemStatement(title="Bug", description="desc", project="TEST"),
            triage, registry,
        )
        assert "pre_score=0.850" in prompt
        assert "file_overlap=" in prompt

    def test_strategy_mentions_must_examine(self):
        triage = _make_triage_result()
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_ME1))
        prompt = build_phase2_system_prompt(
            ProblemStatement(title="Bug", description="desc", project="TEST"),
            triage, registry,
        )
        assert "MUST examine" in prompt or "must-examine" in prompt.lower()

    def test_output_format_has_suspect_fields(self):
        triage = _make_triage_result()
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_ME1))
        prompt = build_phase2_system_prompt(
            ProblemStatement(title="Bug", description="desc", project="TEST"),
            triage, registry,
        )
        assert "mechanism" in prompt
        assert "evidence_quotes" in prompt


class _ToolThenSuspectsLLM(MockLLMProvider):
    def __init__(self, sha: str, *, tool: str = "get_commit_diff"):
        self._sha = sha
        self._tool = tool
        self._call_count = 0

    def complete(self, messages, **kwargs) -> LLMResponse:
        self._call_count += 1
        if self._call_count == 1:
            content = (
                f'```tool\n{{"tool": "{self._tool}", '
                f'"args": {{"commit_id": "{self._sha}"}}}}\n```'
            )
        else:
            content = (
                f'```suspects\n[{{"commit_id": "{self._sha}", '
                f'"confidence": 0.85, "mechanism": "test", "evidence_quotes": ["line"]}}]\n```'
            )
        return LLMResponse(content=content, tokens_used=100, estimated_cost=0.001, model="mock")


class _StaticLLM(MockLLMProvider):
    def __init__(self, content: str):
        self._content = content

    def complete(self, messages, **kwargs) -> LLMResponse:
        return LLMResponse(content=self._content, tokens_used=10, estimated_cost=0.0, model="mock")


class TestSuspect:
    def _make_suspect(self, **overrides) -> Suspect:
        defaults = {
            "commit_id": SHA_IN,
            "rank": 1,
            "confidence": 0.85,
            "mechanism": "Changed null check in parse()",
            "evidence_quotes": ["- if (x != null)", "+ if (x == null)"],
            "phase": "investigation",
            "tools_used": ["get_commit_diff", "get_blame"],
        }
        defaults.update(overrides)
        return Suspect(**defaults)

    def test_construction_and_fields(self):
        s = self._make_suspect()
        assert s.commit_id == SHA_IN
        assert s.rank == 1
        assert s.confidence == 0.85
        assert s.phase == "investigation"
        assert len(s.tools_used) == 2

    def test_defaults(self):
        s = Suspect(commit_id=SHA_IN)
        assert s.rank == 0
        assert s.confidence == 0.0
        assert s.mechanism == ""
        assert s.evidence_quotes == []
        assert s.phase == "investigation"
        assert s.tools_used == []

    def test_to_dict_shape(self):
        d = self._make_suspect().to_dict()
        assert d["commit_id"] == SHA_IN
        assert d["confidence"] == 0.85
        assert d["mechanism"] == "Changed null check in parse()"
        assert isinstance(d["evidence_quotes"], list)
        assert len(d["evidence_quotes"]) == 2

    def test_to_dict_backward_compat_keys(self):
        """D3/D6 scoring consumes commit_id, confidence, mechanism, evidence_quotes."""
        d = self._make_suspect().to_dict()
        for key in ("commit_id", "confidence", "mechanism", "evidence_quotes"):
            assert key in d, f"Missing backward-compat key: {key}"

    def test_round_trip_serde(self):
        original = self._make_suspect()
        rebuilt = Suspect.from_dict(original.to_dict())
        assert rebuilt.commit_id == original.commit_id
        assert rebuilt.rank == original.rank
        assert rebuilt.confidence == original.confidence
        assert rebuilt.mechanism == original.mechanism
        assert rebuilt.evidence_quotes == original.evidence_quotes
        assert rebuilt.phase == original.phase
        assert rebuilt.tools_used == original.tools_used

    def test_empty_evidence_quotes_serde(self):
        s = self._make_suspect(evidence_quotes=[])
        d = s.to_dict()
        assert d["evidence_quotes"] == []
        rebuilt = Suspect.from_dict(d)
        assert rebuilt.evidence_quotes == []

    def test_from_dict_missing_optional_fields(self):
        minimal = {"commit_id": SHA_IN}
        s = Suspect.from_dict(minimal)
        assert s.commit_id == SHA_IN
        assert s.rank == 0
        assert s.confidence == 0.0


class TestInvestigationExitReason:
    def test_all_nine_values(self):
        expected = {
            "normal", "budget_exhausted", "max_turns", "forced_conclude",
            "stall", "provider_error", "empty_candidates",
            "watchlist_expansion_exhausted", "watchlist_skipped",
        }
        actual = {r.value for r in InvestigationExitReason}
        assert actual == expected

    def test_string_compatible(self):
        reason = InvestigationExitReason.NORMAL
        assert str(reason) == "InvestigationExitReason.NORMAL"
        assert reason.value == "normal"
        assert f"Exit: {reason.value}" == "Exit: normal"

    def test_enum_from_value(self):
        assert InvestigationExitReason("budget_exhausted") == InvestigationExitReason.BUDGET_EXHAUSTED

    def test_is_str_subclass(self):
        reason = InvestigationExitReason.FORCED_CONCLUDE
        assert isinstance(reason, str)


class TestInvestigationResultExtended:
    def test_backward_compat_defaults(self):
        r = InvestigationResult(issue_key="TEST-1")
        assert r.suspects == []
        assert r.exit_reason is None
        assert r.elapsed_s == 0.0
        assert r.error is None

    def test_with_exit_reason(self):
        r = InvestigationResult(
            issue_key="TEST-1",
            exit_reason=InvestigationExitReason.NORMAL,
            elapsed_s=12.5,
        )
        assert r.exit_reason == InvestigationExitReason.NORMAL
        assert r.elapsed_s == 12.5

    def test_existing_usage_pattern_unchanged(self):
        r = InvestigationResult(issue_key="TEST-1")
        r.suspects = [{"commit_id": SHA_IN, "confidence": 0.9}]
        r.retrieval_recall = True
        assert r.suspects[0]["commit_id"] == SHA_IN


class _Phase2ToolThenSuspectsLLM(MockLLMProvider):
    """Simulates V4.2 flow: diff each must-examine, then suspects."""

    def __init__(self, must_examine_shas: list[str]):
        self._shas = list(must_examine_shas)
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
                {"commit_id": sha, "confidence": 0.9 - i * 0.1,
                 "mechanism": f"test mechanism {i}", "evidence_quotes": ["line"]}
                for i, sha in enumerate(self._shas)
            ]
            content = f"```suspects\n{json.dumps(suspects)}\n```"
        return LLMResponse(content=content, tokens_used=100, model="mock")


class _DuplicateCallLLM(MockLLMProvider):
    """Calls the same tool twice on same SHA, then suspects."""

    def __init__(self, sha: str):
        self._sha = sha
        self._call_count = 0

    def complete(self, messages, **kwargs) -> LLMResponse:
        self._call_count += 1
        if self._call_count <= 2:
            content = (
                f'```tool\n{{"tool": "get_commit_diff", '
                f'"args": {{"commit_id": "{self._sha}"}}}}\n```'
            )
        else:
            content = (
                f'```suspects\n[{{"commit_id": "{self._sha}", '
                f'"confidence": 0.85, "mechanism": "test", "evidence_quotes": ["x"]}}]\n```'
            )
        return LLMResponse(content=content, tokens_used=50, model="mock")


class TestRevisedScopedInvestigator:
    def _make_investigator(self, llm, triage=None, candidate_set=None, **kwargs):
        triage = triage or _make_triage_result()
        cs = candidate_set or _make_candidate_set(SHA_ME1, SHA_ME2, SHA_ME3, SHA_WL1)
        return RevisedScopedInvestigator(
            llm=llm,
            problem=ProblemStatement(title="Bug", description="desc", project="TEST"),
            triage=triage,
            candidate_set=cs,
            git=_mock_git(),
            **kwargs,
        )

    def test_produces_suspects_after_examining_must_examine(self):
        llm = _Phase2ToolThenSuspectsLLM([SHA_ME1, SHA_ME2, SHA_ME3])
        inv = self._make_investigator(llm)
        result = inv.investigate()
        assert isinstance(result, Phase2Result)
        assert len(result.suspects) >= 1
        assert result.exit_reason == InvestigationExitReason.NORMAL
        assert result.diff_examined is True

    def test_returns_exit_reason_max_turns(self):
        llm = _CountingLLM("Still thinking...")
        inv = self._make_investigator(llm, max_turns=2)
        result = inv.investigate()
        assert result.exit_reason in (
            InvestigationExitReason.MAX_TURNS,
            InvestigationExitReason.FORCED_CONCLUDE,
        )
        assert result.suspects == []

    def test_empty_candidates_returns_immediately(self):
        triage = _make_triage_result()
        inv = self._make_investigator(
            MockLLMProvider(),
            triage=triage,
            candidate_set=CandidateSet(),
        )
        result = inv.investigate()
        assert result.exit_reason == InvestigationExitReason.EMPTY_CANDIDATES
        assert result.suspects == []

    def test_cache_dedup_prevents_double_execution(self):
        llm = _DuplicateCallLLM(SHA_ME1)
        inv = self._make_investigator(llm)
        result = inv.investigate()
        actual_calls = [tc for tc in result.tool_trace if tc.tool == "get_commit_diff"]
        assert len(actual_calls) == 1, "Second call should be cached"
        assert result.exit_reason == InvestigationExitReason.NORMAL

    def test_must_examine_coverage_tracked(self):
        llm = _Phase2ToolThenSuspectsLLM([SHA_ME1, SHA_ME2, SHA_ME3])
        inv = self._make_investigator(llm)
        result = inv.investigate()
        assert result.must_examine_coverage > 0.0
        assert "must_examine_coverage" in result.metadata

    def test_rejects_suspects_without_any_diff(self):
        suspects_only = (
            f'```suspects\n[{{"commit_id": "{SHA_ME1}", '
            f'"confidence": 0.9, "mechanism": "x", "evidence_quotes": ["y"]}}]\n```'
        )
        llm = _StaticLLM(suspects_only)
        inv = self._make_investigator(llm, max_turns=4)
        result = inv.investigate()
        assert result.suspects == []

    def test_nudge_escalation_to_force_conclude(self):
        llm = _CountingLLM("I'm thinking about this...")
        inv = self._make_investigator(llm, max_turns=5)
        result = inv.investigate()
        assert result.exit_reason == InvestigationExitReason.FORCED_CONCLUDE

    def test_metadata_has_exit_reason(self):
        llm = _Phase2ToolThenSuspectsLLM([SHA_ME1])
        inv = self._make_investigator(llm)
        result = inv.investigate()
        assert "exit_reason" in result.metadata
        assert result.metadata["exit_reason"] == "normal"

    def test_respects_max_tool_calls(self):
        many_calls_content = "\n".join(
            f'```tool\n{{"tool": "get_commit_diff", "args": {{"commit_id": "{SHA_ME1}"}}}}\n```'
            for _ in range(20)
        )
        llm = _StaticLLM(many_calls_content)
        inv = self._make_investigator(llm, max_tool_calls=5, max_turns=3)
        result = inv.investigate()
        real_calls = len(result.tool_trace)
        assert real_calls <= 5


class TestGetProvider:
    def test_default_returns_mock_when_no_keys(self, monkeypatch):
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("DISABLE_LOCAL_LLM", "1")
        provider = get_provider()
        assert isinstance(provider, MockLLMProvider)

    def test_fail_fast_raises_when_no_provider(self, monkeypatch):
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("DISABLE_LOCAL_LLM", "1")
        with pytest.raises(ProviderUnavailableError, match="No real LLM provider"):
            get_provider(fail_fast=True)

    def test_eval_strict_env_activates_fail_fast(self, monkeypatch):
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("DISABLE_LOCAL_LLM", "1")
        monkeypatch.setenv("EVAL_STRICT", "1")
        with pytest.raises(ProviderUnavailableError):
            get_provider()

    def test_phase_investigation_uses_investigation_model(self, monkeypatch):
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("DISABLE_LOCAL_LLM", "1")
        monkeypatch.setenv("INVESTIGATION_MODEL", "qwen3:8b")
        from commit_investigator.infra.llm import OpenAIProvider
        provider = get_provider(phase="investigation")
        assert isinstance(provider, OpenAIProvider)
        assert provider.model_name == "qwen3:8b"

    def test_no_phase_ignores_investigation_model(self, monkeypatch):
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("DISABLE_LOCAL_LLM", "1")
        monkeypatch.setenv("INVESTIGATION_MODEL", "qwen3:8b")
        provider = get_provider()
        assert isinstance(provider, MockLLMProvider)

    def test_backward_compat_no_args(self, monkeypatch):
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("DISABLE_LOCAL_LLM", "1")
        monkeypatch.delenv("EVAL_STRICT", raising=False)
        provider = get_provider(prefer_real=True)
        assert isinstance(provider, MockLLMProvider)
