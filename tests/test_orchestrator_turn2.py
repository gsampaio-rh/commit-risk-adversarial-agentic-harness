"""Tests for orchestrator multi-turn turn-2 injection."""

from __future__ import annotations

import json

import pytest

from commit_investigator.context_builder import InvestigationContext
from commit_investigator.llm import LLMProvider, LLMResponse
from commit_investigator.orchestrator import AgentOrchestrator, FollowUpMode
from commit_investigator.smart_diff import AssembledDiff


class _FakeGitProvider:
    def get_file_at_commit(self, commit_id: str, path: str) -> str | None:
        return f"content of {path}"

    def get_blame_snippet(self, commit_id, path, line_start, line_end, context_lines=2):
        return f"blame {path}:{line_start}"


class _TwoTurnLLM(LLMProvider):
    """Returns valid hypothesis JSON on each turn."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "two-turn-test"

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=4096):
        self.calls += 1
        turn_idx = self.calls - 1
        summary = f"turn-{self.calls}-summary"
        return LLMResponse(
            content=json.dumps({"summary": summary, "hypotheses": []}),
            tool_calls=[],
            tokens_used=20,
            estimated_cost=0.001,
            model=self.model_name,
            finish_reason="stop",
        )


def _truncated_context() -> InvestigationContext:
    return InvestigationContext(
        commit_id="572f3cee35feabc",
        project="camel",
        diff="diff --git a/A.java b/A.java\n+++ b/A.java\n@@ -1 +1,2 @@\n+line\n",
        message="fix",
        touched_files=["A.java"],
        csv_features={},
        file_histories={},
        author_stats=None,
        truncation_metadata=AssembledDiff(
            text="x",
            included_files=["A.java"],
            truncated_files=["Missing.java"],
            total_chars=10,
        ),
    )


class TestOrchestratorTurn2:
    def test_always_mode_runs_two_turns(self, tmp_path):
        orchestrator = AgentOrchestrator(
            llm_provider=_TwoTurnLLM(),
            max_turns=2,
            follow_up_mode=FollowUpMode.ALWAYS,
            checkpoint_dir=tmp_path / "cp",
        )
        report = orchestrator.investigate(
            commit_id="572f3cee35fe",
            project="camel",
            context=_truncated_context(),
            git_provider=_FakeGitProvider(),  # type: ignore[arg-type]
        )
        assert report.turn_count == 2
        assert report.reasoning_summary == "turn-2-summary"

    def test_turn2_injection_in_checkpoint(self, tmp_path):
        cp_dir = tmp_path / "checkpoints"
        orchestrator = AgentOrchestrator(
            llm_provider=_TwoTurnLLM(),
            max_turns=2,
            follow_up_mode=FollowUpMode.ALWAYS,
            checkpoint_dir=cp_dir,
        )
        orchestrator.investigate(
            commit_id="572f3cee35fe",
            project="camel",
            context=_truncated_context(),
            git_provider=_FakeGitProvider(),  # type: ignore[arg-type]
        )
        turn1 = json.loads((cp_dir / "turn_1.json").read_text())
        assert "turn2_injection" in turn1
        assert "## Truncated Files" in turn1["turn2_injection"]
        assert "Continue the investigation" not in turn1["turn2_injection"]

    def test_turn2_metadata_on_report(self, tmp_path):
        orchestrator = AgentOrchestrator(
            llm_provider=_TwoTurnLLM(),
            max_turns=2,
            follow_up_mode=FollowUpMode.ALWAYS,
            checkpoint_dir=tmp_path / "cp",
        )
        report = orchestrator.investigate(
            commit_id="572f3cee35fe",
            project="camel",
            context=_truncated_context(),
            git_provider=_FakeGitProvider(),  # type: ignore[arg-type]
        )
        assert "turn2_injection" in report.metadata
        assert report.metadata["total_cost_usd"] >= 0
