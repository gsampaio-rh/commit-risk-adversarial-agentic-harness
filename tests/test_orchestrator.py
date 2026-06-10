"""Tests for the agent orchestrator with mock LLM."""

from commit_investigator.context_builder import InvestigationContext
from commit_investigator.llm import MockLLMProvider
from commit_investigator.orchestrator import (
    INVESTIGATION_SYSTEM_PROMPT,
    AgentOrchestrator,
    _coerce_text_field,
    _normalize_findings,
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
        assert "SUPPORTED hypothesis with diff evidence" in INVESTIGATION_SYSTEM_PROMPT
        assert "limited blast radius" in INVESTIGATION_SYSTEM_PROMPT
        assert "EXAMPLE A" in INVESTIGATION_SYSTEM_PROMPT
        assert "EXAMPLE B" in INVESTIGATION_SYSTEM_PROMPT

    def test_system_prompt_has_fix_commit_residual_risk_rule(self):
        assert "commit message mentions a fix" in INVESTIGATION_SYSTEM_PROMPT
        assert "SUPPORTED mechanism exists" in INVESTIGATION_SYSTEM_PROMPT

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
