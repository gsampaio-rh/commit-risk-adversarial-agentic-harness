"""V4.1 Scoped Investigation — V4 retrieval + V3-style examination tools scoped to CandidateSet."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from commit_investigator.agent.tools import build_scoped_tools
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.harness.scoped_prompts import (
    build_scoped_system_prompt,
    parse_suspects,
    parse_tool_calls,
)
from commit_investigator.harness.trace_writer import TraceWriter, build_scoped_trace
from commit_investigator.harness.v4_runner import V4InvestigationResult
from commit_investigator.infra.git_context import GitContextProvider, TemporalBoundViolation
from commit_investigator.infra.llm import LLMMessage, LLMProvider
from commit_investigator.models.candidates import CandidateSet
from commit_investigator.retrieval import compute_recall_at_k, prepare_investigation


@dataclass
class ToolCallRecord:
    tool: str
    args: dict[str, Any]
    result_preview: str = ""
    latency_ms: float = 0.0


@dataclass
class ScopedInvestigationResult:
    suspects: list[dict[str, Any]] = field(default_factory=list)
    tool_trace: list[ToolCallRecord] = field(default_factory=list)
    reasoning_summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    diff_examined: bool = False


class ScopedInvestigator:
    """V4.1 bug attribution: scoped tools + multi-turn loop."""

    def __init__(
        self,
        llm: LLMProvider,
        problem: ProblemStatement,
        candidate_set: CandidateSet,
        git: GitContextProvider,
        *,
        max_tool_calls: int = 15,
        max_turns: int = 8,
    ) -> None:
        self._llm = llm
        self._problem = problem
        self._candidates = candidate_set
        self._max_tool_calls = max_tool_calls
        self._max_turns = max_turns
        self._registry = build_scoped_tools(git, candidate_set)

    def investigate(self) -> ScopedInvestigationResult:
        trace: list[ToolCallRecord] = []
        calls_used = 0
        diff_examined = False
        texts: list[str] = []
        start = time.time()
        messages = [
            LLMMessage(role="system", content=build_scoped_system_prompt(
                self._problem, self._candidates, self._registry,
            )),
            LLMMessage(role="user", content="Begin investigation. Examine the top-ranked candidates."),
        ]

        for _ in range(self._max_turns):
            resp = self._llm.complete(messages, max_tokens=4096)
            texts.append(resp.content)

            suspects = parse_suspects(resp.content)
            if suspects and diff_examined:
                return self._finish(suspects, trace, texts, start, diff_examined)

            parsed = parse_tool_calls(resp.content)
            if not parsed:
                if diff_examined:
                    return self._finish(suspects or [], trace, texts, start, diff_examined)
                messages.append(LLMMessage(role="assistant", content=resp.content))
                messages.append(LLMMessage(role="user", content="Use ```tool to examine candidates."))
                continue

            parts: list[str] = []
            for call in parsed:
                if calls_used >= self._max_tool_calls:
                    parts.append(f"**{call['tool']}**: Budget exhausted.")
                    break
                t0 = time.time()
                try:
                    result = self._registry.execute(call["tool"], **call.get("args", {}))
                except TemporalBoundViolation as exc:
                    result = f"Error: {exc}"
                if call["tool"] == "get_commit_diff" and not result.startswith("Error"):
                    diff_examined = True
                calls_used += 1
                trace.append(ToolCallRecord(
                    call["tool"], call.get("args", {}), result[:500], (time.time() - t0) * 1000,
                ))
                parts.append(f"**{call['tool']}**:\n{result}")

            hint = (
                "Conclude NOW with ```suspects."
                if calls_used >= self._max_tool_calls - 2
                else "Continue or conclude with ```suspects."
            )
            messages.append(LLMMessage(role="assistant", content=resp.content))
            messages.append(LLMMessage(
                role="user",
                content=f"Results ({calls_used}/{self._max_tool_calls} calls):\n\n"
                        + "\n\n".join(parts) + f"\n\n{hint}",
            ))

        return self._finish([], trace, texts, start, diff_examined)

    def _finish(
        self,
        suspects: list[dict[str, Any]],
        trace: list[ToolCallRecord],
        texts: list[str],
        start: float,
        diff_examined: bool,
    ) -> ScopedInvestigationResult:
        return ScopedInvestigationResult(
            suspects=suspects,
            tool_trace=trace,
            reasoning_summary="\n\n".join(texts)[:2000],
            diff_examined=diff_examined,
            metadata={
                "tool_calls": len(trace),
                "elapsed_ms": round((time.time() - start) * 1000, 1),
                "model": self._llm.model_name,
                "candidates_total": len(self._candidates.commits),
            },
        )


def run_scoped_investigation(
    title: str,
    description: str,
    project: str,
    issue_key: str,
    repo_path: str | Path,
    temporal_bound: str,
    ground_truth_sha: str,
    llm: LLMProvider,
    *,
    traces_dir: str | Path = "results/traces",
) -> V4InvestigationResult:
    """Run V4.1 scoped investigation: retrieval + scoped agent."""
    result = V4InvestigationResult(issue_key=issue_key)
    retrieval_start = time.time()

    try:
        retrieval = prepare_investigation(
            source=(title, description),
            repo_path=repo_path,
            temporal_bound=temporal_bound,
            project=project,
            issue_key=issue_key,
        )
    except Exception as exc:
        result.error = f"Retrieval failed: {exc}"
        return result

    retrieval_ms = (time.time() - retrieval_start) * 1000
    recall = compute_recall_at_k(retrieval.candidate_set, ground_truth_sha, k=100)
    result.retrieval_recall = recall.found

    git = GitContextProvider(str(repo_path), temporal_bound)
    inv = ScopedInvestigator(
        llm=llm,
        problem=retrieval.problem_statement,
        candidate_set=retrieval.candidate_set,
        git=git,
    )
    inv_result = inv.investigate()
    result.suspects = inv_result.suspects

    trace = build_scoped_trace(
        issue_key=issue_key,
        temporal_bound=temporal_bound,
        suspects=inv_result.suspects,
        tool_trace=[{"tool": tc.tool, "args": tc.args} for tc in inv_result.tool_trace],
        candidate_count=len(retrieval.candidate_set.commits),
        recall_found=recall.found,
        retrieval_ms=retrieval_ms,
        agent_ms=inv_result.metadata.get("elapsed_ms", 0.0),
    )
    TraceWriter(traces_dir).write(trace)
    result.trace = trace
    return result
