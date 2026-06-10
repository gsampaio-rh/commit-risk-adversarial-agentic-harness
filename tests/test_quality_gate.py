"""Tests for quality_gate stub: evaluate_gate() passthrough behavior."""

from commit_investigator.quality_gate import GateResult, evaluate_gate


class TestEvaluateGate:
    def test_returns_gate_result(self):
        result = evaluate_gate(follow_up_needed=True)
        assert isinstance(result, GateResult)

    def test_passthrough_true(self):
        result = evaluate_gate(follow_up_needed=True)
        assert result.follow_up_needed is True

    def test_passthrough_false(self):
        result = evaluate_gate(follow_up_needed=False)
        assert result.follow_up_needed is False

    def test_signals_empty_in_stub(self):
        result = evaluate_gate(follow_up_needed=True)
        assert result.signals == []

    def test_reason_empty_in_stub(self):
        result = evaluate_gate(follow_up_needed=True)
        assert result.reason == ""

    def test_gate_result_is_frozen(self):
        result = evaluate_gate(follow_up_needed=False)
        try:
            result.follow_up_needed = True  # type: ignore[misc]
            raise AssertionError("Expected frozen dataclass to raise")
        except (AttributeError, TypeError):
            pass
