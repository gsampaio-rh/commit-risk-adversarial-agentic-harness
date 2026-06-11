"""Tests for H1H4T3 prompt, changed-line citation, and mechanism evaluator loop."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from commit_investigator.context_builder import InvestigationContext
from commit_investigator.hypothesis_engine import (
    HYPOTHESIS_SYSTEM_PROMPT,
    HYPOTHESIS_SYSTEM_PROMPT_H1H4T3,
    HypothesisResponse,
    _has_changed_line_citation,
    build_investigation_messages,
    extract_coverage_section,
    mechanism_evaluator_loop,
)
from commit_investigator.llm import LLMProvider, LLMResponse
from commit_investigator.orchestrator import AgentOrchestrator


class _CountingLLMProvider(LLMProvider):
    """Returns fixed JSON payloads; tracks complete() call count."""

    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "counting-test"

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=4096):
        idx = min(self.calls, len(self._contents) - 1)
        self.calls += 1
        return LLMResponse(
            content=self._contents[idx],
            tool_calls=[],
            tokens_used=10,
            estimated_cost=0.0,
            model=self.model_name,
            finish_reason="stop",
        )


def _parse_response(response: LLMResponse) -> HypothesisResponse:
    from commit_investigator.hypothesis_engine import parse_hypothesis_response

    return parse_hypothesis_response(response.content or "")


def _record(_response: LLMResponse) -> None:
    pass


def _context_only_payload(mechanism: str = "Observable: crash. Root change: n/a. Mechanism: NPE") -> str:
    return json.dumps({
        "summary": "Test",
        "hypotheses": [{
            "mechanism": mechanism,
            "evidence_quote": " unchanged context line",
            "file": "foo.py",
            "lines": [1, 2],
        }],
    })


def _grounded_payload() -> str:
    return json.dumps({
        "summary": "Test",
        "hypotheses": [{
            "mechanism": "Observable: NPE. Root change: -if (x). Mechanism: null deref",
            "evidence_quote": "-if (x != null) {",
            "file": "foo.py",
            "lines": [1, 2],
        }],
    })


def _empty_quote_payload() -> str:
    return json.dumps({
        "summary": "Test",
        "hypotheses": [
            {"mechanism": "m1", "evidence_quote": "", "file": "a.py", "lines": []},
            {"mechanism": "m2", "evidence_quote": "   ", "file": "b.py", "lines": []},
        ],
    })


class TestH1H4T3Prompt:
    def test_prompt_distinct_from_baseline(self):
        assert HYPOTHESIS_SYSTEM_PROMPT_H1H4T3 != HYPOTHESIS_SYSTEM_PROMPT

    def test_prompt_has_required_sections(self):
        prompt = HYPOTHESIS_SYSTEM_PROMPT_H1H4T3
        assert "Observable:" in prompt
        assert "Root change:" in prompt
        assert "## CHANGED-LINE EVIDENCE" in prompt
        assert extract_coverage_section(HYPOTHESIS_SYSTEM_PROMPT_H1H4T3)


class TestHasChangedLineCitation:
    def test_empty_string_false(self):
        assert _has_changed_line_citation("") is False

    def test_whitespace_only_false(self):
        assert _has_changed_line_citation("   \n  \n") is False

    def test_context_only_false(self):
        assert _has_changed_line_citation(" unchanged line\n  context") is False

    def test_plus_line_true(self):
        assert _has_changed_line_citation("+added line") is True

    def test_minus_line_true(self):
        assert _has_changed_line_citation("-removed line") is True

    def test_diff_headers_only_false(self):
        quote = "+++ b/file\n--- a/file"
        assert _has_changed_line_citation(quote) is False


class TestMechanismEvaluatorLoop:
    def test_empty_quotes_no_challenge_single_call(self):
        llm = _CountingLLMProvider([_empty_quote_payload()])
        from commit_investigator.llm import LLMMessage

        msgs = [LLMMessage(role="system", content="sys"), LLMMessage(role="user", content="user")]
        result, _ = mechanism_evaluator_loop(
            llm, msgs, None, _parse_response, _record, ValueError,
        )
        assert llm.calls == 1
        assert len(result.hypotheses) == 2

    def test_grounded_quotes_no_challenge_single_call(self):
        llm = _CountingLLMProvider([_grounded_payload()])
        from commit_investigator.llm import LLMMessage

        msgs = [LLMMessage(role="system", content="sys"), LLMMessage(role="user", content="user")]
        mechanism_evaluator_loop(llm, msgs, None, _parse_response, _record, ValueError)
        assert llm.calls == 1

    def test_context_only_exhausts_after_two_calls(self):
        llm = _CountingLLMProvider([
            _context_only_payload(),
            _context_only_payload("Observable: still wrong. Root change: n/a. Mechanism: race"),
        ])
        from commit_investigator.llm import LLMMessage

        msgs = [LLMMessage(role="system", content="sys"), LLMMessage(role="user", content="user")]
        mechanism_evaluator_loop(llm, msgs, None, _parse_response, _record, ValueError)
        assert llm.calls == 2


class TestOrchestratorMechanismEvaluatorFlag:
    def _mock_context(self) -> InvestigationContext:
        return InvestigationContext(
            commit_id="test_commit_hash",
            project="camel",
            diff="+ risky code",
            message="Fix bug",
            touched_files=["src/Main.java"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )

    def test_flag_false_never_calls_mechanism_evaluator_loop(self):
        llm = _CountingLLMProvider([_empty_quote_payload()])
        orchestrator = AgentOrchestrator(
            llm_provider=llm,
            enable_mechanism_evaluator=False,
            max_turns=1,
        )
        with patch(
            "commit_investigator.orchestrator.mechanism_evaluator_loop",
        ) as mock_loop:
            orchestrator.investigate(
                commit_id="x",
                project="camel",
                context=self._mock_context(),
            )
            mock_loop.assert_not_called()

    def test_flag_true_calls_mechanism_evaluator_loop_once(self):
        llm = _CountingLLMProvider([_grounded_payload()])
        orchestrator = AgentOrchestrator(
            llm_provider=llm,
            enable_mechanism_evaluator=True,
            max_turns=1,
        )
        with patch(
            "commit_investigator.orchestrator.mechanism_evaluator_loop",
            wraps=mechanism_evaluator_loop,
        ) as mock_loop:
            orchestrator.investigate(
                commit_id="x",
                project="camel",
                context=self._mock_context(),
            )
            mock_loop.assert_called_once()

    def test_flag_true_uses_h1h4t3_system_prompt(self):
        ctx = self._mock_context()
        msgs = build_investigation_messages(ctx, system_prompt=HYPOTHESIS_SYSTEM_PROMPT_H1H4T3)
        assert msgs[0].content == HYPOTHESIS_SYSTEM_PROMPT_H1H4T3
        assert "Observable:" in msgs[0].content
