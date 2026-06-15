"""Integration tests for confidence-gated follow-up in orchestrator."""

from commit_investigator.context.context_builder import InvestigationContext
from commit_investigator.hypothesis.hypothesis_engine import HypothesisResponse, HypothesisSpec
from commit_investigator.pipeline.orchestrator import AgentOrchestrator, FollowUpMode


def _generic_context(diff: str | None = None) -> InvestigationContext:
    return InvestigationContext(
        commit_id="aabbccdd1234",
        project="camel",
        message="Refactor service layer",
        diff=diff or "diff --git a/ServiceImpl.java b/ServiceImpl.java\n+    return process(value);\n",
        touched_files=["ServiceImpl.java"],
        csv_features={},
        file_histories={},
        author_stats=None,
    )


def _guard_removal_context() -> InvestigationContext:
    ctx = _generic_context(
        diff=(
            "diff --git a/pom.xml b/pom.xml\n"
            "-    <version>8.2.0</version>\n"
            "+    <version>9.4.0</version>\n"
            "-        if (value != null) {\n"
            "-            return value;\n"
            "-        }\n"
        ),
    )
    ctx.message = "CAMEL-11268 Upgrade"
    return ctx


class TestConfidenceFollowUpIntegration:
    def test_medium_confidence_triggers_follow_up(self):
        orchestrator = AgentOrchestrator(max_turns=3)
        context = _generic_context()
        hyp_response = HypothesisResponse(
            summary="Possible null handling change",
            hypotheses=[
                HypothesisSpec(
                    mechanism="[logic-error] Observable: NPE. Root change: + return process(value);",
                    evidence_quote="+    return process(value);",
                    file="ServiceImpl.java",
                ),
            ],
        )
        assert orchestrator._should_follow_up(hyp_response, context, turn=1) is True

    def test_t1_gate_still_fires_with_defect_signals_and_no_supported(self):
        orchestrator = AgentOrchestrator(max_turns=3)
        context = _guard_removal_context()
        hyp_response = HypothesisResponse(
            summary="Version bump with guard removal",
            hypotheses=[
                HypothesisSpec(
                    mechanism="[logic-error] Observable: NPE.",
                    evidence_quote="",
                    file="pom.xml",
                ),
            ],
        )
        assert orchestrator._should_follow_up(hyp_response, context, turn=1) is True

    def test_max_turns_short_circuit(self):
        orchestrator = AgentOrchestrator(max_turns=2)
        context = _guard_removal_context()
        hyp_response = HypothesisResponse(summary="x", hypotheses=[])
        assert orchestrator._should_follow_up(hyp_response, context, turn=2) is False

    def test_always_mode_unchanged(self):
        orchestrator = AgentOrchestrator(max_turns=3, follow_up_mode=FollowUpMode.ALWAYS)
        context = _generic_context()
        hyp_response = HypothesisResponse(summary="x", hypotheses=[])
        assert orchestrator._should_follow_up(hyp_response, context, turn=1) is True
