"""Agent orchestrator: bounded multi-turn investigation loop.

The orchestrator owns turn limits, tool dispatch, budget tracking,
checkpoint persistence, and report assembly. The LLM performs reasoning
over assembled context inside each turn.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from commit_investigator.archetype import detect_archetype, has_production_defect_signals
from commit_investigator.context_builder import CommitContextBuilder, InvestigationContext
from commit_investigator.git_context import GitContextProvider
from commit_investigator.llm import LLMMessage, LLMProvider, LLMResponse, get_provider
from commit_investigator.prompts import INVESTIGATION_SYSTEM_PROMPT
from commit_investigator.quality_gate import HypothesisArtifact, evaluate_gate
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
from commit_investigator.response_parser import coerce_text_field, extract_json, normalize_findings, parse_lines
from commit_investigator.risk_policy import evaluate_risk
from commit_investigator.tools import ToolRegistry, build_default_registry

# Re-export for backward compatibility with existing imports
__all__ = ["INVESTIGATION_SYSTEM_PROMPT", "AgentOrchestrator", "InvalidInvestigationResponseError",
           "BudgetState", "TurnCheckpoint", "DEFAULT_MAX_DIFF_CHARS"]


class InvalidInvestigationResponseError(ValueError):
    """Raised when LLM output is empty, unparseable, or missing required fields."""


DEFAULT_MAX_DIFF_CHARS = 16_000


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
        max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS,
    ) -> None:
        self._llm = llm_provider or get_provider()
        self._max_turns = max_turns
        self._budget = BudgetState(max_tokens=max_tokens, max_cost=max_cost)
        self._checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self._checkpoints: list[TurnCheckpoint] = []
        self._max_diff_chars = max_diff_chars

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

            follow_up_needed = self._should_follow_up(response, context, turn)

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
        context_parts = [f"## Commit: {context.commit_id}\n## Project: {context.project}\n"]

        if context.message:
            context_parts.append(f"## Commit Message\n{context.message.strip()}\n")

        if context.diff:
            limit = self._max_diff_chars
            diff_preview = context.diff[:limit]
            if len(context.diff) > limit:
                diff_preview += f"\n... (truncated, {len(context.diff)} chars total)"
            context_parts.append(f"## Diff\n```\n{diff_preview}\n```\n")

        if context.touched_files:
            context_parts.append("## Touched Files\n" + "\n".join(f"- {f}" for f in context.touched_files))

        if context.csv_features:
            feat_str = ", ".join(f"{k}={v}" for k, v in sorted(context.csv_features.items()))
            context_parts.append(f"\n## Numeric Features\n{feat_str}")

        if context.router_probability is not None:
            route = context.router_route or "UNKNOWN"
            context_parts.append(
                f"\n## ML Risk Prior\n"
                f"router_probability={context.router_probability:.3f} (route={route})\n"
                "Note: This is an ML model score from change metrics. It is a prior, not a "
                "defect label. Use it as one input to the rubric, especially criterion (c)."
            )

        if context.missing_reasons:
            context_parts.append("\n## Missing Context\n" + "\n".join(f"- {r}" for r in context.missing_reasons))

        user_content = "\n".join(context_parts)

        return [
            LLMMessage(role="system", content=INVESTIGATION_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_content),
        ]

    def _build_hypothesis_artifact(
        self,
        parsed: dict[str, Any],
        context: InvestigationContext,
        verdict: Any,
        localization: list[LocalizationClaim],
        findings: list[str],
        validation_error: str | None = None,
    ) -> HypothesisArtifact:
        """Build HypothesisArtifact from parsed LLM output and context signals."""
        diff_truncated = len(context.diff or "") >= self._max_diff_chars
        return HypothesisArtifact(
            supported_count=verdict.supported_count,
            production_defect_signals=has_production_defect_signals(context),
            localization=localization,
            diff_truncated=diff_truncated,
            archetype_is_ambiguous=not detect_archetype(context),
            findings=findings,
            validation_error=validation_error,
        )

    def _should_follow_up(
        self,
        response: LLMResponse,
        context: InvestigationContext,
        turn: int,
    ) -> bool:
        """Determine if another turn is needed using the deterministic Script gate.

        The LLM's follow_up_needed field is DEPRECATED and NOT read here.
        Gate decisions are driven entirely by HypothesisArtifact signals.
        """
        if turn >= self._max_turns:
            return False
        if self._budget.budget_exceeded:
            return False

        # Parse LLM output to get signals for the artifact (not follow_up_needed)
        try:
            parsed = json.loads(response.content)
        except (json.JSONDecodeError, TypeError):
            parsed = {}

        reasoning = coerce_text_field(parsed.get("reasoning"), "Investigation completed.")
        findings = normalize_findings(parsed.get("findings"))

        localization = []
        for loc in parsed.get("localization", []):
            if isinstance(loc, dict) and "file" in loc:
                localization.append(LocalizationClaim(
                    file=loc["file"],
                    lines=parse_lines(loc.get("lines")),
                    rationale=loc.get("rationale", ""),
                ))

        risk_level_str = parsed.get("risk_level", "MEDIUM")
        try:
            risk_level = RiskLevel(risk_level_str)
        except ValueError:
            risk_level = RiskLevel.MEDIUM

        verdict = evaluate_risk(risk_level, context, reasoning)
        artifact = self._build_hypothesis_artifact(
            parsed, context, verdict, localization, findings
        )
        return evaluate_gate(artifact).follow_up_needed

    def _assemble_report(
        self,
        context: InvestigationContext,
        last_response: LLMResponse,
        tools_used: list[str],
        turns: int,
    ) -> CommitInvestigationReport:
        """Parse LLM output into a validated CommitInvestigationReport."""
        content = (last_response.content or "").strip()
        if not content:
            raise InvalidInvestigationResponseError("Empty LLM response; cannot assemble report")

        parsed = extract_json(last_response.content)
        if not parsed or "risk_level" not in parsed:
            preview = content[:300].replace("\n", " ")
            raise InvalidInvestigationResponseError(
                f"Invalid LLM JSON (missing risk_level): {preview!r}"
            )

        risk_level_str = parsed["risk_level"]
        try:
            risk_level = RiskLevel(risk_level_str)
        except ValueError as exc:
            raise InvalidInvestigationResponseError(
                f"Invalid risk_level {risk_level_str!r}"
            ) from exc

        reasoning = coerce_text_field(parsed.get("reasoning"), "Investigation completed.")
        verdict = evaluate_risk(risk_level, context, reasoning)
        risk_level = verdict.risk_level

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
                    lines=parse_lines(loc.get("lines")),
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

        findings = normalize_findings(parsed.get("findings"))

        metadata: dict[str, Any] = {
            "model": last_response.model,
            "total_tokens": self._budget.total_tokens,
            "total_cost": self._budget.total_cost,
            "budget_exceeded": self._budget.budget_exceeded,
            "missing_reasons": list(context.missing_reasons),
        }
        if verdict.cap_applied:
            metadata["clean_commit_risk_cap_applied"] = True  # backward compat key
            metadata["cap_applied"] = True
            metadata["cap_reason"] = verdict.cap_reason
            metadata["applied_rules"] = list(verdict.applied_rules)

        return CommitInvestigationReport(
            commit_id=context.commit_id,
            project=context.project,
            risk_assessment=RiskAssessment(level=risk_level, confidence=confidence),
            evidence=evidence_items,
            findings=findings,
            localization=localization,
            reasoning_summary=reasoning,
            recommendations=recommendations,
            tools_used=tools_used,
            turn_count=turns,
            metadata=metadata,
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


