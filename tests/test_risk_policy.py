"""AC-7 isolation tests for _apply_clean_commit_risk_cap() — pre-extraction.

Written against orchestrator private functions before archetype.py / risk_policy.py
exist. Post-extraction (iter-3a commit 2) these tests migrate to risk_policy.evaluate_risk().
"""

import pytest

from commit_investigator.context_builder import InvestigationContext
from commit_investigator.orchestrator import (
    _apply_clean_commit_risk_cap,
    _reasoning_all_speculative_or_unverifiable,
)
from commit_investigator.report import RiskLevel


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
# _apply_clean_commit_risk_cap — full AC-7 isolation matrix
# ---------------------------------------------------------------------------

class TestApplyCleanCommitRiskCap:
    def test_low_passthrough(self):
        level, capped = _apply_clean_commit_risk_cap(
            RiskLevel.LOW, _version_bump_context(), "STAGE 3: SPECULATIVE",
        )
        assert level == RiskLevel.LOW
        assert capped is False

    def test_medium_passthrough(self):
        level, capped = _apply_clean_commit_risk_cap(
            RiskLevel.MEDIUM, _version_bump_context(), "STAGE 3: SPECULATIVE",
        )
        assert level == RiskLevel.MEDIUM
        assert capped is False

    def test_low_passthrough_generic_context(self):
        level, capped = _apply_clean_commit_risk_cap(RiskLevel.LOW, _generic_diff_context(), "")
        assert level == RiskLevel.LOW
        assert capped is False

    def test_high_capped_version_bump_speculative_reasoning(self):
        level, capped = _apply_clean_commit_risk_cap(
            RiskLevel.HIGH,
            _version_bump_context(),
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): cross-version break.",
        )
        assert level == RiskLevel.MEDIUM
        assert capped is True

    def test_high_capped_archetype_with_criterion_b_supported(self):
        ctx = _version_bump_context()
        ctx.diff += "\n+        return future;\n"
        reasoning = (
            "STAGE 3: HYPOTHESIS A — SUPPORTED: raw QueryFactory erasure breaks callers. "
            "STAGE 4: criterion (b) HIGH."
        )
        level, capped = _apply_clean_commit_risk_cap(RiskLevel.HIGH, ctx, reasoning)
        assert level == RiskLevel.MEDIUM
        assert capped is True

    def test_critical_capped_version_bump_speculative(self):
        level, capped = _apply_clean_commit_risk_cap(
            RiskLevel.CRITICAL,
            _version_bump_context(),
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): cross-version break.",
        )
        assert level == RiskLevel.MEDIUM
        assert capped is True

    def test_critical_capped_speculative_only_non_archetype(self):
        level, capped = _apply_clean_commit_risk_cap(
            RiskLevel.CRITICAL,
            _generic_diff_context(),
            "HYPOTHESIS 1 (SPECULATIVE): external caller impact. HYPOTHESIS 2 (UNVERIFIABLE): scope unknown.",
        )
        assert level == RiskLevel.MEDIUM
        assert capped is True

    def test_high_not_capped_when_guard_removal(self):
        level, capped = _apply_clean_commit_risk_cap(
            RiskLevel.HIGH,
            _guard_removal_context(),
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): migration.",
        )
        assert level == RiskLevel.HIGH
        assert capped is False

    def test_high_not_capped_when_lifecycle_change(self):
        level, capped = _apply_clean_commit_risk_cap(
            RiskLevel.HIGH,
            _lifecycle_context(),
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): lifecycle ordering.",
        )
        assert level == RiskLevel.HIGH
        assert capped is False

    def test_high_not_capped_when_concurrency_change(self):
        level, capped = _apply_clean_commit_risk_cap(
            RiskLevel.HIGH,
            _concurrency_context(),
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): race condition.",
        )
        assert level == RiskLevel.HIGH
        assert capped is False

    def test_high_capped_speculative_only_generic_context(self):
        level, capped = _apply_clean_commit_risk_cap(
            RiskLevel.HIGH,
            _generic_diff_context(),
            "HYPOTHESIS 1 (SPECULATIVE): assumed external behavior.",
        )
        assert level == RiskLevel.MEDIUM
        assert capped is True

    def test_high_not_capped_when_supported_on_generic_diff(self):
        level, capped = _apply_clean_commit_risk_cap(
            RiskLevel.HIGH,
            _generic_diff_context(),
            "STAGE 3: HYPOTHESIS A — SUPPORTED: removed null-check at line 42.",
        )
        assert level == RiskLevel.HIGH
        assert capped is False

    def test_empty_reasoning_no_cap_on_generic_diff(self):
        level, capped = _apply_clean_commit_risk_cap(RiskLevel.HIGH, _generic_diff_context(), "")
        assert level == RiskLevel.HIGH
        assert capped is False

    def test_empty_reasoning_caps_on_archetype(self):
        level, capped = _apply_clean_commit_risk_cap(RiskLevel.HIGH, _version_bump_context(), "")
        assert level == RiskLevel.MEDIUM
        assert capped is True

    def test_null_diff_no_crash(self):
        ctx = _generic_diff_context()
        ctx.diff = None  # type: ignore[assignment]
        level, capped = _apply_clean_commit_risk_cap(RiskLevel.HIGH, ctx, "SPECULATIVE")
        assert level in (RiskLevel.HIGH, RiskLevel.MEDIUM)


# ---------------------------------------------------------------------------
# AC-6: determinism test (same inputs → same outputs ×1000)
# ---------------------------------------------------------------------------

class TestApplyCleanCommitRiskCapDeterminism:
    def test_deterministic_1000x_archetype_cap(self):
        ctx = _version_bump_context()
        reasoning = "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): cross-version break."
        first = _apply_clean_commit_risk_cap(RiskLevel.HIGH, ctx, reasoning)
        for _ in range(999):
            assert _apply_clean_commit_risk_cap(RiskLevel.HIGH, ctx, reasoning) == first

    def test_deterministic_1000x_no_cap_supported(self):
        ctx = _generic_diff_context()
        reasoning = "HYPOTHESIS A — SUPPORTED: removed null-check at line 42."
        first = _apply_clean_commit_risk_cap(RiskLevel.HIGH, ctx, reasoning)
        for _ in range(999):
            assert _apply_clean_commit_risk_cap(RiskLevel.HIGH, ctx, reasoning) == first

    def test_deterministic_1000x_speculative_global_cap(self):
        ctx = _generic_diff_context()
        reasoning = "HYPOTHESIS 1 (SPECULATIVE): assumed external behavior."
        first = _apply_clean_commit_risk_cap(RiskLevel.CRITICAL, ctx, reasoning)
        for _ in range(999):
            assert _apply_clean_commit_risk_cap(RiskLevel.CRITICAL, ctx, reasoning) == first
