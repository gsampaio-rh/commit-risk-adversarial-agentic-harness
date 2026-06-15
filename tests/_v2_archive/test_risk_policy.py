"""Tests for risk_policy: PolicyVerdict, evaluate_risk, and reasoning helpers.

These tests were written pre-extraction (AC-7) against the orchestrator private
functions and now run against the canonical risk_policy module. The behavior
contract is unchanged — every case that passed pre-move must pass post-move.
"""

import pytest

from commit_investigator.context.context_builder import InvestigationContext
from commit_investigator.analysis.evidence_tagger import TagResult
from commit_investigator.analysis.report import RiskLevel
from commit_investigator.hypothesis.hypothesis_engine import HypothesisSpec
from commit_investigator.analysis.risk_policy import (
    PolicyVerdict,
    _reasoning_all_speculative_or_unverifiable,
    evaluate_risk,
    evaluate_risk_from_hypotheses,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _version_bump_context() -> InvestigationContext:
    """Canonical version-bump archetype: pom.xml + type migration."""
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


def _generic_diff_context(diff: str = "") -> InvestigationContext:
    """Non-archetype context: generic production diff, no version-bump signals."""
    return InvestigationContext(
        commit_id="aabbccdd1234",
        project="camel",
        message="Refactor service layer",
        diff=diff or "diff --git a/ServiceImpl.java\n+    return process(value);\n",
        touched_files=["ServiceImpl.java"],
        csv_features={},
        file_histories={},
        author_stats=None,
    )


def _guard_removal_context() -> InvestigationContext:
    """Context with explicit guard removal — opts out of clean-commit cap."""
    ctx = _version_bump_context()
    ctx.diff += "\n-        if (value != null) {\n-            return value;\n-        }\n"
    return ctx


def _lifecycle_context() -> InvestigationContext:
    """Context with lifecycle ordering change — opts out of cap."""
    return InvestigationContext(
        commit_id="fbf0ffad627b",
        project="camel",
        message="CAMEL-10279 fix lifecycle ordering",
        diff="-        if (started) {\n-            SmartLifecycle.stop();\n+        // removed guard\n",
        touched_files=["RoutesCollector.java"],
        csv_features={},
        file_histories={},
        author_stats=None,
    )


def _concurrency_context() -> InvestigationContext:
    """Context with concurrency change — opts out of cap."""
    return InvestigationContext(
        commit_id="cc112233",
        project="camel",
        message="Remove lock from hot path",
        diff="-        synchronized (this) {\n-            counter++;\n-        }\n+        counter++;\n",
        touched_files=["Counter.java"],
        csv_features={},
        file_histories={},
        author_stats=None,
    )


def _tag(tier: str = "SPECULATIVE") -> TagResult:
    """Minimal TagResult for evaluate_risk_from_hypotheses branch tests."""
    return TagResult(tier=tier, quote_in_diff=False, match_method="absent")


def _with_router(ctx: InvestigationContext, probability: float | None) -> InvestigationContext:
    ctx.router_probability = probability
    return ctx


# ---------------------------------------------------------------------------
# _reasoning_all_speculative_or_unverifiable
# ---------------------------------------------------------------------------

class TestReasoningAllSpeculative:
    def test_speculative_only(self):
        reasoning = "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): migration change."
        assert _reasoning_all_speculative_or_unverifiable(reasoning) is True

    def test_unverifiable_only(self):
        reasoning = "STAGE 3: HYPOTHESIS 1 (UNVERIFIABLE): truncated diff."
        assert _reasoning_all_speculative_or_unverifiable(reasoning) is True

    def test_mixed_speculative_and_unverifiable(self):
        reasoning = (
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): migration. "
            "HYPOTHESIS 2 (UNVERIFIABLE): truncated."
        )
        assert _reasoning_all_speculative_or_unverifiable(reasoning) is True

    def test_returns_false_when_supported_present(self):
        reasoning = "STAGE 3: HYPOTHESIS A — SUPPORTED: removed guard at line 42."
        assert _reasoning_all_speculative_or_unverifiable(reasoning) is False

    def test_returns_false_for_empty_string(self):
        assert _reasoning_all_speculative_or_unverifiable("") is False

    def test_returns_false_when_stage3_has_supported(self):
        reasoning = (
            "STAGE 3: "
            "HYPOTHESIS 1 (SPECULATIVE): migration. "
            "HYPOTHESIS 2 — SUPPORTED: guard removed at line 88."
        )
        assert _reasoning_all_speculative_or_unverifiable(reasoning) is False

    def test_case_insensitive_match(self):
        assert _reasoning_all_speculative_or_unverifiable("speculative reasoning") is True
        assert _reasoning_all_speculative_or_unverifiable("SPECULATIVE reasoning") is True


# ---------------------------------------------------------------------------
# evaluate_risk — full AC-7 matrix (behavior-preserving vs old cap function)
# ---------------------------------------------------------------------------

class TestEvaluateRisk:
    # --- Passthrough cases (no cap ever applied) ---

    def test_low_passthrough(self):
        verdict = evaluate_risk(RiskLevel.LOW, _version_bump_context(), "STAGE 3: SPECULATIVE")
        assert verdict.risk_level == RiskLevel.LOW
        assert verdict.cap_applied is False

    def test_medium_passthrough(self):
        verdict = evaluate_risk(RiskLevel.MEDIUM, _version_bump_context(), "STAGE 3: SPECULATIVE")
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is False

    def test_low_passthrough_generic_context(self):
        verdict = evaluate_risk(RiskLevel.LOW, _generic_diff_context(), "")
        assert verdict.risk_level == RiskLevel.LOW
        assert verdict.cap_applied is False

    # --- HIGH capped to MEDIUM ---

    def test_high_capped_version_bump_speculative_reasoning(self):
        verdict = evaluate_risk(
            RiskLevel.HIGH,
            _version_bump_context(),
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): cross-version break.",
        )
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is True

    def test_high_capped_archetype_with_criterion_b_supported(self):
        """SUPPORTED criterion (b) still capped inside clean archetype."""
        ctx = _version_bump_context()
        ctx.diff += "\n+        return future;\n"
        reasoning = (
            "STAGE 3: HYPOTHESIS A — SUPPORTED: raw QueryFactory erasure breaks callers. "
            "STAGE 4: criterion (b) HIGH."
        )
        verdict = evaluate_risk(RiskLevel.HIGH, ctx, reasoning)
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is True

    # --- CRITICAL capped to MEDIUM ---

    def test_critical_capped_version_bump_speculative(self):
        verdict = evaluate_risk(
            RiskLevel.CRITICAL,
            _version_bump_context(),
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): cross-version break.",
        )
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is True

    def test_critical_capped_speculative_only_non_archetype(self):
        """Speculative-only reasoning caps globally — even on non-archetype diff."""
        verdict = evaluate_risk(
            RiskLevel.CRITICAL,
            _generic_diff_context(),
            "HYPOTHESIS 1 (SPECULATIVE): external caller impact. HYPOTHESIS 2 (UNVERIFIABLE): scope unknown.",
        )
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is True

    # --- Guard/lifecycle/concurrency prevents cap ---

    def test_high_not_capped_when_guard_removal(self):
        verdict = evaluate_risk(
            RiskLevel.HIGH,
            _guard_removal_context(),
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): migration.",
        )
        assert verdict.risk_level == RiskLevel.HIGH
        assert verdict.cap_applied is False

    def test_high_not_capped_when_lifecycle_change(self):
        verdict = evaluate_risk(
            RiskLevel.HIGH,
            _lifecycle_context(),
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): lifecycle ordering.",
        )
        assert verdict.risk_level == RiskLevel.HIGH
        assert verdict.cap_applied is False

    def test_high_not_capped_when_concurrency_change(self):
        verdict = evaluate_risk(
            RiskLevel.HIGH,
            _concurrency_context(),
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): race condition.",
        )
        assert verdict.risk_level == RiskLevel.HIGH
        assert verdict.cap_applied is False

    # --- Speculative-only caps globally (not only archetype) ---

    def test_high_capped_speculative_only_generic_context(self):
        """Global cap rule: speculative-only reasoning caps even without archetype match."""
        verdict = evaluate_risk(
            RiskLevel.HIGH,
            _generic_diff_context(),
            "HYPOTHESIS 1 (SPECULATIVE): assumed external behavior.",
        )
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is True

    # --- Supported hypothesis on non-archetype: NOT capped ---

    def test_high_not_capped_when_supported_on_generic_diff(self):
        verdict = evaluate_risk(
            RiskLevel.HIGH,
            _generic_diff_context(),
            "STAGE 3: HYPOTHESIS A — SUPPORTED: removed null-check at line 42.",
        )
        assert verdict.risk_level == RiskLevel.HIGH
        assert verdict.cap_applied is False

    # --- Edge cases ---

    def test_empty_reasoning_no_cap_on_generic_diff(self):
        """Empty reasoning has no speculative signal → no global cap on generic diff."""
        verdict = evaluate_risk(RiskLevel.HIGH, _generic_diff_context(), "")
        assert verdict.risk_level == RiskLevel.HIGH
        assert verdict.cap_applied is False

    def test_empty_reasoning_caps_on_archetype(self):
        """Archetype alone triggers cap regardless of reasoning content."""
        verdict = evaluate_risk(RiskLevel.HIGH, _version_bump_context(), "")
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is True

    def test_null_diff_no_crash(self):
        ctx = _generic_diff_context()
        ctx.diff = None  # type: ignore[assignment]
        verdict = evaluate_risk(RiskLevel.HIGH, ctx, "SPECULATIVE")
        assert verdict.risk_level in (RiskLevel.HIGH, RiskLevel.MEDIUM)

    # --- PolicyVerdict fields ---

    def test_cap_reason_populated_for_archetype_cap(self):
        verdict = evaluate_risk(
            RiskLevel.HIGH,
            _version_bump_context(),
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): migration.",
        )
        assert verdict.cap_reason == "clean_archetype_no_production_defect_signals"
        assert "cap_to_MEDIUM:clean_archetype" in verdict.applied_rules

    def test_cap_reason_populated_for_speculative_only_cap(self):
        verdict = evaluate_risk(
            RiskLevel.HIGH,
            _generic_diff_context(),
            "HYPOTHESIS 1 (SPECULATIVE): assumed external behavior.",
        )
        assert verdict.cap_reason == "speculative_or_unverifiable_only"
        assert "cap_to_MEDIUM:speculative_only" in verdict.applied_rules

    def test_cap_reason_empty_when_no_cap(self):
        verdict = evaluate_risk(
            RiskLevel.HIGH,
            _generic_diff_context(),
            "HYPOTHESIS A — SUPPORTED: removed guard at line 42.",
        )
        assert verdict.cap_reason == ""
        assert verdict.applied_rules == []

    def test_supported_count_one_when_supported_reasoning(self):
        verdict = evaluate_risk(
            RiskLevel.HIGH,
            _generic_diff_context(),
            "HYPOTHESIS A — SUPPORTED: removed null-check at line 42.",
        )
        assert verdict.supported_count == 1

    def test_supported_count_zero_when_speculative_only(self):
        verdict = evaluate_risk(
            RiskLevel.HIGH,
            _generic_diff_context(),
            "HYPOTHESIS 1 (SPECULATIVE): assumed behavior.",
        )
        assert verdict.supported_count == 0

    def test_verdict_is_frozen_dataclass(self):
        verdict = evaluate_risk(RiskLevel.LOW, _generic_diff_context(), "")
        assert isinstance(verdict, PolicyVerdict)
        with pytest.raises((AttributeError, TypeError)):
            verdict.risk_level = RiskLevel.HIGH  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AC-6: determinism test (same inputs → same outputs ×1000)
# ---------------------------------------------------------------------------

class TestEvaluateRiskDeterminism:
    def test_deterministic_1000x_archetype_cap(self):
        """Same inputs must produce identical PolicyVerdict 1000 times."""
        ctx = _version_bump_context()
        reasoning = "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): cross-version break."
        first = evaluate_risk(RiskLevel.HIGH, ctx, reasoning)
        for _ in range(999):
            result = evaluate_risk(RiskLevel.HIGH, ctx, reasoning)
            assert result.risk_level == first.risk_level
            assert result.cap_applied == first.cap_applied
            assert result.cap_reason == first.cap_reason
            assert result.applied_rules == first.applied_rules
            assert result.supported_count == first.supported_count

    def test_deterministic_1000x_no_cap_supported(self):
        ctx = _generic_diff_context()
        reasoning = "HYPOTHESIS A — SUPPORTED: removed null-check at line 42."
        first = evaluate_risk(RiskLevel.HIGH, ctx, reasoning)
        for _ in range(999):
            result = evaluate_risk(RiskLevel.HIGH, ctx, reasoning)
            assert result == first

    def test_deterministic_1000x_speculative_global_cap(self):
        ctx = _generic_diff_context()
        reasoning = "HYPOTHESIS 1 (SPECULATIVE): assumed external behavior."
        first = evaluate_risk(RiskLevel.CRITICAL, ctx, reasoning)
        for _ in range(999):
            result = evaluate_risk(RiskLevel.CRITICAL, ctx, reasoning)
            assert result == first


# ---------------------------------------------------------------------------
# evaluate_risk_from_hypotheses — live orchestrator path (TagResult input)
# ---------------------------------------------------------------------------

class TestEvaluateRiskFromHypotheses:
    def test_base_medium_passthrough_empty_tags(self):
        """AC-2a / EC-1: no supported, defect, or router → MEDIUM passthrough."""
        verdict = evaluate_risk_from_hypotheses([], _generic_diff_context())
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is False
        assert verdict.cap_reason == ""
        assert verdict.applied_rules == []
        assert verdict.supported_count == 0

    def test_supported_non_archetype_passthrough(self):
        """AC-2b: one SUPPORTED on generic diff → HIGH, no cap."""
        verdict = evaluate_risk_from_hypotheses([_tag("SUPPORTED")], _generic_diff_context())
        assert verdict.risk_level == RiskLevel.HIGH
        assert verdict.cap_applied is False
        assert verdict.cap_reason == ""
        assert verdict.applied_rules == []
        assert verdict.supported_count == 1

    def test_supported_clean_archetype_cap(self):
        """AC-2c: SUPPORTED inside clean archetype still capped."""
        verdict = evaluate_risk_from_hypotheses([_tag("SUPPORTED")], _version_bump_context())
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is True
        assert verdict.cap_reason == "clean_archetype_no_production_defect_signals"
        assert verdict.applied_rules == ["cap_to_MEDIUM:clean_archetype"]
        assert verdict.supported_count == 1

    def test_defect_override_on_archetype(self):
        """AC-2d: defect signals bypass cap (guard/lifecycle/concurrency contexts)."""
        for ctx_factory in (_guard_removal_context, _lifecycle_context, _concurrency_context):
            verdict = evaluate_risk_from_hypotheses([], ctx_factory())
            assert verdict.risk_level == RiskLevel.HIGH
            assert verdict.cap_applied is False
            assert verdict.cap_reason == ""
            assert verdict.applied_rules == []
            assert verdict.supported_count == 0

    def test_router_boundary_speculative_cap(self):
        """AC-2e / EC-2: router 0.70 inclusive → HIGH base, speculative cap on generic."""
        ctx = _with_router(_generic_diff_context(), 0.70)
        verdict = evaluate_risk_from_hypotheses([], ctx)
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is True
        assert verdict.cap_reason == "speculative_or_unverifiable_only"
        assert verdict.applied_rules == ["cap_to_MEDIUM:speculative_only"]
        assert verdict.supported_count == 0

    def test_router_below_threshold_medium(self):
        """AC-2f / EC-2: router 0.69 → MEDIUM passthrough."""
        ctx = _with_router(_generic_diff_context(), 0.69)
        verdict = evaluate_risk_from_hypotheses([], ctx)
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is False
        assert verdict.cap_reason == ""
        assert verdict.applied_rules == []
        assert verdict.supported_count == 0

    def test_router_on_archetype_clean_cap(self):
        """AC-2g: router on archetype → clean_archetype cap_reason wins."""
        ctx = _with_router(_version_bump_context(), 0.80)
        verdict = evaluate_risk_from_hypotheses([], ctx)
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is True
        assert verdict.cap_reason == "clean_archetype_no_production_defect_signals"
        assert verdict.applied_rules == ["cap_to_MEDIUM:clean_archetype"]
        assert verdict.supported_count == 0

    def test_router_on_non_archetype_speculative_cap(self):
        """AC-2h: router on generic diff → speculative_only cap."""
        ctx = _with_router(_generic_diff_context(), 0.80)
        verdict = evaluate_risk_from_hypotheses([], ctx)
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is True
        assert verdict.cap_reason == "speculative_or_unverifiable_only"
        assert verdict.applied_rules == ["cap_to_MEDIUM:speculative_only"]
        assert verdict.supported_count == 0

    def test_defect_only_high_no_cap(self):
        """AC-2i: defect alone drives HIGH without cap (_guard_removal_context per contract)."""
        verdict = evaluate_risk_from_hypotheses([], _guard_removal_context())
        assert verdict.risk_level == RiskLevel.HIGH
        assert verdict.cap_applied is False
        assert verdict.cap_reason == ""
        assert verdict.applied_rules == []
        assert verdict.supported_count == 0

    def test_supported_router_non_archetype_passthrough(self):
        """AC-2j: SUPPORTED + high router on generic → HIGH passthrough."""
        ctx = _with_router(_generic_diff_context(), 0.85)
        verdict = evaluate_risk_from_hypotheses([_tag("SUPPORTED")], ctx)
        assert verdict.risk_level == RiskLevel.HIGH
        assert verdict.cap_applied is False
        assert verdict.cap_reason == ""
        assert verdict.applied_rules == []
        assert verdict.supported_count == 1

    def test_router_none_coerced_to_zero(self):
        """EC-3: router_probability=None coerced to 0.0 — same as unset."""
        unset_verdict = evaluate_risk_from_hypotheses([], _generic_diff_context())
        none_ctx = _with_router(_generic_diff_context(), None)
        none_verdict = evaluate_risk_from_hypotheses([], none_ctx)
        assert none_verdict == unset_verdict
        assert none_verdict.risk_level == RiskLevel.MEDIUM
        assert none_verdict.cap_applied is False
        assert none_verdict.supported_count == 0

    def test_multiple_supported_count(self):
        """EC-4: multiple SUPPORTED tags increment supported_count."""
        tagged = [_tag("SUPPORTED"), _tag("SUPPORTED")]
        verdict = evaluate_risk_from_hypotheses(tagged, _generic_diff_context())
        assert verdict.supported_count == 2
        assert verdict.risk_level == RiskLevel.HIGH
        assert verdict.cap_applied is False

    def test_mixed_tiers_supported_count(self):
        """EC-5: only SUPPORTED tier increments supported_count."""
        tagged = [_tag("SPECULATIVE"), _tag("SUPPORTED"), _tag("UNVERIFIABLE"), _tag("REFUTED")]
        verdict = evaluate_risk_from_hypotheses(tagged, _generic_diff_context())
        assert verdict.supported_count == 1
        assert verdict.risk_level == RiskLevel.HIGH
        assert verdict.cap_applied is False

    def test_speculative_tags_only_router_cap(self):
        """EC-6: non-SUPPORTED tags do not block speculative_only cap."""
        ctx = _with_router(_generic_diff_context(), 0.80)
        tagged = [_tag("SPECULATIVE"), _tag("UNVERIFIABLE")]
        verdict = evaluate_risk_from_hypotheses(tagged, ctx)
        assert verdict.supported_count == 0
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is True
        assert verdict.cap_reason == "speculative_or_unverifiable_only"
        assert verdict.applied_rules == ["cap_to_MEDIUM:speculative_only"]

    def test_low_confidence_speculative_only_caps_high_risk(self):
        """FP scenario: router HIGH + speculative-only hypotheses → MEDIUM.

        When supported_count==0, _apply_archetype_cap fires via is_speculative_only
        before _apply_low_confidence_cap is reached. The commit is correctly capped
        to MEDIUM via 'cap_to_MEDIUM:speculative_only'. This is the realistic FP path.
        LOW confidence cap is a forward-compatible bypass for future formula tuning.
        """
        tagged = [_tag("SPECULATIVE"), _tag("UNVERIFIABLE")]
        hyps = [
            HypothesisSpec(mechanism="[logic-error] maybe", evidence_quote="", file="X.java"),
            HypothesisSpec(mechanism="[config-init] possibly", evidence_quote="", file="Y.java"),
        ]
        ctx = _generic_diff_context()
        ctx.message = None
        ctx.router_probability = 0.85
        verdict = evaluate_risk_from_hypotheses(tagged, ctx, hyps)
        assert verdict.supported_count == 0
        assert verdict.risk_level == RiskLevel.MEDIUM
        assert verdict.cap_applied is True
        assert "cap_to_MEDIUM:speculative_only" in verdict.applied_rules

    def test_low_confidence_bypassed_by_defect_signals(self):
        """EC-8: defect signals bypass LOW confidence cap."""
        ctx = _guard_removal_context()
        ctx.message = None
        ctx.router_probability = None
        verdict = evaluate_risk_from_hypotheses([], ctx, [])
        assert verdict.risk_level == RiskLevel.HIGH
        assert verdict.cap_applied is False
        assert verdict.confidence_score >= 0.0
