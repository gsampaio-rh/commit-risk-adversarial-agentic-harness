"""Tests for the six-dimension evaluation harness."""

import pytest

from tests.conftest import skip_no_data

from commit_investigator.runners.eval_harness import EvalHarness, _is_test_or_doc
from commit_investigator.infra.ground_truth import GroundTruthGraph
from commit_investigator.analysis.report import (
    CommitInvestigationReport,
    EvidenceItem,
    EvidenceType,
    LocalizationClaim,
    RiskAssessment,
    RiskLevel,
)
from commit_investigator.routing.router import Route


def _make_report(
    commit_id: str = "abc123",
    risk_level: RiskLevel = RiskLevel.HIGH,
    confidence: float = 0.8,
    localization: list | None = None,
) -> CommitInvestigationReport:
    return CommitInvestigationReport(
        commit_id=commit_id,
        project="CAMEL",
        risk_assessment=RiskAssessment(level=risk_level, confidence=confidence),
        evidence=[EvidenceItem(
            type=EvidenceType.DIFF_HUNK,
            source="src/Main.java",
            content="risky code",
            relevance="test",
        )],
        findings=["test finding"],
        localization=localization or [],
        reasoning_summary="Test reasoning for eval.",
        turn_count=1,
    )


@skip_no_data
class TestEvalHarnessReal:
    @pytest.fixture
    def harness(self) -> EvalHarness:
        gt = GroundTruthGraph.from_replication_zip(
            "data/apachejit/apachejit_dataset_replication.zip"
        )
        return EvalHarness(ground_truth=gt, budget_tier="$10", max_evals=10)

    def test_d1_correct_prediction_buggy(self, harness):
        report = _make_report(
            commit_id="a2fce2828a03e6c8bdad4c10e6363552a96c6206",
            risk_level=RiskLevel.HIGH,
        )
        result = harness.evaluate_report(report, buggy_label=True)
        assert result.scores["D1"].score == 1.0

    def test_d1_incorrect_prediction(self, harness):
        report = _make_report(risk_level=RiskLevel.LOW)
        result = harness.evaluate_report(report, buggy_label=True)
        assert result.scores["D1"].score == 0.0

    def test_d2_with_localization(self, harness):
        report = _make_report(
            commit_id="a2fce2828a03e6c8bdad4c10e6363552a96c6206",
            localization=[LocalizationClaim(file="src/Foo.java", rationale="test")],
        )
        result = harness.evaluate_report(report, buggy_label=True)
        assert result.scores["D2"].score >= 0.0

    def test_batch_eval(self, harness):
        report = _make_report(commit_id="a2fce2828a03e6c8bdad4c10e6363552a96c6206")
        batch = [(report, True, Route.INVESTIGATE)]
        eval_report = harness.evaluate_batch(batch)
        assert eval_report.total_sampled == 1
        assert "D1" in eval_report.dimension_averages

    def test_router_baseline(self, harness):
        report = _make_report()
        batch = [(report, True, Route.INVESTIGATE)]
        eval_report = harness.evaluate_batch(batch)
        assert "D1_router_only" in eval_report.router_baseline


class TestIsTestOrDoc:
    """Unit tests for _is_test_or_doc helper — D2 fix-ground-truth filter."""

    def test_source_java_not_filtered(self):
        assert _is_test_or_doc("Foo.java") is False
        assert _is_test_or_doc("SpringBootAutoConfigurationMojo.java") is False
        assert _is_test_or_doc("CamelReactiveStreamsServiceImpl.java") is False

    def test_test_java_filtered(self):
        assert _is_test_or_doc("FooTest.java") is True
        assert _is_test_or_doc("FooTests.java") is True
        assert _is_test_or_doc("FooIT.java") is True
        assert _is_test_or_doc("FooSpec.java") is True

    def test_test_java_with_path_prefix_filtered(self):
        assert _is_test_or_doc("src/test/java/com/example/FooTest.java") is True

    def test_adoc_filtered(self):
        assert _is_test_or_doc("jms-component.adoc") is True
        assert _is_test_or_doc("docs/design.adoc") is True

    def test_markdown_filtered(self):
        assert _is_test_or_doc("README.md") is True

    def test_rst_txt_filtered(self):
        assert _is_test_or_doc("CHANGES.rst") is True
        assert _is_test_or_doc("NOTICE.txt") is True

    def test_xml_not_filtered(self):
        # XML filtering is intentionally excluded — too ambiguous to classify by name alone
        assert _is_test_or_doc("camelContext.xml") is False
        assert _is_test_or_doc("test-camel-context.xml") is False
