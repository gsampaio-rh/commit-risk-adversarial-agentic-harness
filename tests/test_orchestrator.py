"""Tests for the agent orchestrator with mock LLM."""

import pytest

from commit_investigator.context_builder import InvestigationContext
from commit_investigator.hypothesis_engine import (
    HYPOTHESIS_SYSTEM_PROMPT,
    build_investigation_messages,
)
from commit_investigator.llm import LLMProvider, LLMResponse, MockLLMProvider
from commit_investigator.orchestrator import (
    DEFAULT_MAX_DIFF_CHARS,
    INVESTIGATION_SYSTEM_PROMPT,
    AgentOrchestrator,
    InvalidInvestigationResponseError,
)
from commit_investigator.response_parser import coerce_text_field, normalize_findings
from commit_investigator.report import CommitInvestigationReport, RiskLevel


def _mock_context() -> InvestigationContext:
    return InvestigationContext(
        commit_id="test_commit_hash",
        project="camel",
        diff="diff --git a/src/Main.java\n+++ b/src/Main.java\n+risky code",
        message="Fix: handle null case",
        touched_files=["src/Main.java"],
        csv_features={"la": 5.0, "ld": 2.0, "nf": 1.0},
        file_histories={},
        author_stats=None,
    )


class TestInvestigationPrompt:
    def test_hypothesis_system_prompt_focuses_on_generation_only(self):
        """AC-1/2/3: New prompt is about hypothesis generation, no rubric tiers."""
        assert "hypotheses" in HYPOTHESIS_SYSTEM_PROMPT.lower()
        assert "mechanism" in HYPOTHESIS_SYSTEM_PROMPT
        assert "evidence_quote" in HYPOTHESIS_SYSTEM_PROMPT
        # AC-2: No rubric tier labels in prompt
        assert "RISK CLASSIFICATION RUBRIC" not in HYPOTHESIS_SYSTEM_PROMPT
        # AC-3: No CLEAN-COMMIT DISCRIMINATION block
        assert "CLEAN-COMMIT DISCRIMINATION" not in HYPOTHESIS_SYSTEM_PROMPT

    def test_hypothesis_system_prompt_le_60_lines(self):
        """AC-1: Prompt must be ≤60 lines."""
        lines = HYPOTHESIS_SYSTEM_PROMPT.strip().splitlines()
        assert len(lines) <= 60, f"Prompt has {len(lines)} lines (max 60)"

    def test_prompt_no_high_medium_low_rubric(self):
        """AC-2: No standalone rubric tier assignments in prompt."""
        # The prompt should not contain 'HIGH:' or 'MEDIUM:' as rubric directives
        assert "HIGH:" not in HYPOTHESIS_SYSTEM_PROMPT
        assert "MEDIUM:" not in HYPOTHESIS_SYSTEM_PROMPT
        assert "LOW:" not in HYPOTHESIS_SYSTEM_PROMPT

    def test_backward_compat_investigation_prompt_is_hypothesis_prompt(self):
        """INVESTIGATION_SYSTEM_PROMPT is aliased to HYPOTHESIS_SYSTEM_PROMPT."""
        assert INVESTIGATION_SYSTEM_PROMPT is HYPOTHESIS_SYSTEM_PROMPT

    def test_coerce_structured_llm_fields(self):
        reasoning = coerce_text_field({"STAGE 1": "summary"}, "default")
        assert "STAGE 1" in reasoning
        findings = normalize_findings([{"hypothesis": "If X then Y"}, "plain string"])
        assert len(findings) == 2
        assert "hypothesis" in findings[0]
        assert findings[1] == "plain string"

    def test_normalize_findings_returns_empty_list_not_default_string(self):
        """AC-7: Empty findings → [] not ['Investigation completed']."""
        assert normalize_findings(None) == []
        assert normalize_findings([]) == []
        assert normalize_findings("") == []

    def test_router_probability_injected_in_user_message(self):
        context = _mock_context()
        context.router_probability = 0.652
        context.router_route = "INVESTIGATE"
        messages = build_investigation_messages(context)
        user_content = messages[1].content
        assert "0.652" in user_content
        assert "Router Prior" in user_content

    def test_router_probability_omitted_when_unset(self):
        messages = build_investigation_messages(_mock_context())
        user_content = messages[1].content
        assert "Router Prior" not in user_content

    def test_default_diff_limit_is_16k(self):
        assert DEFAULT_MAX_DIFF_CHARS == 16_000

    def test_diff_truncation_note_when_truncation_metadata_present(self):
        """Smart diff truncation note shown when truncation_metadata has truncated_files."""
        from commit_investigator.smart_diff import AssembledDiff
        ctx = _mock_context()
        ctx.diff = "x" * 100
        ctx.truncation_metadata = AssembledDiff(
            text="x" * 100,
            included_files=["Foo.java"],
            truncated_files=["Bar.java"],
            total_chars=100,
        )
        messages = build_investigation_messages(ctx)
        user_content = messages[1].content
        assert "smart-truncated" in user_content
        assert "Bar.java" in user_content

    def test_diff_not_truncated_when_under_limit(self):
        ctx = _mock_context()
        ctx.diff = "y" * 200
        messages = build_investigation_messages(ctx)
        user_content = messages[1].content
        assert "smart-truncated" not in user_content
        assert "y" * 200 in user_content


class _StaticLLMProvider(LLMProvider):
    """Returns a fixed JSON payload for orchestrator assembly tests."""

    def __init__(self, content: str) -> None:
        self._content = content

    @property
    def model_name(self) -> str:
        return "static-test"

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=4096):
        return LLMResponse(
            content=self._content,
            tool_calls=[],
            tokens_used=10,
            estimated_cost=0.0,
            model=self.model_name,
            finish_reason="stop",
        )


class _SequenceLLMProvider(LLMProvider):
    """Returns a sequence of payloads; tracks call count for retry tests."""

    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "sequence-test"

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


def _version_bump_context() -> InvestigationContext:
    return InvestigationContext(
        commit_id="9530370f7642a79b67f7bc4b999cfcae6c193305",
        project="camel",
        message="CAMEL-11268 Upgrade to Infinispan 9.x",
        diff=(
            "diff --git a/pom.xml b/pom.xml\n"
            "-    <version>8.2.0</version>\n"
            "+    <version>9.4.0</version>\n"
            "diff --git a/InfinispanProducer.java b/InfinispanProducer.java\n"
            "-import org.infinispan.commons.util.concurrent.NotifyingFuture;\n"
            "+import java.util.concurrent.CompletableFuture;\n"
        ),
        touched_files=["pom.xml", "InfinispanProducer.java"],
        csv_features={"la": 5.0},
        file_histories={},
        author_stats=None,
    )



class TestInvalidInvestigationResponse:
    def test_empty_llm_response_raises(self):
        orchestrator = AgentOrchestrator(llm_provider=_StaticLLMProvider(""))
        with pytest.raises(InvalidInvestigationResponseError, match="Empty LLM"):
            orchestrator.investigate(
                commit_id="x",
                project="camel",
                context=_mock_context(),
            )

    def test_missing_summary_raises(self):
        """New schema: missing summary/hypotheses → InvalidInvestigationResponseError."""
        llm = _SequenceLLMProvider(['{"confidence": 0.5, "reasoning": "x"}'])
        orchestrator = AgentOrchestrator(llm_provider=llm)
        with pytest.raises(InvalidInvestigationResponseError, match="missing summary/hypotheses"):
            orchestrator.investigate(
                commit_id="x",
                project="camel",
                context=_mock_context(),
            )
        assert llm.calls == 2, "Parse failure should retry once before raising"

    def test_invalid_json_retries_once_then_raises(self):
        """EC-1: invalid JSON → retry once → InvalidInvestigationResponseError."""
        llm = _SequenceLLMProvider(["not valid json", "still not json"])
        orchestrator = AgentOrchestrator(llm_provider=llm)
        with pytest.raises(InvalidInvestigationResponseError, match="Invalid LLM JSON"):
            orchestrator.investigate(
                commit_id="x",
                project="camel",
                context=_mock_context(),
            )
        assert llm.calls == 2

    def test_invalid_json_retries_then_succeeds(self):
        """EC-1: invalid JSON on first attempt, valid JSON on retry succeeds."""
        valid = '{"summary":"ok","hypotheses":[]}'
        llm = _SequenceLLMProvider(["not valid json", valid])
        orchestrator = AgentOrchestrator(llm_provider=llm)
        report = orchestrator.investigate(
            commit_id="x",
            project="camel",
            context=_mock_context(),
        )
        assert llm.calls == 2
        assert report.reasoning_summary == "ok"

    def test_clean_archetype_returns_medium_directly(self):
        """Version-bump context → no SUPPORTED hypotheses → Script assigns MEDIUM directly."""
        payload = (
            '{"summary":"Upgrade to Infinispan 9.x via pom.xml version bump and import migration.",'
            '"hypotheses":[{"mechanism":"If caller uses NotifyingFuture API then NPE",'
            '"evidence_quote":"","file":"InfinispanProducer.java","lines":[1,5]}]}'
        )
        orchestrator = AgentOrchestrator(llm_provider=_StaticLLMProvider(payload))
        report = orchestrator.investigate(
            commit_id="9530370f7642",
            project="camel",
            context=_version_bump_context(),
        )
        # New behavior: Script computes MEDIUM directly (no cap needed since never HIGH)
        assert report.risk_assessment.level == RiskLevel.MEDIUM


class TestAgentOrchestrator:
    def test_mock_investigation_produces_valid_report(self):
        orchestrator = AgentOrchestrator(
            llm_provider=MockLLMProvider(),
            max_turns=3,
        )
        report = orchestrator.investigate(
            commit_id="test_commit_hash",
            project="camel",
            context=_mock_context(),
        )
        assert isinstance(report, CommitInvestigationReport)
        assert report.commit_id == "test_commit_hash"
        assert len(report.evidence) >= 1

    def test_respects_max_turns(self):
        orchestrator = AgentOrchestrator(
            llm_provider=MockLLMProvider(),
            max_turns=1,
        )
        report = orchestrator.investigate(
            commit_id="test_commit_hash",
            project="camel",
            context=_mock_context(),
        )
        assert report.turn_count <= 1

    def test_budget_tracking(self):
        orchestrator = AgentOrchestrator(
            llm_provider=MockLLMProvider(),
            max_turns=3,
            max_tokens=50000,
        )
        report = orchestrator.investigate(
            commit_id="test_commit_hash",
            project="camel",
            context=_mock_context(),
        )
        assert report.metadata["total_tokens"] > 0
        assert report.metadata["total_cost"] >= 0

    def test_risk_level_from_mock(self):
        orchestrator = AgentOrchestrator(llm_provider=MockLLMProvider())
        report = orchestrator.investigate(
            commit_id="x",
            project="camel",
            context=_mock_context(),
        )
        # Mock varies risk by commit context; with diff present it is never empty.
        assert report.risk_assessment.level in (
            RiskLevel.LOW,
            RiskLevel.MEDIUM,
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        )
        assert 0.0 <= report.risk_assessment.confidence <= 1.0

    def test_checkpoint_persistence(self, tmp_path):
        orchestrator = AgentOrchestrator(
            llm_provider=MockLLMProvider(),
            checkpoint_dir=tmp_path / "checkpoints",
        )
        orchestrator.investigate(
            commit_id="x",
            project="camel",
            context=_mock_context(),
        )
        checkpoint_files = list((tmp_path / "checkpoints").glob("turn_*.json"))
        assert len(checkpoint_files) >= 1
