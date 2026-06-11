"""Tests for the LLM-as-judge reasoning faithfulness evaluation."""

import json

import pytest

from commit_investigator.eval_judge import (
    D3_FIX_DIFF_FALLBACK_RUBRIC,
    JudgeResult,
    ReasoningJudge,
    _parse_judge_response,
)
from commit_investigator.jira_client import JiraIssue
from commit_investigator.llm import LLMProvider, LLMResponse
from commit_investigator.report import (
    CommitInvestigationReport,
    EvidenceItem,
    EvidenceType,
    LocalizationClaim,
    Recommendation,
    RecommendationPriority,
    RiskAssessment,
    RiskLevel,
)


class _StubJudgeProvider(LLMProvider):
    """Returns a configurable JSON response for judge testing."""

    def __init__(self, score: int, justification: str = "test justification") -> None:
        self._score = score
        self._justification = justification

    @property
    def model_name(self) -> str:
        return "stub-judge"

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=4096):
        return LLMResponse(
            content=json.dumps({"score": self._score, "justification": self._justification}),
            tokens_used=50,
            estimated_cost=0.0001,
            model="stub-judge",
        )


class _FailingProvider(LLMProvider):
    """Simulates an LLM call that raises an exception."""

    @property
    def model_name(self) -> str:
        return "failing-provider"

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=4096):
        raise ConnectionError("network timeout")


def _make_report(
    localization: list | None = None,
    recommendations: list | None = None,
    reasoning: str = "The diff modifies CamelContext.java adding a null check on exchange body.",
    evidence_content: str = "diff --git a/src/CamelContext.java\n+if (body != null) { process(body); }",
) -> CommitInvestigationReport:
    locs = localization or []
    recs = recommendations or []
    return CommitInvestigationReport(
        commit_id="abc123",
        project="camel",
        risk_assessment=RiskAssessment(level=RiskLevel.HIGH, confidence=0.85),
        evidence=[EvidenceItem(
            type=EvidenceType.DIFF_HUNK,
            source="src/CamelContext.java",
            content=evidence_content,
            relevance="Primary diff",
        )],
        findings=["Null pointer risk in exchange processing"],
        localization=locs,
        reasoning_summary=reasoning,
        recommendations=recs,
        turn_count=1,
    )


def _make_jira_issue(
    description: str = "NullPointerException when exchange body is null in CamelContext",
) -> JiraIssue:
    return JiraIssue(
        key="CAMEL-1234",
        summary="NPE in CamelContext exchange processing",
        description=description,
        priority="Major",
        components=["camel-core"],
        resolution="Fixed",
        status="Closed",
    )


class TestParseJudgeResponse:
    def test_plain_json(self):
        result = _parse_judge_response('{"score": 3, "justification": "good match"}')
        assert result["score"] == 3

    def test_markdown_fenced(self):
        text = '```json\n{"score": 2, "justification": "partial"}\n```'
        result = _parse_judge_response(text)
        assert result["score"] == 2

    def test_json_embedded_in_text(self):
        text = 'Here is my assessment:\n{"score": 1, "justification": "vague"}\nDone.'
        result = _parse_judge_response(text)
        assert result["score"] == 1

    def test_empty_returns_empty(self):
        assert _parse_judge_response("") == {}

    def test_garbage_returns_empty(self):
        assert _parse_judge_response("not json at all") == {}


class TestJudgeResult:
    def test_valid_result(self):
        r = JudgeResult(dimension="D3", score=3, max_score=4, normalized=0.75, justification="ok")
        assert r.is_valid

    def test_invalid_negative(self):
        r = JudgeResult(dimension="D3", score=-1, max_score=4, normalized=0.0, justification="bad")
        assert not r.is_valid

    def test_invalid_over_max(self):
        r = JudgeResult(dimension="D3", score=5, max_score=4, normalized=1.0, justification="bad")
        assert not r.is_valid


class TestReasoningJudgeD3:
    def test_d3_returns_normalized_score(self):
        judge = ReasoningJudge(_StubJudgeProvider(score=3))
        report = _make_report()
        jira = _make_jira_issue()

        result = judge.score_d3_root_cause(report, jira)
        assert result.dimension == "D3_diagnosis"
        assert result.score == 3
        assert result.max_score == 4
        assert result.normalized == 0.75
        assert "test justification" in result.justification

    def test_d3_clamps_over_max_score(self):
        judge = ReasoningJudge(_StubJudgeProvider(score=99))
        result = judge.score_d3_root_cause(_make_report(), _make_jira_issue())
        assert result.score == 4

    def test_d3_with_fix_files(self):
        judge = ReasoningJudge(_StubJudgeProvider(score=2))
        result = judge.score_d3_root_cause(
            _make_report(), _make_jira_issue(),
            fix_files={"src/CamelContext.java", "src/Exchange.java"},
        )
        assert result.is_valid

    def test_d3_llm_failure_returns_zero(self):
        judge = ReasoningJudge(_FailingProvider())
        result = judge.score_d3_root_cause(_make_report(), _make_jira_issue())
        assert result.score == 0
        assert "failed" in result.justification.lower()


class TestReasoningJudgeD5:
    def test_d5_returns_normalized_score(self):
        judge = ReasoningJudge(_StubJudgeProvider(score=2))
        report = _make_report(recommendations=[
            Recommendation(action="Add null check", priority=RecommendationPriority.HIGH, rationale="Prevents NPE"),
        ])
        jira = _make_jira_issue()

        result = judge.score_d5_recommendations(report, jira)
        assert result.dimension == "D5_recommendations"
        assert result.score == 2
        assert result.max_score == 3
        assert abs(result.normalized - 2 / 3) < 0.01

    def test_d5_clamps_over_max_score(self):
        judge = ReasoningJudge(_StubJudgeProvider(score=50))
        report = _make_report(recommendations=[
            Recommendation(action="Fix it", priority=RecommendationPriority.MEDIUM, rationale="test"),
        ])
        result = judge.score_d5_recommendations(report, _make_jira_issue())
        assert result.score == 3

    def test_d5_llm_failure_returns_zero(self):
        judge = ReasoningJudge(_FailingProvider())
        report = _make_report(recommendations=[
            Recommendation(action="Fix it", priority=RecommendationPriority.MEDIUM, rationale="test"),
        ])
        result = judge.score_d5_recommendations(report, _make_jira_issue())
        assert result.score == 0


class TestD6EvidenceGrounding:
    def test_full_grounding(self):
        """Agent localizes actual files, reasoning mentions them, evidence has diff content."""
        report = _make_report(
            localization=[LocalizationClaim(file="src/CamelContext.java", rationale="NPE risk")],
            reasoning="The diff modifies CamelContext.java adding a null check on exchange body.",
        )
        result = ReasoningJudge.score_d6_evidence_grounding(
            report,
            actual_files={"src/main/java/CamelContext.java"},
        )
        assert result.dimension == "D6_evidence_grounding"
        assert result.score >= 3
        assert result.max_score == 4
        assert result.normalized >= 0.75

    def test_no_grounding(self):
        """Agent has no localization, generic reasoning, minimal evidence."""
        report = _make_report(
            localization=[],
            reasoning="This commit might have issues.",
            evidence_content="Numeric features only",
        )
        result = ReasoningJudge.score_d6_evidence_grounding(
            report, actual_files={"src/Foo.java"},
        )
        assert result.score == 0
        assert result.normalized == 0.0

    def test_partial_grounding(self):
        """Agent localizes files but wrong ones, reasoning doesn't mention actual files."""
        report = _make_report(
            localization=[LocalizationClaim(file="src/Wrong.java", rationale="maybe")],
            reasoning="Some generic reasoning about code quality issues.",
        )
        result = ReasoningJudge.score_d6_evidence_grounding(
            report, actual_files={"src/CamelContext.java"},
        )
        # Has localization (1 point) + has diff evidence (1 point) = 2, no file overlap, no file mention
        assert 1 <= result.score <= 2

    def test_no_actual_files(self):
        """When actual_files is None, only check localization and evidence existence."""
        report = _make_report(
            localization=[LocalizationClaim(file="src/Foo.java", rationale="test")],
        )
        result = ReasoningJudge.score_d6_evidence_grounding(report, actual_files=None)
        # Has localization (1 point) + has specific evidence (1 point)
        assert result.score >= 1


class TestEvalHarnessD6Integration:
    """Test that D6 is wired into the eval harness correctly."""

    @pytest.fixture
    def _harness(self):
        from unittest.mock import MagicMock
        gt = MagicMock()
        gt.get_chain.return_value = MagicMock(fix_hashes=[], issue_keys=[])
        from commit_investigator.eval_harness import EvalHarness
        return EvalHarness(ground_truth=gt)

    def test_d6_present_in_results(self, _harness):
        report = _make_report()
        result = _harness.evaluate_report(report, buggy_label=False)
        assert "D6" in result.scores
        assert result.scores["D6"].dimension == "D6_evidence_grounding"

    def test_d6_automated(self, _harness):
        report = _make_report()
        result = _harness.evaluate_report(report, buggy_label=False)
        assert result.scores["D6"].automated is True


class TestEvalHarnessJudgeFallback:
    """Test that D3/D5 fall back to non-judge scoring when no judge_provider is set."""

    @pytest.fixture
    def _harness_no_judge(self):
        from unittest.mock import MagicMock
        gt = MagicMock()
        gt.get_chain.return_value = MagicMock(fix_hashes=["fix123"], issue_keys=["CAMEL-1234"])
        gt.get_issue_keys.return_value = ["CAMEL-1234"]

        jira = MagicMock()
        jira.get_issue.return_value = _make_jira_issue()

        from commit_investigator.eval_harness import EvalHarness
        return EvalHarness(ground_truth=gt, jira_client=jira)

    def test_d3_uses_word_overlap_fallback(self, _harness_no_judge):
        report = _make_report(reasoning="NullPointerException exchange body CamelContext")
        result = _harness_no_judge.evaluate_report(report, buggy_label=True)
        assert "D3" in result.scores
        assert "fallback" in result.scores["D3"].details.lower()

    def test_d5_uses_stub_fallback(self, _harness_no_judge):
        report = _make_report(recommendations=[
            Recommendation(action="Fix NPE", priority=RecommendationPriority.HIGH, rationale="test"),
        ])
        result = _harness_no_judge.evaluate_report(report, buggy_label=True)
        assert "D5" in result.scores
        assert "stub" in result.scores["D5"].details.lower()


class TestD3FixDiffFallback:
    """AC-1, AC-2, AC-3, AC-5: D3 fallback for empty JIRA descriptions."""

    def test_fallback_rubric_exists_and_mentions_fix_diff(self):
        """AC-1: D3_FIX_DIFF_FALLBACK_RUBRIC exists and describes fix-diff oracle."""
        assert "fix diff" in D3_FIX_DIFF_FALLBACK_RUBRIC.lower()
        assert "{fix_files}" in D3_FIX_DIFF_FALLBACK_RUBRIC
        assert "{agent_reasoning}" in D3_FIX_DIFF_FALLBACK_RUBRIC

    def test_fallback_method_exists_on_judge(self):
        judge = ReasoningJudge(_StubJudgeProvider(score=2))
        assert hasattr(judge, "score_d3_root_cause_fix_diff_fallback")

    def test_fallback_method_returns_valid_judge_result(self):
        judge = ReasoningJudge(_StubJudgeProvider(score=3, justification="fix diff match"))
        jira_no_desc = _make_jira_issue(description=None)
        result = judge.score_d3_root_cause_fix_diff_fallback(
            _make_report(), jira_no_desc, fix_files={"src/CamelContext.java"}
        )
        assert result.dimension == "D3_diagnosis"
        assert result.score == 3
        assert result.normalized == 0.75

    # AC-5a: empty description + fix files → fallback used, score > 0
    def test_harness_empty_description_with_fix_files_uses_fallback(self):
        from unittest.mock import MagicMock

        from commit_investigator.eval_harness import EvalHarness

        gt = MagicMock()
        gt.get_chain.return_value = MagicMock(fix_hashes=["fix123"], issue_keys=["CAMEL-5678"])
        gt.get_issue_keys.return_value = ["CAMEL-5678"]

        jira = MagicMock()
        jira.get_issue.return_value = JiraIssue(
            key="CAMEL-5678",
            summary="NPE in exchange processing",
            description=None,  # empty
            priority="Major",
            components=[],
            resolution="Fixed",
            status="Closed",
        )

        # Provide a mock git provider so fix_files is non-empty
        git_provider = MagicMock()
        git_provider.get_touched_files.return_value = {"src/CamelContext.java", "src/Exchange.java"}

        harness = EvalHarness(
            ground_truth=gt,
            jira_client=jira,
            git_providers={"camel": git_provider},
            judge_provider=_StubJudgeProvider(score=3, justification="fix diff match"),
        )
        report = _make_report(reasoning="NPE when body is null in CamelContext.java")
        result = harness.evaluate_report(report, buggy_label=True)

        assert "D3" in result.scores
        d3 = result.scores["D3"]
        assert d3.score > 0.0
        assert "fix-diff-fallback" in d3.details

    # AC-5b: non-empty description → standard JIRA path, judge_oracle=jira
    def test_harness_non_empty_description_uses_jira_path(self):
        from unittest.mock import MagicMock

        from commit_investigator.eval_harness import EvalHarness

        gt = MagicMock()
        gt.get_chain.return_value = MagicMock(fix_hashes=["fix123"], issue_keys=["CAMEL-1234"])
        gt.get_issue_keys.return_value = ["CAMEL-1234"]

        jira = MagicMock()
        jira.get_issue.return_value = _make_jira_issue(
            description="NullPointerException in CamelContext exchange body"
        )

        harness = EvalHarness(
            ground_truth=gt,
            jira_client=jira,
            judge_provider=_StubJudgeProvider(score=4, justification="precise match"),
        )
        result = harness.evaluate_report(_make_report(), buggy_label=True)

        assert "D3" in result.scores
        d3 = result.scores["D3"]
        assert "judge_oracle=jira" in d3.details

    # AC-5c: empty description + no fix files → score=0 with informative message
    def test_harness_empty_description_no_fix_files_returns_zero(self):
        from unittest.mock import MagicMock

        from commit_investigator.eval_harness import EvalHarness

        gt = MagicMock()
        gt.get_chain.return_value = MagicMock(fix_hashes=[], issue_keys=["CAMEL-9999"])
        gt.get_issue_keys.return_value = ["CAMEL-9999"]

        jira = MagicMock()
        jira.get_issue.return_value = JiraIssue(
            key="CAMEL-9999",
            summary="Unknown bug",
            description="",  # empty string
            priority=None,
            components=[],
            resolution=None,
            status="Open",
        )

        harness = EvalHarness(
            ground_truth=gt,
            jira_client=jira,
            judge_provider=_StubJudgeProvider(score=3),
        )
        result = harness.evaluate_report(_make_report(), buggy_label=True)

        assert "D3" in result.scores
        d3 = result.scores["D3"]
        assert d3.score == 0.0
        assert "judge_oracle=unavailable" in d3.details


class TestEvalHarnessWithJudge:
    """Test that D3/D5 use the judge when judge_provider is set."""

    @pytest.fixture
    def _harness_with_judge(self):
        from unittest.mock import MagicMock
        gt = MagicMock()
        gt.get_chain.return_value = MagicMock(fix_hashes=["fix123"], issue_keys=["CAMEL-1234"])
        gt.get_issue_keys.return_value = ["CAMEL-1234"]

        jira = MagicMock()
        jira.get_issue.return_value = _make_jira_issue()

        from commit_investigator.eval_harness import EvalHarness
        return EvalHarness(
            ground_truth=gt,
            jira_client=jira,
            judge_provider=_StubJudgeProvider(score=3, justification="precise match"),
        )

    def test_d3_uses_judge(self, _harness_with_judge):
        report = _make_report()
        result = _harness_with_judge.evaluate_report(report, buggy_label=True)
        assert "D3" in result.scores
        assert "judge" in result.scores["D3"].details.lower()
        assert result.scores["D3"].score == 0.75

    def test_d5_uses_judge(self, _harness_with_judge):
        report = _make_report(recommendations=[
            Recommendation(action="Fix NPE", priority=RecommendationPriority.HIGH, rationale="test"),
        ])
        result = _harness_with_judge.evaluate_report(report, buggy_label=True)
        assert "D5" in result.scores
        assert "judge" in result.scores["D5"].details.lower()
        assert result.scores["D5"].score == 1.0  # 3/3
