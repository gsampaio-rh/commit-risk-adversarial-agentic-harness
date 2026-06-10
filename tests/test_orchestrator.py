"""Tests for the agent orchestrator with mock LLM."""

import pytest

from commit_investigator.context_builder import InvestigationContext
from commit_investigator.llm import LLMMessage, LLMProvider, LLMResponse, MockLLMProvider
from commit_investigator.orchestrator import (
    DEFAULT_MAX_DIFF_CHARS,
    INVESTIGATION_SYSTEM_PROMPT,
    AgentOrchestrator,
    InvalidInvestigationResponseError,
    _apply_clean_commit_risk_cap,
    _coerce_text_field,
    _has_production_defect_signals,
    _matches_clean_archetype,
    _normalize_findings,
    _reasoning_all_speculative_or_unverifiable,
)
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
    def test_system_prompt_has_four_stages_and_rubric(self):
        assert "STAGE 1" in INVESTIGATION_SYSTEM_PROMPT
        assert "STAGE 4" in INVESTIGATION_SYSTEM_PROMPT
        assert "SUPPORTED defect hypothesis with diff evidence" in INVESTIGATION_SYSTEM_PROMPT
        assert "SPECULATIVE" in INVESTIGATION_SYSTEM_PROMPT
        assert "CLEAN-COMMIT DISCRIMINATION" in INVESTIGATION_SYSTEM_PROMPT
        assert "limited blast radius" in INVESTIGATION_SYSTEM_PROMPT
        assert "EXAMPLE A" in INVESTIGATION_SYSTEM_PROMPT
        assert "EXAMPLE B" in INVESTIGATION_SYSTEM_PROMPT

    def test_system_prompt_clean_commit_guard_preserves_buggy_high(self):
        assert "Do NOT apply clean-commit discrimination when" in INVESTIGATION_SYSTEM_PROMPT
        assert "SUPPORTED hypothesis with diff evidence" in INVESTIGATION_SYSTEM_PROMPT
        assert "risk_level MUST be ≥ HIGH" in INVESTIGATION_SYSTEM_PROMPT
        assert "router_probability does not override this cap" in INVESTIGATION_SYSTEM_PROMPT
        assert "Strict bar under clean-commit discrimination" in INVESTIGATION_SYSTEM_PROMPT

    def test_system_prompt_has_fix_commit_residual_risk_rule(self):
        assert "commit message mentions a fix" in INVESTIGATION_SYSTEM_PROMPT
        assert "SUPPORTED mechanism exists" in INVESTIGATION_SYSTEM_PROMPT

    def test_system_prompt_migration_opt_out_and_criterion_b_exclusion(self):
        assert "PRIMARY change" in INVESTIGATION_SYSTEM_PROMPT
        assert "Migration typing refactors alone do NOT waive discrimination" in INVESTIGATION_SYSTEM_PROMPT
        assert "criterion (b) API/binary incompatibility does NOT apply under discrimination" in INVESTIGATION_SYSTEM_PROMPT
        assert "Material guard removal" in INVESTIGATION_SYSTEM_PROMPT
        assert "EXAMPLE C" in INVESTIGATION_SYSTEM_PROMPT
        assert "even if ≥ 0.70" in INVESTIGATION_SYSTEM_PROMPT

    def test_coerce_structured_llm_fields(self):
        reasoning = _coerce_text_field({"STAGE 1": "summary"}, "default")
        assert "STAGE 1" in reasoning
        findings = _normalize_findings([{"hypothesis": "If X then Y"}, "plain string"])
        assert len(findings) == 2
        assert "hypothesis" in findings[0]
        assert findings[1] == "plain string"

    def test_router_probability_injected_in_user_message(self):
        orchestrator = AgentOrchestrator(llm_provider=MockLLMProvider())
        context = _mock_context()
        context.router_probability = 0.652
        context.router_route = "INVESTIGATE"
        messages = orchestrator._build_initial_messages(context)
        user_content = messages[1].content
        assert "router_probability=0.652" in user_content
        assert "route=INVESTIGATE" in user_content
        assert "prior, not a defect label" in user_content

    def test_router_probability_omitted_when_unset(self):
        orchestrator = AgentOrchestrator(llm_provider=MockLLMProvider())
        messages = orchestrator._build_initial_messages(_mock_context())
        user_content = messages[1].content
        assert "ML Risk Prior" not in user_content

    def test_default_diff_limit_is_16k(self):
        assert DEFAULT_MAX_DIFF_CHARS == 16_000

    def test_diff_truncation_at_configured_limit(self):
        orchestrator = AgentOrchestrator(llm_provider=MockLLMProvider(), max_diff_chars=100)
        ctx = _mock_context()
        ctx.diff = "x" * 200
        messages = orchestrator._build_initial_messages(ctx)
        user_content = messages[1].content
        assert "truncated, 200 chars total" in user_content
        assert "x" * 100 in user_content
        assert "x" * 101 not in user_content

    def test_diff_not_truncated_when_under_limit(self):
        orchestrator = AgentOrchestrator(llm_provider=MockLLMProvider(), max_diff_chars=500)
        ctx = _mock_context()
        ctx.diff = "y" * 200
        messages = orchestrator._build_initial_messages(ctx)
        user_content = messages[1].content
        assert "truncated" not in user_content
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


class TestCleanCommitRiskCap:
    def test_matches_version_bump_archetype(self):
        assert _matches_clean_archetype(_version_bump_context()) is True

    def test_reasoning_all_speculative_when_no_supported_hypothesis(self):
        reasoning = (
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): migration change. "
            "HYPOTHESIS 2 (UNVERIFIABLE): truncated diff."
        )
        assert _reasoning_all_speculative_or_unverifiable(reasoning) is True

    def test_reasoning_not_all_speculative_when_supported_present(self):
        reasoning = "STAGE 3: HYPOTHESIS A — SUPPORTED: removed guard at line 42."
        assert _reasoning_all_speculative_or_unverifiable(reasoning) is False

    def test_apply_cap_downgrades_high_on_version_bump_with_speculative_only(self):
        reasoning = "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): cross-version break."
        level, capped = _apply_clean_commit_risk_cap(
            RiskLevel.HIGH, _version_bump_context(), reasoning,
        )
        assert capped is True
        assert level == RiskLevel.MEDIUM

    def test_apply_cap_downgrades_high_on_supported_criterion_b_when_archetype(self):
        reasoning = (
            "STAGE 3: HYPOTHESIS A — SUPPORTED: raw QueryFactory erasure breaks callers. "
            "STAGE 4: criterion (b) HIGH."
        )
        ctx = _version_bump_context()
        ctx.diff += "\n+        return future;\n"
        level, capped = _apply_clean_commit_risk_cap(RiskLevel.HIGH, ctx, reasoning)
        assert capped is True
        assert level == RiskLevel.MEDIUM

    def test_apply_cap_skipped_when_guard_removal_in_diff(self):
        ctx = _version_bump_context()
        ctx.diff += "\n-        if (value != null) {\n-            return value;\n-        }\n"
        level, capped = _apply_clean_commit_risk_cap(
            RiskLevel.HIGH, ctx, "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): migration.",
        )
        assert capped is False
        assert level == RiskLevel.HIGH

    def test_jira_ticket_with_return_does_not_opt_out_of_cap(self):
        ctx = _version_bump_context()
        ctx.diff += "\n+        return future;\n"
        assert _has_production_defect_signals(ctx) is False

    def test_has_production_defect_signals_for_guard_removal(self):
        ctx = InvestigationContext(
            commit_id="fbf0ffad627b",
            project="camel",
            message="CAMEL-10279 fix lifecycle ordering",
            diff="-        if (started) {\n+        // removed guard\n",
            touched_files=["RoutesCollector.java"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert _has_production_defect_signals(ctx) is True

    def test_matches_jetty_version_bump_archetype(self):
        ctx = InvestigationContext(
            commit_id="b9f1653151e2",
            project="camel",
            message="CAMEL jetty upgrade",
            diff=(
                "-    <jetty9-version>9.3.14</jetty9-version>\n"
                "+    <jetty9-version>9.3.21</jetty9-version>\n"
                "-    <!-- binary incompatible above 9.3.15 -->\n"
            ),
            touched_files=["parent/pom.xml"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert _matches_clean_archetype(ctx) is True


class TestInvalidInvestigationResponse:
    def test_empty_llm_response_raises(self):
        orchestrator = AgentOrchestrator(llm_provider=_StaticLLMProvider(""))
        with pytest.raises(InvalidInvestigationResponseError, match="Empty LLM"):
            orchestrator.investigate(
                commit_id="x",
                project="camel",
                context=_mock_context(),
            )

    def test_missing_risk_level_raises(self):
        orchestrator = AgentOrchestrator(
            llm_provider=_StaticLLMProvider('{"confidence": 0.5, "reasoning": "x"}'),
        )
        with pytest.raises(InvalidInvestigationResponseError, match="missing risk_level"):
            orchestrator.investigate(
                commit_id="x",
                project="camel",
                context=_mock_context(),
            )

    def test_clean_archetype_high_capped_in_assembled_report(self):
        payload = (
            '{"risk_level":"HIGH","confidence":0.8,'
            '"reasoning":"STAGE 3: HYPOTHESIS 1 (SPECULATIVE): migration.",'
            '"findings":[],"follow_up_needed":false,'
            '"localization":[],"recommendations":[]}'
        )
        orchestrator = AgentOrchestrator(llm_provider=_StaticLLMProvider(payload))
        report = orchestrator.investigate(
            commit_id="9530370f7642",
            project="camel",
            context=_version_bump_context(),
        )
        assert report.risk_assessment.level == RiskLevel.MEDIUM
        assert report.metadata.get("clean_commit_risk_cap_applied") is True


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
