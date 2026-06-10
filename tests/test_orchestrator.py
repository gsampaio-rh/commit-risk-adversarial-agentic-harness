"""Tests for the agent orchestrator with mock LLM."""

from commit_investigator.context_builder import InvestigationContext
from commit_investigator.llm import MockLLMProvider
from commit_investigator.orchestrator import AgentOrchestrator
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
        # Mock returns MEDIUM when diff is present
        assert report.risk_assessment.level == RiskLevel.MEDIUM

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
