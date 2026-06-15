"""Confidence model: deterministic scalar score and tier from extracted signals.

Equation (fixed weights from spike design in state.json):
  score = 0.35*supported_score
        + 0.25*router_agreement
        + 0.20*evidence_density
        + 0.10*diversity_score
        + 0.10*message_clarity
        - 0.05*missing_context_penalty

Each component is normalized to [0,1]. Score is clamped to [0.0, 1.0].

Tier thresholds:
  score >= 0.65 → HIGH   (terminate with confidence)
  0.40 <= score < 0.65 → MEDIUM  (request turn-2 follow-up)
  score < 0.40 → LOW    (flag LOW_CONFIDENCE, cap risk at MEDIUM)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from commit_investigator.analysis.signal_extractor import ConfidenceSignals

__all__ = ["ConfidenceResult", "compute_confidence", "ConfidenceTier"]

ConfidenceTier = Literal["HIGH", "MEDIUM", "LOW"]

# Weight vector (must sum to 1.0 before penalty)
_W_SUPPORTED = 0.35
_W_ROUTER = 0.25
_W_EVIDENCE = 0.20
_W_DIVERSITY = 0.10
_W_CLARITY = 0.10
_W_MISSING_PENALTY = 0.05

# Normalization constants
_MAX_SUPPORTED = 3          # >3 SUPPORTED hypotheses → capped at 1.0
_MAX_DIVERSITY = 3          # 3 distinct categories in top-3 → max diversity
_MAX_MISSING_FLAGS = 3      # >3 missing flags → capped penalty at 1.0

# Tier boundaries (inclusive lower bound)
_TIER_HIGH = 0.65
_TIER_MEDIUM = 0.40


@dataclass(frozen=True)
class ConfidenceResult:
    """Output of compute_confidence: scalar score and discrete tier."""

    score: float
    """Weighted confidence score in [0.0, 1.0]."""

    tier: ConfidenceTier
    """Confidence tier: HIGH (≥0.65), MEDIUM (0.40–0.65), LOW (<0.40)."""


def _normalize_supported(count: int) -> float:
    """Map supported_count to [0,1]; >3 capped at 1.0."""
    return min(count / _MAX_SUPPORTED, 1.0)


def _normalize_diversity(count: int) -> float:
    """Map hypothesis_diversity to [0,1]; >3 capped at 1.0."""
    return min(count / _MAX_DIVERSITY, 1.0)


def _normalize_missing_penalty(flag_count: int) -> float:
    """Map missing_context_flags to penalty in [0,1]; >3 capped at 1.0."""
    return min(flag_count / _MAX_MISSING_FLAGS, 1.0)


def _score_to_tier(score: float) -> ConfidenceTier:
    if score >= _TIER_HIGH:
        return "HIGH"
    if score >= _TIER_MEDIUM:
        return "MEDIUM"
    return "LOW"


def compute_confidence(signals: ConfidenceSignals) -> ConfidenceResult:
    """Compute scalar confidence score and tier from 7 extracted signals.

    All weights are fixed (not learned): design rationale is in state.json
    spike-investigation-confidence-equation. Calibration runs in
    calibrate_confidence.py validate the weights against ground truth D1 outcomes.
    """
    supported_score = _normalize_supported(signals.supported_count)
    diversity_score = _normalize_diversity(signals.hypothesis_diversity)
    missing_penalty = _normalize_missing_penalty(signals.missing_context_flags)

    raw = (
        _W_SUPPORTED * supported_score
        + _W_ROUTER * signals.router_agreement
        + _W_EVIDENCE * signals.evidence_density
        + _W_DIVERSITY * diversity_score
        + _W_CLARITY * signals.commit_message_clarity
        - _W_MISSING_PENALTY * missing_penalty
    )

    score = round(max(0.0, min(1.0, raw)), 10)
    return ConfidenceResult(score=score, tier=_score_to_tier(score))
