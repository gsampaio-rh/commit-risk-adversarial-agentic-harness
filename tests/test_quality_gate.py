"""Tests for quality_gate: deterministic 4-trigger evaluate_gate().

AC-7: One test per canonical trigger (T1-T4) plus no-trigger baseline.
"""

from commit_investigator.quality_gate import (
    T1_NO_SUPPORTED_WITH_DEFECT,
    T2_AMBIGUOUS_EMPTY_LOC_TRUNCATED,
    T3_SCHEMA_VALIDATION_FAILURE,
    T4_MASKED_EMPTY_FINDINGS,
    GateResult,
    HypothesisArtifact,
    evaluate_gate,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_artifact(**overrides) -> HypothesisArtifact:
    """Build a default all-clear artifact with selective overrides."""
    defaults = {
        "supported_count": 1,
        "production_defect_signals": False,
        "localization": [{"file": "Foo.java"}],
        "diff_truncated": False,
        "archetype_is_ambiguous": False,
        "findings": ["Some finding"],
        "validation_error": None,
    }
    defaults.update(overrides)
    return HypothesisArtifact(**defaults)


# ---------------------------------------------------------------------------
# AC-7: One test per trigger
# ---------------------------------------------------------------------------


class TestTriggerT1:
    """T1: supported_count == 0 AND production_defect_signals."""

    def test_fires_when_no_supported_and_defect_signals(self) -> None:
        artifact = _make_artifact(supported_count=0, production_defect_signals=True)
        result = evaluate_gate(artifact)
        assert result.follow_up_needed is True
        assert T1_NO_SUPPORTED_WITH_DEFECT in result.signals

    def test_no_fire_when_supported_count_positive(self) -> None:
        artifact = _make_artifact(supported_count=1, production_defect_signals=True)
        result = evaluate_gate(artifact)
        assert T1_NO_SUPPORTED_WITH_DEFECT not in result.signals

    def test_no_fire_when_no_defect_signals(self) -> None:
        artifact = _make_artifact(supported_count=0, production_defect_signals=False)
        result = evaluate_gate(artifact)
        assert T1_NO_SUPPORTED_WITH_DEFECT not in result.signals


class TestTriggerT2:
    """T2: localization == [] AND diff_truncated AND archetype_is_ambiguous.

    EC-1: All THREE conjuncts required.
    """

    def test_fires_when_all_three_conjuncts(self) -> None:
        artifact = _make_artifact(
            localization=[],
            diff_truncated=True,
            archetype_is_ambiguous=True,
        )
        result = evaluate_gate(artifact)
        assert result.follow_up_needed is True
        assert T2_AMBIGUOUS_EMPTY_LOC_TRUNCATED in result.signals

    def test_no_fire_when_localization_present(self) -> None:
        """EC-1: localization alone not empty."""
        artifact = _make_artifact(
            localization=[{"file": "Foo.java"}],
            diff_truncated=True,
            archetype_is_ambiguous=True,
        )
        result = evaluate_gate(artifact)
        assert T2_AMBIGUOUS_EMPTY_LOC_TRUNCATED not in result.signals

    def test_no_fire_when_diff_not_truncated(self) -> None:
        """EC-1: diff_truncated alone False."""
        artifact = _make_artifact(
            localization=[],
            diff_truncated=False,
            archetype_is_ambiguous=True,
        )
        result = evaluate_gate(artifact)
        assert T2_AMBIGUOUS_EMPTY_LOC_TRUNCATED not in result.signals

    def test_no_fire_when_archetype_clean(self) -> None:
        """EC-1: clean archetype (archetype_is_ambiguous=False)."""
        artifact = _make_artifact(
            localization=[],
            diff_truncated=True,
            archetype_is_ambiguous=False,
        )
        result = evaluate_gate(artifact)
        assert T2_AMBIGUOUS_EMPTY_LOC_TRUNCATED not in result.signals


class TestTriggerT3:
    """T3: HypothesisArtifact validation failure."""

    def test_fires_when_validation_error_present(self) -> None:
        artifact = _make_artifact(validation_error="field required: hypotheses")
        result = evaluate_gate(artifact)
        assert result.follow_up_needed is True
        assert T3_SCHEMA_VALIDATION_FAILURE in result.signals

    def test_no_fire_when_no_validation_error(self) -> None:
        artifact = _make_artifact(validation_error=None)
        result = evaluate_gate(artifact)
        assert T3_SCHEMA_VALIDATION_FAILURE not in result.signals


class TestTriggerT4:
    """T4: findings == ['Investigation completed'] (masked empty output).

    EC-2: Exact match only — empty list or superset must NOT fire.
    """

    def test_fires_on_exact_masked_findings(self) -> None:
        artifact = _make_artifact(findings=["Investigation completed"])
        result = evaluate_gate(artifact)
        assert result.follow_up_needed is True
        assert T4_MASKED_EMPTY_FINDINGS in result.signals

    def test_no_fire_on_empty_list(self) -> None:
        """EC-2: empty list is different from masked output."""
        artifact = _make_artifact(findings=[])
        result = evaluate_gate(artifact)
        assert T4_MASKED_EMPTY_FINDINGS not in result.signals

    def test_no_fire_on_superset(self) -> None:
        """EC-2: extra findings alongside the masked string must not fire."""
        artifact = _make_artifact(findings=["Investigation completed", "Other finding"])
        result = evaluate_gate(artifact)
        assert T4_MASKED_EMPTY_FINDINGS not in result.signals

    def test_no_fire_on_real_finding(self) -> None:
        artifact = _make_artifact(findings=["Boolean.class guard removed"])
        result = evaluate_gate(artifact)
        assert T4_MASKED_EMPTY_FINDINGS not in result.signals


# ---------------------------------------------------------------------------
# No triggers: all-clear case
# ---------------------------------------------------------------------------


class TestNoTriggers:
    def test_all_clear_returns_false(self) -> None:
        artifact = _make_artifact()
        result = evaluate_gate(artifact)
        assert result.follow_up_needed is False
        assert result.signals == []
        assert result.reason == ""

    def test_gate_result_is_frozen(self) -> None:
        artifact = _make_artifact()
        result = evaluate_gate(artifact)
        try:
            result.follow_up_needed = True  # type: ignore[misc]
            raise AssertionError("Expected frozen dataclass to raise")
        except (AttributeError, TypeError):
            pass

    def test_returns_gate_result_type(self) -> None:
        artifact = _make_artifact()
        result = evaluate_gate(artifact)
        assert isinstance(result, GateResult)


# ---------------------------------------------------------------------------
# Multi-trigger: multiple signals fire simultaneously
# ---------------------------------------------------------------------------


class TestMultiTrigger:
    def test_multiple_signals_all_reported(self) -> None:
        artifact = _make_artifact(
            supported_count=0,
            production_defect_signals=True,
            findings=["Investigation completed"],
        )
        result = evaluate_gate(artifact)
        assert result.follow_up_needed is True
        assert T1_NO_SUPPORTED_WITH_DEFECT in result.signals
        assert T4_MASKED_EMPTY_FINDINGS in result.signals

    def test_reason_contains_all_signals(self) -> None:
        artifact = _make_artifact(
            supported_count=0,
            production_defect_signals=True,
            findings=["Investigation completed"],
        )
        result = evaluate_gate(artifact)
        assert T1_NO_SUPPORTED_WITH_DEFECT in result.reason
        assert T4_MASKED_EMPTY_FINDINGS in result.reason
