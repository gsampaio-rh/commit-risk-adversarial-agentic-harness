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


INVESTIGATION_SYSTEM_PROMPT = """\
You are a commit risk investigator. Analyze the provided commit context and \
produce a risk assessment grounded in diff evidence.

## INVESTIGATION METHOD

Structure your "reasoning" field in four stages:

STAGE 1 — CHANGE SUMMARY: What changed, which files, stated intent.

STAGE 2 — DEFECT HYPOTHESES: List 2–3 specific failure modes this change \
COULD introduce. Format each as:
  "HYPOTHESIS: If <condition> then <failure> in <file>:<area>"

STAGE 3 — EVIDENCE: For each hypothesis, cite diff evidence for/against.
  Mark each: SUPPORTED / REFUTED / UNVERIFIABLE / SPECULATIVE.
  SUPPORTED: diff shows a concrete mechanism (removed guard, changed control flow, \
wrong default, lifecycle ordering change). SPECULATIVE: relies on assumed external \
behavior (cross-version API breakage, theoretical caller impact) not shown in the diff.
  Under clean-commit discrimination (below): type/import substitution, version-bump \
incompatibility, or comment-only signals are SPECULATIVE unless diff shows wrong \
logic (removed guard, inverted condition) at a call site.

STAGE 4 — VERDICT: State rubric tier and risk_level.

## RISK CLASSIFICATION RUBRIC

CRITICAL: Credential exposure, injection vulnerability, data loss, or \
production outage likely in normal usage paths.

HIGH: At least one of:
  (a) A SUPPORTED defect hypothesis with diff evidence
  (b) API/binary incompatibility (removed generics, changed public signatures, \
removed guards on production paths)
  (c) ML risk prior router_probability ≥ 0.70
  (d) Security-relevant change without input validation
  (e) Large new production logic (>200 lines in one new file) with complex \
behavior and no tests added in the same commit

MEDIUM: Risk indicators exist but no SUPPORTED hypothesis. Uncertainty \
about impact. Truncated diff preventing verification.

LOW: Docs-only, test-only, formatting, or no defect mechanism identifiable.

## CLEAN-COMMIT DISCRIMINATION (apply ONLY to these patterns)

PRIMARY change: the dominant intent of the commit — version bump, type/import migration, \
label rename, or comment-only signal — even when incidental typing refactors (split \
CompletableFuture locals, raw-type erasure, import swaps) appear in the same files.

When the PRIMARY change is one of the patterns below, apply STRICT evidence rules \
and cap risk_level at MEDIUM unless a SUPPORTED hypothesis passes the strict bar:
  - Dependency/library version bump (pom.xml or gradle version changes dominate)
  - Comment or doc removal about compatibility thresholds (no code behavior change)
  - Constant, label, enum, or property rename with no control-flow change
  - Pure refactor (extract/rename) or mechanical API method rename
  - Type/import substitution for API migration without logic change

Strict bar under clean-commit discrimination:
  - Cross-version incompatibility, binary breakage, or assumed caller impact → \
SPECULATIVE (never SUPPORTED) unless diff shows removed guard, wrong default, or \
inverted condition at a call site
  - Rubric criterion (b) API/binary incompatibility does NOT apply under discrimination \
for migration-driven generic erasure, import swaps, or raw-type signature adaptation
  - When all defect hypotheses are SPECULATIVE or UNVERIFIABLE → risk_level ≤ MEDIUM \
(router_probability does not override this cap, even if ≥ 0.70)

Migration typing refactors alone do NOT waive discrimination: split typed variables, \
CompletableFuture/NotifyingFuture substitution, or raw QueryFactory erasure during a \
version bump remain under discrimination unless a strict-bar SUPPORTED mechanism exists.

Do NOT apply clean-commit discrimination when:
  - Material guard removal, null-check removal, validation removal, or inverted \
condition in visible production hunks
  - Lifecycle ordering, startup/shutdown sequencing, or concurrency semantics changed \
(SmartLifecycle, @Order, synchronized/lock paths) in visible production hunks
  - Commit references a filed defect (CAMEL-*, JIRA key) AND production logic changed \
beyond mechanical API adaptation

RULES:
- A SUPPORTED hypothesis with diff evidence → risk_level MUST be ≥ HIGH \
(except clean-commit discrimination cap above).
- SPECULATIVE-only hypotheses → MEDIUM, not HIGH — applies globally AND under \
clean-commit discrimination.
- Do NOT assign MEDIUM because a change is "additive", has "limited blast \
radius", or is "backward-compatible in intent."
- Do NOT assign LOW/MEDIUM solely because the commit message mentions a fix \
or the change appears corrective. Residual risk from an incomplete fix or \
regression elsewhere still requires ≥ HIGH when a SUPPORTED mechanism exists.
- router_probability is an ML prior (0.0–1.0), NOT ground truth.
- findings[] must list only SUPPORTED hypotheses with file paths.
- localization[] must list files where a SUPPORTED hypothesis points, NOT all \
touched files.
- NEVER reuse names, phrases, or scenarios from EXAMPLE A/B (e.g. \
WidgetConverter.java, library Z) — reason only from the actual diff provided.

## EXAMPLE A — HIGH (fictional placeholder names only — do not copy)

Commit: Bumps dependency Z in multiple modules. Diff removes null-check on \
conversion path in WidgetConverter.java.

reasoning: "STAGE 1: ... STAGE 2: HYPOTHESIS: If null input on Boolean \
conversion, removed guard returns null causing NPE in WidgetConverter.java:142. \
STAGE 3: SUPPORTED — diff removes guard at line 142. STAGE 4: Rubric HIGH, \
criterion (a)."
risk_level: HIGH

## EXAMPLE B — LOW (fictional — do not copy)

Commit: Fixes test assertion order in one unit test file.

reasoning: "STAGE 1: Single test file, assertion order fix. STAGE 2: No \
defect hypotheses — no production code changed. STAGE 3: N/A. STAGE 4: \
Rubric LOW."
risk_level: LOW

## EXAMPLE C — MEDIUM (fictional — do not copy)

Commit: Upgrades library Q in pom.xml. Production files adapt imports from \
LegacyFuture to standard Future; interface method loses generic bound during migration.

reasoning: "STAGE 1: Version bump dominates; type substitution in Adapter.java. \
STAGE 2: HYPOTHESIS: cross-version binary break for external callers. STAGE 3: \
SPECULATIVE — migration-consistent raw-type change, no wrong logic at call site. \
Criterion (b) does not apply under discrimination. STAGE 4: Rubric MEDIUM."
risk_level: MEDIUM

IMPORTANT: Respond ONLY with a single JSON object (no markdown, no text \
outside JSON). Required fields:
- risk_level: one of LOW, MEDIUM, HIGH, CRITICAL
- confidence: float 0.0 to 1.0
- reasoning: string with all four stages
- findings: list of strings (SUPPORTED hypotheses only)
- follow_up_needed: boolean
- localization: list of {file, lines, rationale} objects
- recommendations: list of {action, priority, rationale} objects"""


class InvalidInvestigationResponseError(ValueError):
    """Raised when LLM output is empty, unparseable, or missing required fields."""


_LIFECYCLE_RE = re.compile(
    r"SmartLifecycle|@Order|shutdown|startup|@EventListener|lifecycle",
    re.IGNORECASE,
)
_GUARD_REMOVAL_RE = re.compile(
    r"^-\s+.*(?:if\s*\(|guard|null\s*==|!=\s*null|nullcheck)",
    re.MULTILINE | re.IGNORECASE,
)
_VERSION_DIFF_RE = re.compile(r"^[-+].*(?:<version>|version\s*=)", re.MULTILINE | re.IGNORECASE)
_IMPORT_CHANGE_RE = re.compile(r"^[-+]\s*import\s+", re.MULTILINE)
_TYPE_MIGRATION_RE = re.compile(
    r"NotifyingFuture|CompletableFuture|QueryFactory|raw type",
    re.IGNORECASE,
)
_CONCURRENCY_CHANGE_RE = re.compile(
    r"^[-+].*(?:synchronized|ReentrantLock|\bLock\.|volatile\s+\w+)",
    re.MULTILINE | re.IGNORECASE,
)
_LABEL_RENAME_RE = re.compile(
    r'^[-+].*"(?:FileName|LogType|logType|logAggregationType)"',
    re.MULTILINE,
)
_COMPAT_COMMENT_RE = re.compile(
    r"^-.*(?:incompatible|compatibility|breaking threshold|binary incompatible)",
    re.MULTILINE | re.IGNORECASE,
)
_VERSION_PROPERTY_RE = re.compile(
    r"^[-+].*(?:-version>|<[\w-]*version>)",
    re.MULTILINE | re.IGNORECASE,
)


_SUPPORTED_HYPOTHESIS_RE = re.compile(
    r"(?:HYPOTHESIS\s+[A-Z0-9]+\s*[—-]\s*SUPPORTED|"
    r"HYPOTHESIS[^.\n]*?(?:—|:)\s*SUPPORTED\b|"
    r"\bSUPPORTED\s*—|\(\s*SUPPORTED\s*\))",
    re.IGNORECASE,
)


def _has_production_defect_signals(context: InvestigationContext) -> bool:
    """True when opt-out from clean-commit cap applies (buggy production patterns).

    Material guard/lifecycle/concurrency only — not JIRA ticket + routine return edits.
    """
    diff = context.diff or ""

    if _GUARD_REMOVAL_RE.search(diff):
        return True

    if _LIFECYCLE_RE.search(diff) and re.search(r"^[-+]", diff, re.MULTILINE):
        return True

    return _CONCURRENCY_CHANGE_RE.search(diff) is not None


def _matches_clean_archetype(context: InvestigationContext) -> bool:
    """True when commit matches known clean-commit FP archetypes."""
    diff = context.diff or ""
    touched = " ".join(context.touched_files or [])

    version_touched = any(name in touched for name in ("pom.xml", "build.gradle", ".gradle"))
    if version_touched and (
        _VERSION_DIFF_RE.search(diff)
        or _VERSION_PROPERTY_RE.search(diff)
        or _COMPAT_COMMENT_RE.search(diff)
    ):
        return True

    if _LABEL_RENAME_RE.search(diff):
        return True

    import_changes = len(_IMPORT_CHANGE_RE.findall(diff))
    if import_changes >= 2:
        return True

    if _TYPE_MIGRATION_RE.search(diff) and (import_changes >= 1 or version_touched):
        return True

    minus_methods = len(re.findall(r"^-\s*(?:public|protected)[^\n]*\(", diff, re.MULTILINE))
    plus_methods = len(re.findall(r"^\+\s*(?:public|protected)[^\n]*\(", diff, re.MULTILINE))
    if minus_methods >= 1 and plus_methods >= 1 and import_changes <= 1:
        if not _has_production_defect_signals(context):
            return True

    return False


def _reasoning_has_supported_hypothesis(reasoning: str) -> bool:
    if not reasoning:
        return False
    if _SUPPORTED_HYPOTHESIS_RE.search(reasoning):
        return True
    if re.search(r"STAGE 3[^STAGE]*\bHYPOTHESIS\b[^STAGE]*\bSUPPORTED\b", reasoning, re.IGNORECASE | re.DOTALL):
        return True
    return False


def _reasoning_all_speculative_or_unverifiable(reasoning: str) -> bool:
    if _reasoning_has_supported_hypothesis(reasoning):
        return False
    return bool(re.search(r"SPECULATIVE|UNVERIFIABLE", reasoning, re.IGNORECASE))


def _apply_clean_commit_risk_cap(
    risk_level: RiskLevel,
    context: InvestigationContext,
    reasoning: str,
) -> tuple[RiskLevel, bool]:
    """Cap HIGH/CRITICAL to MEDIUM for clean archetypes without production defect signals."""
    if risk_level not in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return risk_level, False

    if _has_production_defect_signals(context):
        return risk_level, False

    # Archetype commits always cap; speculative-only reasoning caps globally.
    should_cap = _matches_clean_archetype(context) or _reasoning_all_speculative_or_unverifiable(
        reasoning,
    )
    if not should_cap:
        return risk_level, False

    return RiskLevel.MEDIUM, True


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


DEFAULT_MAX_DIFF_CHARS = 16_000


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
        content = (last_response.content or "").strip()
        if not content:
            raise InvalidInvestigationResponseError("Empty LLM response; cannot assemble report")

        parsed = _extract_json(last_response.content)
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

        reasoning = _coerce_text_field(parsed.get("reasoning"), "Investigation completed.")
        risk_level, cap_applied = _apply_clean_commit_risk_cap(risk_level, context, reasoning)

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

        findings = _normalize_findings(parsed.get("findings"))

        metadata: dict[str, Any] = {
            "model": last_response.model,
            "total_tokens": self._budget.total_tokens,
            "total_cost": self._budget.total_cost,
            "budget_exceeded": self._budget.budget_exceeded,
            "missing_reasons": list(context.missing_reasons),
        }
        if cap_applied:
            metadata["clean_commit_risk_cap_applied"] = True

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


def _coerce_text_field(value: Any, default: str) -> str:
    """Normalize LLM output to a string (some models return nested JSON objects)."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _normalize_findings(raw: Any) -> list[str]:
    """Ensure findings is a list of strings for schema validation."""
    if not raw:
        return ["Investigation completed"]
    if not isinstance(raw, list):
        return [_coerce_text_field(raw, "Investigation completed")]
    findings = [_coerce_text_field(item, "") for item in raw]
    findings = [f for f in findings if f.strip()]
    return findings or ["Investigation completed"]
