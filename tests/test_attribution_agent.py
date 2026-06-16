"""Tests for the V3 Attribution Agent (orchestrator)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from commit_investigator.infra.git_context import GitContextProvider
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.infra.llm import LLMMessage, LLMProvider, LLMResponse
from commit_investigator.agent.orchestrator import (
    AgentOrchestrator,
    BudgetState,
    BugAttributionReport,
    SuspectCommit,
    _parse_suspects,
    _parse_tool_calls,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeAttributionLLM(LLMProvider):
    """Fake LLM that simulates an attribution agent conversation.

    Turns 1-3: requests search/blame/diff tools (meets MIN_TOOL_CALLS_BEFORE_CONCLUDE)
    Turn 4: outputs suspects
    """

    def __init__(self) -> None:
        self._call_count = 0

    @property
    def model_name(self) -> str:
        return "fake-attribution-v1"

    def complete(
        self, messages, tools=None, temperature=0.0, max_tokens=4096
    ) -> LLMResponse:
        self._call_count += 1

        if self._call_count == 1:
            content = (
                "Let me search for commits related to this bug.\n\n"
                "```tool\n"
                '{"tool": "search_commits_by_keyword", "args": {"keyword": "fix", "max_results": 3}}\n'
                "```\n"
            )
        elif self._call_count == 2:
            content = (
                "Let me search by file path.\n\n"
                "```tool\n"
                '{"tool": "search_commits_by_keyword", "args": {"keyword": "RouteBuilder", "max_results": 3}}\n'
                "```\n"
            )
        elif self._call_count == 3:
            content = (
                "Let me examine the diff of a suspect commit.\n\n"
                "```tool\n"
                '{"tool": "get_commit_diff", "args": {"commit_id": "HEAD~3"}}\n'
                "```\n"
            )
        else:
            content = (
                "Based on my investigation, here are my suspects:\n\n"
                "```suspects\n"
                "[\n"
                '  {"commit_id": "abc123def456", "confidence": 0.8, '
                '"mechanism": "If the null check was removed then NPE occurs", '
                '"evidence_quotes": ["- if (x != null)"]}\n'
                "]\n"
                "```\n"
            )

        return LLMResponse(
            content=content,
            tokens_used=500,
            estimated_cost=0.001,
            model=self.model_name,
        )


class TestParseToolCalls:
    def test_parses_single_tool_call(self) -> None:
        text = '```tool\n{"tool": "get_blame", "args": {"path": "foo.java"}}\n```'
        calls = _parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["tool"] == "get_blame"
        assert calls[0]["args"]["path"] == "foo.java"

    def test_parses_multiple_tool_calls(self) -> None:
        text = (
            'Some text\n```tool\n{"tool": "a", "args": {}}\n```\n'
            'More text\n```tool\n{"tool": "b", "args": {"x": 1}}\n```\n'
        )
        calls = _parse_tool_calls(text)
        assert len(calls) == 2

    def test_ignores_malformed_json(self) -> None:
        text = '```tool\n{not valid json}\n```'
        calls = _parse_tool_calls(text)
        assert calls == []

    def test_no_tool_calls(self) -> None:
        text = "Just regular text with no tools."
        calls = _parse_tool_calls(text)
        assert calls == []


class TestParseSuspects:
    def test_parses_valid_suspects(self) -> None:
        text = (
            '```suspects\n'
            '[{"commit_id": "abc", "confidence": 0.9, '
            '"mechanism": "test", "evidence_quotes": ["quote"]}]\n'
            '```'
        )
        suspects = _parse_suspects(text)
        assert len(suspects) == 1
        assert suspects[0].commit_id == "abc"
        assert suspects[0].rank == 1
        assert suspects[0].confidence == 0.9

    def test_empty_suspects(self) -> None:
        text = "No suspects here."
        assert _parse_suspects(text) == []

    def test_multiple_suspects_get_ranks(self) -> None:
        text = (
            '```suspects\n'
            '[{"commit_id": "a", "confidence": 0.9, "mechanism": "m1", "evidence_quotes": []},'
            ' {"commit_id": "b", "confidence": 0.5, "mechanism": "m2", "evidence_quotes": []}]\n'
            '```'
        )
        suspects = _parse_suspects(text)
        assert len(suspects) == 2
        assert suspects[0].rank == 1
        assert suspects[1].rank == 2


class TestBudgetState:
    def test_not_exceeded_initially(self) -> None:
        b = BudgetState()
        assert b.budget_exceeded is False

    def test_exceeded_on_tokens(self) -> None:
        b = BudgetState(max_tokens=100)
        b.total_tokens = 100
        assert b.budget_exceeded is True

    def test_exceeded_on_cost(self) -> None:
        b = BudgetState(max_cost=0.01)
        b.total_cost = 0.01
        assert b.budget_exceeded is True

    def test_exceeded_on_tool_calls(self) -> None:
        b = BudgetState(max_tool_calls=5)
        b.total_tool_calls = 5
        assert b.budget_exceeded is True

    def test_record_updates_state(self) -> None:
        b = BudgetState()
        b.record(LLMResponse(content="", tokens_used=100, estimated_cost=0.01))
        assert b.total_tokens == 100
        assert b.total_cost == 0.01
        assert b.turns_used == 1


class TestBugAttributionReport:
    def test_to_dict(self) -> None:
        report = BugAttributionReport(
            problem_title="test",
            problem_description="desc",
            suspects=[SuspectCommit("abc", 1, 0.8, "mechanism", ["quote"])],
            reasoning_summary="reasoning",
            tool_trace=[],
            metadata={"turns_used": 1},
        )
        d = report.to_dict()
        assert d["problem_title"] == "test"
        assert len(d["suspects"]) == 1
        assert d["suspects"][0]["commit_id"] == "abc"


skip_no_git = pytest.mark.skipif(
    not (REPO_ROOT / ".git").exists(),
    reason="No git repo at project root",
)


@skip_no_git
class TestAgentOrchestratorIntegration:
    """Integration test: fake LLM + real git repo."""

    def test_runs_multi_turn_investigation(self) -> None:
        llm = FakeAttributionLLM()
        orchestrator = AgentOrchestrator(
            llm_provider=llm,
            max_turns=10,
            max_tool_calls=30,
        )
        problem = ProblemStatement(
            title="NPE in RouteBuilder",
            description="NullPointerException when context is null",
            project="test",
        )
        git = GitContextProvider(REPO_ROOT)
        report = orchestrator.investigate(problem, git)

        assert isinstance(report, BugAttributionReport)
        assert report.problem_title == "NPE in RouteBuilder"
        assert len(report.suspects) == 1
        assert report.suspects[0].commit_id == "abc123def456"
        assert len(report.tool_trace) == 3
        assert report.metadata["turns_used"] == 4
        assert report.metadata["tool_calls"] == 3
        assert report.metadata["evidence_scoring_applied"] is True
        assert report.metadata["post_processing_applied"] is False
        assert len(report.metadata["evidence_scores"]) == 1
        assert report.metadata["evidence_scores"][0]["commit_id"] == "abc123def456"
        assert "grounding_rate" in report.metadata["evidence_scores"][0]

    def test_stops_on_budget(self) -> None:
        """Agent stops when tool call budget is exhausted."""

        class InfiniteToolLLM(LLMProvider):
            @property
            def model_name(self) -> str:
                return "infinite-tools"

            def complete(self, messages, **kwargs) -> LLMResponse:
                return LLMResponse(
                    content='```tool\n{"tool": "list_recent_commits", "args": {"max_results": 1}}\n```',
                    tokens_used=50,
                    estimated_cost=0.0001,
                )

        orchestrator = AgentOrchestrator(
            llm_provider=InfiniteToolLLM(),
            max_tool_calls=3,
            max_turns=20,
        )
        problem = ProblemStatement(title="test", description="test", project="test")
        git = GitContextProvider(REPO_ROOT)
        report = orchestrator.investigate(problem, git)

        assert report.metadata["budget_exceeded"] is True
        assert report.metadata["tool_calls"] <= 4
        assert report.metadata["evidence_scoring_applied"] is True
        assert report.metadata["evidence_scores"] == []


class TestEarlyExitNudge:
    """Tests for early exit nudge when agent has examined enough diffs."""

    @skip_no_git
    def test_early_exit_metadata_recorded(self) -> None:
        """Verify early_exit_threshold appears in report metadata."""
        llm = FakeAttributionLLM()
        orchestrator = AgentOrchestrator(
            llm_provider=llm,
            max_turns=10,
            max_tool_calls=30,
            early_exit_threshold=5,
        )
        problem = ProblemStatement(title="test", description="test", project="test")
        git = GitContextProvider(REPO_ROOT)
        report = orchestrator.investigate(problem, git)
        assert report.metadata["early_exit_threshold"] == 5

    @skip_no_git
    def test_early_exit_nudge_after_threshold(self) -> None:
        """Agent receives early conclude nudge after threshold + examine calls."""
        messages_captured: list[str] = []

        class TrackingLLM(LLMProvider):
            """LLM that does many tool calls to trigger early exit nudge."""

            def __init__(self) -> None:
                self._call_count = 0

            @property
            def model_name(self) -> str:
                return "tracking-llm"

            def complete(self, messages, **kwargs) -> LLMResponse:
                self._call_count += 1
                for m in messages:
                    if hasattr(m, "content") and "conclude NOW" in m.content:
                        messages_captured.append(m.content)

                if self._call_count <= 3:
                    return LLMResponse(
                        content='```tool\n{"tool": "search_commits_by_keyword", "args": {"keyword": "fix", "max_results": 1}}\n```',
                        tokens_used=50,
                        estimated_cost=0.0001,
                    )
                elif self._call_count <= 6:
                    return LLMResponse(
                        content='```tool\n{"tool": "get_commit_diff", "args": {"commit_id": "HEAD~1"}}\n```',
                        tokens_used=50,
                        estimated_cost=0.0001,
                    )
                else:
                    return LLMResponse(
                        content=(
                            '```suspects\n'
                            '[{"commit_id": "abc123", "confidence": 0.8, '
                            '"mechanism": "test", "evidence_quotes": []}]\n'
                            '```'
                        ),
                        tokens_used=50,
                        estimated_cost=0.0001,
                    )

        orchestrator = AgentOrchestrator(
            llm_provider=TrackingLLM(),
            max_turns=15,
            max_tool_calls=30,
            early_exit_threshold=5,
        )
        problem = ProblemStatement(title="test", description="desc", project="test")
        git = GitContextProvider(REPO_ROOT)
        report = orchestrator.investigate(problem, git)

        assert len(messages_captured) > 0, "Early exit nudge should have been triggered"
        assert report.suspects[0].commit_id == "abc123"


class TestEvidenceScoringMetadata:
    """Unit tests for in-pipeline evidence score attachment."""

    def test_suspects_unchanged_after_scoring(self) -> None:
        from commit_investigator.agent.orchestrator import _attach_evidence_scores

        suspects = [
            SuspectCommit("abc123", 1, 0.85, "mechanism one", ["quote a"]),
            SuspectCommit("def456", 2, 0.55, "mechanism two", []),
        ]
        git = MagicMock()
        git.get_diff.return_value = "+added line\n-old line"

        before = [
            (s.commit_id, s.rank, s.confidence, s.mechanism, list(s.evidence_quotes))
            for s in suspects
        ]
        scores = _attach_evidence_scores(suspects, git)
        after = [
            (s.commit_id, s.rank, s.confidence, s.mechanism, list(s.evidence_quotes))
            for s in suspects
        ]

        assert before == after
        assert len(scores) == 2
        assert scores[1]["total_quotes"] == 0
        assert scores[1]["grounding_rate"] == 0.0

    def test_empty_suspects_returns_empty_scores(self) -> None:
        from commit_investigator.agent.orchestrator import _attach_evidence_scores

        git = MagicMock()
        assert _attach_evidence_scores([], git) == []
        git.get_diff.assert_not_called()
