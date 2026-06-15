"""Unit tests for confidence_model tier boundaries and score clamping."""

import pytest

from commit_investigator.analysis.confidence_model import compute_confidence
from commit_investigator.analysis.signal_extractor import ConfidenceSignals


def _signals(**overrides) -> ConfidenceSignals:
    defaults = {
        "supported_count": 0,
        "evidence_density": 0.0,
        "router_agreement": 0.5,
        "hypothesis_diversity": 0,
        "diff_signal_ratio": 0.0,
        "missing_context_flags": 0,
        "commit_message_clarity": 0.0,
    }
    defaults.update(overrides)
    return ConfidenceSignals(**defaults)


class TestConfidenceTiers:
    def test_high_tier_strong_signals(self):
        result = compute_confidence(_signals(
            supported_count=3,
            evidence_density=1.0,
            router_agreement=1.0,
            hypothesis_diversity=3,
            commit_message_clarity=1.0,
        ))
        assert result.tier == "HIGH"
        assert result.score >= 0.65

    def test_medium_tier_moderate_signals(self):
        result = compute_confidence(_signals(
            supported_count=1,
            evidence_density=1.0,
            router_agreement=0.5,
            hypothesis_diversity=0,
        ))
        assert result.tier == "MEDIUM"
        assert 0.40 <= result.score < 0.65

    def test_low_tier_weak_signals(self):
        result = compute_confidence(_signals(
            supported_count=0,
            evidence_density=0.0,
            router_agreement=0.5,
            hypothesis_diversity=0,
            missing_context_flags=3,
        ))
        assert result.tier == "LOW"
        assert result.score < 0.40

    def test_boundary_score_at_least_0_65_is_high(self):
        result = compute_confidence(_signals(
            supported_count=2,
            evidence_density=1.0,
            router_agreement=1.0,
            hypothesis_diversity=0,
        ))
        assert result.score >= 0.65
        assert result.tier == "HIGH"

    def test_boundary_score_at_least_0_40_is_medium(self):
        result = compute_confidence(_signals(
            supported_count=1,
            evidence_density=1.0,
            router_agreement=0.5,
            hypothesis_diversity=0,
        ))
        assert result.score >= 0.40
        assert result.tier == "MEDIUM"

    def test_boundary_score_below_0_40_is_low(self):
        result = compute_confidence(_signals(
            supported_count=1,
            evidence_density=0.75,
            router_agreement=0.5,
            hypothesis_diversity=0,
        ))
        assert result.score < 0.40
        assert result.tier == "LOW"

    def test_exact_boundary_score_0_65_is_high(self):
        """EC-7: mathematically exact 0.65 must map to HIGH tier."""
        result = compute_confidence(_signals(
            supported_count=3,
            evidence_density=0.5,
            router_agreement=1.0,
            missing_context_flags=4,
        ))
        assert result.score == pytest.approx(0.65)
        assert result.tier == "HIGH"

    def test_exact_boundary_score_0_40_is_medium(self):
        """EC-7: mathematically exact 0.40 must map to MEDIUM tier."""
        result = compute_confidence(_signals(
            supported_count=3,
            evidence_density=0.5,
            router_agreement=0.0,
            missing_context_flags=4,
        ))
        assert result.score == pytest.approx(0.40)
        assert result.tier == "MEDIUM"


class TestScoreClamping:
    def test_score_clamped_to_unit_interval(self):
        result = compute_confidence(_signals(
            supported_count=99,
            evidence_density=1.0,
            router_agreement=1.0,
            hypothesis_diversity=99,
            commit_message_clarity=1.0,
        ))
        assert 0.0 <= result.score <= 1.0

    def test_missing_context_penalty_capped(self):
        result = compute_confidence(_signals(missing_context_flags=99))
        assert result.score >= 0.0

    @pytest.mark.parametrize("flags", [3, 10])
    def test_missing_penalty_saturates_at_three(self, flags: int):
        capped = compute_confidence(_signals(missing_context_flags=3)).score
        result = compute_confidence(_signals(missing_context_flags=flags)).score
        assert result == pytest.approx(capped)
