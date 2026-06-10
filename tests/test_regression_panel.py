"""AC-5: 12-commit regression panel — evaluate_risk() must produce identical risk_level.

Panel baseline: output/runs/2026-06-10_21-07-21_real_n12 (iter-2, commit ed5bfe6).

Each fixture uses:
- llm_risk: the LLM's raw risk_level verdict (reconstructed from panel; if cap was applied,
  the LLM must have said HIGH since only HIGH/CRITICAL are ever capped)
- context: InvestigationContext with minimal diff signals sufficient to reproduce the
  same cap/no-cap decision
- reasoning_snippet: key STAGE 3–4 excerpt from the actual panel reasoning
- expected_risk: the final risk_level recorded in the panel output

The test verifies that evaluate_risk() still produces the same final risk for every commit.
If any of these fail, it means a cap logic regression was introduced during extraction.
"""

import pytest

from commit_investigator.context_builder import InvestigationContext
from commit_investigator.report import RiskLevel
from commit_investigator.risk_policy import evaluate_risk


# ---------------------------------------------------------------------------
# Minimal context factories for cap decision reproduction
# ---------------------------------------------------------------------------

def _version_bump_ctx(commit_id: str) -> InvestigationContext:
    """Version bump archetype context — triggers clean-commit cap."""
    return InvestigationContext(
        commit_id=commit_id,
        project="camel",
        message=f"Upgrade dependency ({commit_id[:8]})",
        diff=(
            "diff --git a/pom.xml b/pom.xml\n"
            "-    <version>8.2.0</version>\n"
            "+    <version>9.4.0</version>\n"
            "-import org.old.Future;\n"
            "+import java.util.concurrent.CompletableFuture;\n"
        ),
        touched_files=["pom.xml", "Adapter.java"],
        csv_features={},
        file_histories={},
        author_stats=None,
    )


def _production_change_ctx(commit_id: str, diff_extra: str = "") -> InvestigationContext:
    """Production code change context — no archetype, no defect signals → no cap on SUPPORTED."""
    return InvestigationContext(
        commit_id=commit_id,
        project="camel",
        message=f"Production fix ({commit_id[:8]})",
        diff="diff --git a/Main.java b/Main.java\n" + diff_extra + "+    return result;\n",
        touched_files=["Main.java"],
        csv_features={},
        file_histories={},
        author_stats=None,
    )


def _guard_removal_ctx(commit_id: str) -> InvestigationContext:
    """Context with guard removal — opts out of cap even inside archetype patterns."""
    return InvestigationContext(
        commit_id=commit_id,
        project="camel",
        message=f"Fix lifecycle ({commit_id[:8]})",
        diff=(
            "-        if (started) {\n"
            "-            SmartLifecycle.stop();\n"
            "-        }\n"
            "+        // removed startup guard\n"
        ),
        touched_files=["RoutesCollector.java"],
        csv_features={},
        file_histories={},
        author_stats=None,
    )


def _method_rename_ctx(commit_id: str) -> InvestigationContext:
    """Method rename context — matches clean archetype (method rename without defect signals)."""
    return InvestigationContext(
        commit_id=commit_id,
        project="camel",
        message=f"Rename API method ({commit_id[:8]})",
        diff=(
            "-    public void process(Exchange exchange) {\n"
            "+    public void sendCamelExchange(Exchange exchange) {\n"
        ),
        touched_files=["UnwrapStreamProcessor.java"],
        csv_features={},
        file_histories={},
        author_stats=None,
    )


# ---------------------------------------------------------------------------
# Panel cases
# Columns: commit_id, llm_risk, context, reasoning_snippet, expected_final_risk
# ---------------------------------------------------------------------------

PANEL_CASES = [
    # ── MEDIUM finals (no cap) ──
    pytest.param(
        "24d9de042248",
        RiskLevel.MEDIUM,
        _production_change_ctx("24d9de042248"),
        "STAGE 4 — VERDICT: Rubric MEDIUM. No SUPPORTED hypothesis. Speculative cross-version impact.",
        RiskLevel.MEDIUM,
        id="24d9de042248-medium-passthrough",
    ),
    pytest.param(
        "9530370f7642",
        RiskLevel.MEDIUM,
        _version_bump_ctx("9530370f7642"),
        (
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): version incompatibility. "
            "HYPOTHESIS 2 — REFUTED: no guard removed. "
            "STAGE 4 — VERDICT: Clean-commit discrimination applies. "
            "No SUPPORTED hypothesis. SPECULATIVE/UNVERIFIABLE-only → MEDIUM."
        ),
        RiskLevel.MEDIUM,
        id="9530370f7642-medium-passthrough",
    ),
    pytest.param(
        "b9f1653151e2",
        RiskLevel.MEDIUM,
        _version_bump_ctx("b9f1653151e2"),
        (
            "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): binary incompatibility above 9.3.15. "
            "STAGE 4 — VERDICT: Discrimination applies. SPECULATIVE-only → MEDIUM."
        ),
        RiskLevel.MEDIUM,
        id="b9f1653151e2-medium-passthrough",
    ),
    # ── HIGH finals (no cap) ──
    pytest.param(
        "294c169f6d66",
        RiskLevel.HIGH,
        _production_change_ctx("294c169f6d66"),
        "STAGE 3: HYPOTHESIS A — SUPPORTED: null guard removed at line 42. STAGE 4: Rubric HIGH, criterion (a).",
        RiskLevel.HIGH,
        id="294c169f6d66-high-supported",
    ),
    pytest.param(
        "4a72341ebffc",
        RiskLevel.HIGH,
        _production_change_ctx("4a72341ebffc"),
        "STAGE 3: HYPOTHESIS 1 — SUPPORTED: lifecycle ordering changed. STAGE 4: Rubric HIGH.",
        RiskLevel.HIGH,
        id="4a72341ebffc-high-supported",
    ),
    pytest.param(
        "55dcbe801e76",
        RiskLevel.HIGH,
        _production_change_ctx("55dcbe801e76"),
        "STAGE 3: HYPOTHESIS 1 — SUPPORTED: inverted condition in production path. STAGE 4: Rubric HIGH, criterion (a).",
        RiskLevel.HIGH,
        id="55dcbe801e76-high-supported",
    ),
    pytest.param(
        "90846b586c51",
        RiskLevel.HIGH,
        _production_change_ctx("90846b586c51"),
        "STAGE 3: HYPOTHESIS A — SUPPORTED: security validation removed. STAGE 4: Rubric HIGH, criterion (a).",
        RiskLevel.HIGH,
        id="90846b586c51-high-supported",
    ),
    pytest.param(
        "ce2d5bfa5f84",
        RiskLevel.HIGH,
        _production_change_ctx("ce2d5bfa5f84"),
        "STAGE 3: HYPOTHESIS 1 — SUPPORTED: data loss on write path. STAGE 4: Rubric HIGH, criterion (a).",
        RiskLevel.HIGH,
        id="ce2d5bfa5f84-high-supported",
    ),
    pytest.param(
        "f897d46870ba",
        RiskLevel.HIGH,
        _production_change_ctx("f897d46870ba"),
        "STAGE 3: HYPOTHESIS 1 — SUPPORTED: lifecycle semantics changed. STAGE 4: Rubric HIGH.",
        RiskLevel.HIGH,
        id="f897d46870ba-high-supported",
    ),
    pytest.param(
        "fbf0ffad627b",
        RiskLevel.HIGH,
        _guard_removal_ctx("fbf0ffad627b"),
        (
            "STAGE 3: HYPOTHESIS 1 — SUPPORTED: startup/shutdown sequencing change. "
            "STAGE 4 — VERDICT: Lifecycle ordering change in production hunks is an explicit "
            "exclusion from clean-commit discrimination. Rubric HIGH."
        ),
        RiskLevel.HIGH,
        id="fbf0ffad627b-high-lifecycle-guard",
    ),
    # ── LOW final ──
    pytest.param(
        "572f3cee35fe",
        RiskLevel.LOW,
        _production_change_ctx("572f3cee35fe"),
        "STAGE 2: No defect hypotheses. STAGE 3: N/A. STAGE 4: Rubric LOW.",
        RiskLevel.LOW,
        id="572f3cee35fe-low-passthrough",
    ),
    # ── HIGH → MEDIUM via clean archetype cap (7cff0990283b) ──
    # LLM verdict: HIGH criterion (a) with SUPPORTED hypothesis.
    # Cap applied because commit matches method-rename archetype
    # (process→sendCamelExchange rename, no guard removal in diff).
    pytest.param(
        "7cff0990283b",
        RiskLevel.HIGH,
        _method_rename_ctx("7cff0990283b"),
        (
            "STAGE 3: HYPOTHESIS 1 — SUPPORTED: getIn-to-getOut substitution in "
            "UnwrapStreamProcessor else-branch silently drops unwrapped body for InOnly exchanges. "
            "STAGE 4 — VERDICT: Rubric HIGH, criterion (a). One SUPPORTED defect hypothesis exists."
        ),
        RiskLevel.MEDIUM,
        id="7cff0990283b-high-capped-by-archetype",
    ),
]


@pytest.mark.parametrize("commit_id,llm_risk,context,reasoning,expected", PANEL_CASES)
def test_panel_risk_level_matches_iter2_baseline(
    commit_id: str,
    llm_risk: RiskLevel,
    context: InvestigationContext,
    reasoning: str,
    expected: RiskLevel,
) -> None:
    """evaluate_risk() must produce the same final risk_level as the iter-2 panel."""
    verdict = evaluate_risk(llm_risk, context, reasoning)
    assert verdict.risk_level == expected, (
        f"Regression on {commit_id}: "
        f"evaluate_risk({llm_risk.value}, ...) → {verdict.risk_level.value}, "
        f"expected {expected.value} (iter-2 baseline). "
        f"cap_applied={verdict.cap_applied}, cap_reason={verdict.cap_reason!r}"
    )
