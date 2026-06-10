"""AC-5: 12-commit regression panel — risk cap must match iter-2 baseline.

Pre-extraction: uses orchestrator._apply_clean_commit_risk_cap().
Post-extraction: migrates to risk_policy.evaluate_risk().
"""

import pytest

from commit_investigator.context_builder import InvestigationContext
from commit_investigator.orchestrator import _apply_clean_commit_risk_cap
from commit_investigator.report import RiskLevel


def _version_bump_ctx(commit_id: str) -> InvestigationContext:
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


PANEL_CASES = [
    pytest.param(
        "24d9de042248", RiskLevel.MEDIUM, _production_change_ctx("24d9de042248"),
        "STAGE 4 — VERDICT: Rubric MEDIUM. No SUPPORTED hypothesis. Speculative cross-version impact.",
        RiskLevel.MEDIUM, id="24d9de042248-medium-passthrough",
    ),
    pytest.param(
        "9530370f7642", RiskLevel.MEDIUM, _version_bump_ctx("9530370f7642"),
        "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): version incompatibility. STAGE 4 — VERDICT: Clean-commit discrimination applies. No SUPPORTED hypothesis. SPECULATIVE/UNVERIFIABLE-only → MEDIUM.",
        RiskLevel.MEDIUM, id="9530370f7642-medium-passthrough",
    ),
    pytest.param(
        "b9f1653151e2", RiskLevel.MEDIUM, _version_bump_ctx("b9f1653151e2"),
        "STAGE 3: HYPOTHESIS 1 (SPECULATIVE): binary incompatibility above 9.3.15. STAGE 4 — VERDICT: Discrimination applies. SPECULATIVE-only → MEDIUM.",
        RiskLevel.MEDIUM, id="b9f1653151e2-medium-passthrough",
    ),
    pytest.param(
        "294c169f6d66", RiskLevel.HIGH, _production_change_ctx("294c169f6d66"),
        "STAGE 3: HYPOTHESIS A — SUPPORTED: null guard removed at line 42. STAGE 4: Rubric HIGH, criterion (a).",
        RiskLevel.HIGH, id="294c169f6d66-high-supported",
    ),
    pytest.param(
        "4a72341ebffc", RiskLevel.HIGH, _production_change_ctx("4a72341ebffc"),
        "STAGE 3: HYPOTHESIS 1 — SUPPORTED: lifecycle ordering changed. STAGE 4: Rubric HIGH.",
        RiskLevel.HIGH, id="4a72341ebffc-high-supported",
    ),
    pytest.param(
        "55dcbe801e76", RiskLevel.HIGH, _production_change_ctx("55dcbe801e76"),
        "STAGE 3: HYPOTHESIS 1 — SUPPORTED: inverted condition in production path. STAGE 4: Rubric HIGH, criterion (a).",
        RiskLevel.HIGH, id="55dcbe801e76-high-supported",
    ),
    pytest.param(
        "90846b586c51", RiskLevel.HIGH, _production_change_ctx("90846b586c51"),
        "STAGE 3: HYPOTHESIS A — SUPPORTED: security validation removed. STAGE 4: Rubric HIGH, criterion (a).",
        RiskLevel.HIGH, id="90846b586c51-high-supported",
    ),
    pytest.param(
        "ce2d5bfa5f84", RiskLevel.HIGH, _production_change_ctx("ce2d5bfa5f84"),
        "STAGE 3: HYPOTHESIS 1 — SUPPORTED: data loss on write path. STAGE 4: Rubric HIGH, criterion (a).",
        RiskLevel.HIGH, id="ce2d5bfa5f84-high-supported",
    ),
    pytest.param(
        "f897d46870ba", RiskLevel.HIGH, _production_change_ctx("f897d46870ba"),
        "STAGE 3: HYPOTHESIS 1 — SUPPORTED: lifecycle semantics changed. STAGE 4: Rubric HIGH.",
        RiskLevel.HIGH, id="f897d46870ba-high-supported",
    ),
    pytest.param(
        "fbf0ffad627b", RiskLevel.HIGH, _guard_removal_ctx("fbf0ffad627b"),
        "STAGE 3: HYPOTHESIS 1 — SUPPORTED: startup/shutdown sequencing change. STAGE 4 — VERDICT: Lifecycle ordering change in production hunks is an explicit exclusion from clean-commit discrimination. Rubric HIGH.",
        RiskLevel.HIGH, id="fbf0ffad627b-high-lifecycle-guard",
    ),
    pytest.param(
        "572f3cee35fe", RiskLevel.LOW, _production_change_ctx("572f3cee35fe"),
        "STAGE 2: No defect hypotheses. STAGE 3: N/A. STAGE 4: Rubric LOW.",
        RiskLevel.LOW, id="572f3cee35fe-low-passthrough",
    ),
    pytest.param(
        "7cff0990283b", RiskLevel.HIGH, _method_rename_ctx("7cff0990283b"),
        "STAGE 3: HYPOTHESIS 1 — SUPPORTED: getIn-to-getOut substitution in UnwrapStreamProcessor else-branch silently drops unwrapped body for InOnly exchanges. STAGE 4 — VERDICT: Rubric HIGH, criterion (a). One SUPPORTED defect hypothesis exists.",
        RiskLevel.MEDIUM, id="7cff0990283b-high-capped-by-archetype",
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
    level, capped = _apply_clean_commit_risk_cap(llm_risk, context, reasoning)
    assert level == expected, (
        f"Regression on {commit_id}: cap → {level.value}, expected {expected.value}. capped={capped}"
    )
