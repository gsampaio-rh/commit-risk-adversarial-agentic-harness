"""Tests for the investigation report schema."""

import json

import pytest
from pydantic import ValidationError

from commit_investigator.report import (
    CommitInvestigationReport,
    EvidenceItem,
    EvidenceType,
    LocalizationClaim,
    Recommendation,
    RecommendationPriority,
    RiskAssessment,
    RiskLevel,
    export_json_schema,
)


def _make_evidence() -> EvidenceItem:
    return EvidenceItem(
        type=EvidenceType.DIFF_HUNK,
        source="src/Main.java",
        content="risky code",
        relevance="NPE risk",
    )


def _make_report(**overrides) -> CommitInvestigationReport:
    defaults = dict(
        commit_id="abc123",
        project="camel",
        risk_assessment=RiskAssessment(level=RiskLevel.HIGH, confidence=0.8),
        evidence=[_make_evidence()],
        findings=["Found issue"],
        reasoning_summary="Investigation reasoning.",
        turn_count=1,
    )
    defaults.update(overrides)
    return CommitInvestigationReport(**defaults)


class TestRiskAssessment:
    def test_valid_confidence(self):
        ra = RiskAssessment(level=RiskLevel.LOW, confidence=0.5)
        assert ra.confidence == 0.5

    def test_confidence_zero(self):
        ra = RiskAssessment(level=RiskLevel.LOW, confidence=0.0)
        assert ra.confidence == 0.0

    def test_confidence_one(self):
        ra = RiskAssessment(level=RiskLevel.LOW, confidence=1.0)
        assert ra.confidence == 1.0

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            RiskAssessment(level=RiskLevel.LOW, confidence=-0.1)

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError):
            RiskAssessment(level=RiskLevel.LOW, confidence=1.1)


class TestCommitInvestigationReport:
    def test_valid_minimal_report(self):
        report = _make_report()
        assert report.commit_id == "abc123"
        assert report.risk_assessment.level == RiskLevel.HIGH

    def test_empty_evidence_rejected(self):
        with pytest.raises(ValidationError):
            _make_report(evidence=[])

    def test_serialization_roundtrip(self):
        report = _make_report()
        json_str = report.model_dump_json()
        restored = CommitInvestigationReport.model_validate_json(json_str)
        assert restored.commit_id == report.commit_id
        assert restored.risk_assessment.confidence == report.risk_assessment.confidence

    def test_with_localization(self):
        report = _make_report(
            localization=[LocalizationClaim(file="src/Foo.java", rationale="Risky")]
        )
        assert len(report.localization) == 1

    def test_with_recommendations(self):
        report = _make_report(
            recommendations=[
                Recommendation(action="Add null check", priority=RecommendationPriority.HIGH, rationale="NPE risk")
            ]
        )
        assert len(report.recommendations) == 1

    def test_no_localization_or_recs_is_valid(self):
        report = _make_report(localization=[], recommendations=[])
        assert report is not None


class TestJsonSchema:
    def test_export_is_valid_json(self):
        schema_str = export_json_schema()
        parsed = json.loads(schema_str)
        assert "title" in parsed
        assert parsed["title"] == "CommitInvestigationReport"

    def test_export_is_stable(self):
        s1 = export_json_schema()
        s2 = export_json_schema()
        assert s1 == s2
