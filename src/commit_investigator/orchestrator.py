"""Agent orchestrator: bounded multi-turn investigation loop.

The orchestrator owns turn limits, tool dispatch, budget tracking,
checkpoint persistence, and report assembly. The LLM performs reasoning
over assembled context inside each turn.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from commit_investigator.context_builder import CommitContextBuilder, InvestigationContext
from commit_investigator.git_context import GitContextProvider
from commit_investigator.llm import LLMMessage, LLMProvider, LLMResponse, get_provider
from commit_investigator.report import (
    CommitInvestigationReport,
    EvidenceItem,
    EvidenceType,
    LocalizationClaim,
    Recommendation,
    RecommendationPriority,
    RiskAssessment,
    RiskLevel,
)
from commit_investigator.tools import ToolRegistry, build_default_registry


@dataclass
class BudgetState:
    """Tracks token usage and cost across turns."""

    total_tokens: int = 0
    total_cost: float = 0.0
    max_tokens: int = 50000
    max_cost: float = 0.50
    turns_used: int = 0

    @property
    def budget_exceeded(self) -> bool:
        return self.total_tokens >= self.max_tokens or self.total_cost >= self.max_cost

    def record(self, response: LLMResponse) -> None:
        self.total_tokens += response.tokens_used
        self.total_cost += response.estimated_cost
        self.turns_used += 1


@dataclass
class TurnCheckpoint:
    """Persisted state for a single investigation turn."""

    turn: int
    timestamp: float
    messages_sent: int
    tool_calls_made: list[str]
    tokens_used: int
    cost: float
    follow_up_needed: bool


class AgentOrchestrator:
    """Bounded multi-turn investigative agent.

    Orchestrates: context assembly → LLM reasoning → tool dispatch → report.
    Hard cap on turns prevents unbounded loops.
    """

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        max_turns: int = 3,
        max_tokens: int = 50000,
        max_cost: float = 0.50,
        checkpoint_dir: str | Path | None = None,
    ) -> None:
        self._llm = llm_provider or get_provider()
        self._max_turns = max_turns
        self._budget = BudgetState(max_tokens=max_tokens, max_cost=max_cost)
        self._checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self._checkpoints: list[TurnCheckpoint] = []

    def investigate(
        self,
        commit_id: str,
        project: str,
        csv_row: dict[str, Any] | None = None,
        git_provider: GitContextProvider | None = None,
        context: InvestigationContext | None = None,
    ) -> CommitInvestigationReport:
        """Run a bounded multi-turn investigation on a commit.

        Returns a schema-validated CommitInvestigationReport.
        """
        self._budget = BudgetState(max_tokens=self._budget.max_tokens, max_cost=self._budget.max_cost)
        self._checkpoints = []

        if git_provider is None and context is None:
            raise ValueError("Either git_provider or pre-built context required")

        if context is None:
            builder = CommitContextBuilder(git_provider)  # type: ignore[arg-type]
            context = builder.build(commit_id, project, csv_row)

        tools = self._build_tools(git_provider, context)
        messages = self._build_initial_messages(context)
        tools_used: list[str] = []
        all_tool_calls: list[str] = []

        for turn in range(1, self._max_turns + 1):
            if self._budget.budget_exceeded:
                break

            response = self._llm.complete(
                messages=messages,
                tools=tools.to_openai_tools() if tools else None,
                temperature=0.0,
            )
            self._budget.record(response)

            if response.tool_calls:
                for tc in response.tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc.get("arguments", {})
                    result = tools.execute(tool_name, **tool_args)
                    all_tool_calls.append(tool_name)

                    messages.append(LLMMessage(role="assistant", content=f"[Tool call: {tool_name}]"))
                    messages.append(LLMMessage(role="tool", content=result, name=tool_name))

                tools_used.extend(tc["name"] for tc in response.tool_calls)

            follow_up_needed = self._should_follow_up(response, turn)

            self._save_checkpoint(TurnCheckpoint(
                turn=turn,
                timestamp=time.time(),
                messages_sent=len(messages),
                tool_calls_made=all_tool_calls.copy(),
                tokens_used=self._budget.total_tokens,
                cost=self._budget.total_cost,
                follow_up_needed=follow_up_needed,
            ))

            if not follow_up_needed:
                break

            messages.append(LLMMessage(
                role="user",
                content="Continue the investigation. Focus on areas of uncertainty.",
            ))

        return self._assemble_report(
            context=context,
            last_response=response,
            tools_used=list(set(tools_used + all_tool_calls)),
            turns=self._budget.turns_used,
        )

    def _build_tools(
        self,
        git_provider: GitContextProvider | None,
        context: InvestigationContext,
    ) -> ToolRegistry:
        """Build tool registry if git provider is available."""
        if git_provider is None:
            return ToolRegistry()
        return build_default_registry(git_provider, context)

    def _build_initial_messages(self, context: InvestigationContext) -> list[LLMMessage]:
        """Construct the initial prompt with investigation context."""
        system_prompt = (
            "You are a commit risk investigator. Analyze the provided commit context "
            "and produce a risk assessment with evidence. Be specific: cite file paths, "
            "diff hunks, and metrics. If you need more information, use the available tools.\n\n"
            "IMPORTANT: Respond ONLY with a single JSON object (no markdown, no explanation "
            "outside the JSON). The JSON must contain these fields:\n"
            "- risk_level: one of LOW, MEDIUM, HIGH, CRITICAL\n"
            "- confidence: float 0.0 to 1.0\n"
            "- reasoning: string explaining your assessment\n"
            "- findings: list of strings with specific observations\n"
            "- follow_up_needed: boolean\n"
            "- localization: list of {file, lines, rationale} objects\n"
            "- recommendations: list of {action, priority, rationale} objects"
        )

        context_parts = [f"## Commit: {context.commit_id}\n## Project: {context.project}\n"]

        if context.message:
            context_parts.append(f"## Commit Message\n{context.message.strip()}\n")

        if context.diff:
            diff_preview = context.diff[:4000]
            if len(context.diff) > 4000:
                diff_preview += f"\n... (truncated, {len(context.diff)} chars total)"
            context_parts.append(f"## Diff\n```\n{diff_preview}\n```\n")

        if context.touched_files:
            context_parts.append(f"## Touched Files\n" + "\n".join(f"- {f}" for f in context.touched_files))

        if context.csv_features:
            feat_str = ", ".join(f"{k}={v}" for k, v in sorted(context.csv_features.items()))
            context_parts.append(f"\n## Numeric Features\n{feat_str}")

        if context.missing_reasons:
            context_parts.append(f"\n## Missing Context\n" + "\n".join(f"- {r}" for r in context.missing_reasons))

        user_content = "\n".join(context_parts)

        return [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_content),
        ]

    def _should_follow_up(self, response: LLMResponse, turn: int) -> bool:
        """Determine if another turn is needed based on LLM response."""
        if turn >= self._max_turns:
            return False
        if self._budget.budget_exceeded:
            return False

        try:
            parsed = json.loads(response.content)
            return parsed.get("follow_up_needed", False)
        except (json.JSONDecodeError, TypeError):
            return False

    def _assemble_report(
        self,
        context: InvestigationContext,
        last_response: LLMResponse,
        tools_used: list[str],
        turns: int,
    ) -> CommitInvestigationReport:
        """Parse LLM output into a validated CommitInvestigationReport."""
        parsed = _extract_json(last_response.content)

        risk_level_str = parsed.get("risk_level", "MEDIUM")
        try:
            risk_level = RiskLevel(risk_level_str)
        except ValueError:
            risk_level = RiskLevel.MEDIUM

        confidence = parsed.get("confidence", 0.5)
        confidence = max(0.0, min(1.0, float(confidence)))

        evidence_items = [
            EvidenceItem(
                type=EvidenceType.DIFF_HUNK if context.diff else EvidenceType.NUMERIC_FEATURE,
                source=context.commit_id,
                content=(context.diff[:500] if context.diff else "Numeric features only"),
                relevance="Primary investigation context",
            )
        ]

        localization = []
        for loc in parsed.get("localization", []):
            if isinstance(loc, dict) and "file" in loc:
                localization.append(LocalizationClaim(
                    file=loc["file"],
                    lines=_parse_lines(loc.get("lines")),
                    rationale=loc.get("rationale", "Identified during investigation"),
                ))

        recommendations = []
        for rec in parsed.get("recommendations", []):
            if isinstance(rec, dict) and "action" in rec:
                try:
                    priority = RecommendationPriority(rec.get("priority", "MEDIUM"))
                except ValueError:
                    priority = RecommendationPriority.MEDIUM
                recommendations.append(Recommendation(
                    action=rec["action"],
                    priority=priority,
                    rationale=rec.get("rationale", ""),
                ))

        return CommitInvestigationReport(
            commit_id=context.commit_id,
            project=context.project,
            risk_assessment=RiskAssessment(level=risk_level, confidence=confidence),
            evidence=evidence_items,
            findings=parsed.get("findings", ["Investigation completed"]),
            localization=localization,
            reasoning_summary=parsed.get("reasoning", "Investigation completed."),
            recommendations=recommendations,
            tools_used=tools_used,
            turn_count=turns,
            metadata={
                "model": last_response.model,
                "total_tokens": self._budget.total_tokens,
                "total_cost": self._budget.total_cost,
                "budget_exceeded": self._budget.budget_exceeded,
                "missing_reasons": list(context.missing_reasons),
            },
        )

    def _save_checkpoint(self, checkpoint: TurnCheckpoint) -> None:
        """Persist turn checkpoint to disk if configured."""
        self._checkpoints.append(checkpoint)
        if self._checkpoint_dir:
            self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
            path = self._checkpoint_dir / f"turn_{checkpoint.turn}.json"
            path.write_text(json.dumps({
                "turn": checkpoint.turn,
                "timestamp": checkpoint.timestamp,
                "messages_sent": checkpoint.messages_sent,
                "tool_calls_made": checkpoint.tool_calls_made,
                "tokens_used": checkpoint.tokens_used,
                "cost": checkpoint.cost,
                "follow_up_needed": checkpoint.follow_up_needed,
            }, indent=2))


def _parse_lines(raw: Any) -> tuple[int, int] | None:
    """Parse a line range from various LLM output formats.

    Handles: [1, 10], "1-10", "370-377", [1], None.
    """
    if raw is None:
        return None

    if isinstance(raw, (list, tuple)):
        nums = [int(x) for x in raw if str(x).strip().isdigit()]
        if len(nums) >= 2:
            return (nums[0], nums[1])
        if len(nums) == 1:
            return (nums[0], nums[0])
        return None

    if isinstance(raw, str):
        raw = raw.strip()
        if "-" in raw:
            parts = raw.split("-", 1)
            try:
                return (int(parts[0].strip()), int(parts[1].strip()))
            except ValueError:
                return None
        try:
            n = int(raw)
            return (n, n)
        except ValueError:
            return None

    return None


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from LLM output, tolerating markdown fences."""
    if not text:
        return {}

    text = text.strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    match = _JSON_FENCE_RE.search(text)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start : brace_end + 1])
        except (json.JSONDecodeError, TypeError):
            pass

    return {}
