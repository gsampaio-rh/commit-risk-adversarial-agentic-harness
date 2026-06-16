"""Tests for D3 Attribution Quality LLM Judge."""

from __future__ import annotations

import pytest

from commit_investigator.infra.llm import LLMMessage, LLMProvider, LLMResponse
from commit_investigator.agent.orchestrator import SuspectCommit
from commit_investigator.eval.d3_judge import (
    D3Score,
    _format_evidence,
    _truncate_diff,
    judge_attribution_d3,
    parse_judge_response,
    score_suspect_d3,
)


class FakeJudgeLLM(LLMProvider):
    """Returns a configurable D3 score response."""

    def __init__(self, score: int = 3, rationale: str = "Good causal chain.") -> None:
        self._score = score
        self._rationale = rationale

    @property
    def model_name(self) -> str:
        return "fake-judge-v1"

    def complete(
        self, messages, tools=None, temperature=0.0, max_tokens=4096
    ) -> LLMResponse:
        return LLMResponse(
            content=f'{{"score": {self._score}, "rationale": "{self._rationale}"}}',
            tokens_used=100,
            estimated_cost=0.001,
            model=self.model_name,
        )


class TestParseJudgeResponse:
    def test_json_block(self) -> None:
        text = '```json\n{"score": 3, "rationale": "Sound causal chain."}\n```'
        score, rationale = parse_judge_response(text)
        assert score == 3
        assert rationale == "Sound causal chain."

    def test_bare_json(self) -> None:
        text = 'Here is my evaluation:\n{"score": 4, "rationale": "Precise."}\nDone.'
        score, rationale = parse_judge_response(text)
        assert score == 4
        assert rationale == "Precise."

    def test_clamps_high_score(self) -> None:
        text = '{"score": 7, "rationale": "Impossible score."}'
        score, _ = parse_judge_response(text)
        assert score == 4

    def test_clamps_negative_score(self) -> None:
        text = '{"score": -1, "rationale": "Negative."}'
        score, _ = parse_judge_response(text)
        assert score == 0

    def test_fallback_digit_extraction(self) -> None:
        text = "I would rate this a 2 out of 4 because the explanation is partial."
        score, rationale = parse_judge_response(text)
        assert score == 2
        assert "Extracted from unstructured response" in rationale

    def test_no_score_found(self) -> None:
        text = "This response has no recognizable score."
        score, rationale = parse_judge_response(text)
        assert score == 0
        assert "parse_error" in rationale

    def test_json_without_fence(self) -> None:
        text = '{"score": 1, "rationale": "Vague mechanism."}'
        score, rationale = parse_judge_response(text)
        assert score == 1
        assert rationale == "Vague mechanism."


class TestTruncateDiff:
    def test_short_diff_unchanged(self) -> None:
        diff = "short diff"
        assert _truncate_diff(diff) == diff

    def test_long_diff_truncated(self) -> None:
        diff = "x" * 10_000
        result = _truncate_diff(diff)
        assert len(result) < len(diff)
        assert "truncated" in result


class TestFormatEvidence:
    def test_empty(self) -> None:
        assert _format_evidence([]) == "(none provided)"

    def test_multiple(self) -> None:
        result = _format_evidence(["quote1", "quote2"])
        assert "- `quote1`" in result
        assert "- `quote2`" in result


class TestScoreSuspectD3:
    def test_returns_score_from_llm(self) -> None:
        suspect = SuspectCommit(
            commit_id="abc123",
            rank=1,
            confidence=0.8,
            mechanism="If null check removed then NPE occurs",
            evidence_quotes=["- if (x != null)"],
        )
        llm = FakeJudgeLLM(score=3, rationale="Good causal chain.")
        result = score_suspect_d3(
            suspect, "NPE in Foo", "NullPointerException when x is null",
            diff="+removed null check\n-if (x != null)", llm=llm,
        )
        assert isinstance(result, D3Score)
        assert result.score == 3
        assert result.commit_id == "abc123"
        assert result.tokens_used == 100

    def test_no_mechanism_returns_zero(self) -> None:
        suspect = SuspectCommit("abc", 1, 0.5, "", [])
        llm = FakeJudgeLLM(score=4)
        result = score_suspect_d3(suspect, "title", "desc", None, llm)
        assert result.score == 0
        assert "No mechanism" in result.rationale

    def test_no_diff_still_works(self) -> None:
        suspect = SuspectCommit("abc", 1, 0.5, "Some mechanism.", [])
        llm = FakeJudgeLLM(score=2, rationale="Partial.")
        result = score_suspect_d3(suspect, "title", "desc", None, llm)
        assert result.score == 2


class TestJudgeAttributionD3:
    def test_scores_top_suspects(self) -> None:
        suspects = [
            SuspectCommit("a", 1, 0.9, "Mechanism A", ["quote_a"]),
            SuspectCommit("b", 2, 0.6, "Mechanism B", []),
            SuspectCommit("c", 3, 0.3, "Mechanism C", []),
            SuspectCommit("d", 4, 0.1, "Mechanism D", []),
        ]
        llm = FakeJudgeLLM(score=3)
        result = judge_attribution_d3(
            suspects, "Bug title", "Bug description",
            git_provider=None, llm=llm, max_suspects=3,
        )
        assert len(result.scores) == 3
        assert result.avg_score == 3.0
        assert result.top_suspect_score == 3

    def test_empty_suspects(self) -> None:
        llm = FakeJudgeLLM(score=3)
        result = judge_attribution_d3(
            [], "title", "desc", git_provider=None, llm=llm,
        )
        assert result.scores == []
        assert result.avg_score == 0.0

    def test_to_dict_format(self) -> None:
        suspects = [SuspectCommit("a", 1, 0.9, "Mechanism A", [])]
        llm = FakeJudgeLLM(score=4, rationale="Precise.")
        result = judge_attribution_d3(
            suspects, "title", "desc", git_provider=None, llm=llm,
        )
        d = result.to_dict()
        assert d["avg_score"] == 4.0
        assert d["top_suspect_score"] == 4
        assert len(d["scores"]) == 1
        assert d["scores"][0]["score"] == 4
