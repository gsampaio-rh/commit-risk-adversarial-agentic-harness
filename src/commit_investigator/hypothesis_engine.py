"""HypothesisEngine: LLM prompt, types, and message assembly for hypothesis generation.

The LLM's sole task is to generate hypotheses — it does NOT score risk levels
or apply clean-commit discrimination. Those responsibilities belong to the Script
layer (risk_policy, archetype, evidence_tagger).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from commit_investigator.context_builder import InvestigationContext
from commit_investigator.llm import CursorSDKProvider, LLMMessage, LLMProvider, LLMResponse
from commit_investigator.smart_diff import AssembledDiff

logger = logging.getLogger(__name__)

HYPOTHESIS_SYSTEM_PROMPT = """\
You are a commit risk investigator. Your task: identify specific failure modes
this commit COULD introduce based on the provided diff and context.

## OUTPUT FORMAT

Respond ONLY with valid JSON (no markdown, no text outside JSON):

{
  "summary": "1-2 sentence description of what changed and the stated intent",
  "hypotheses": [
    {
      "mechanism": "If <specific condition> then <specific failure> at <file>:<area>",
      "evidence_quote": "exact line(s) from the diff showing this mechanism (empty string if not visible)",
      "file": "primary file this hypothesis concerns",
      "lines": [start_line, end_line],
      "suggested_action": "what to verify or fix if this hypothesis is confirmed (1 sentence)"
    }
  ]
}

## COVERAGE REQUIREMENT

For each production source file (*.java, *.py, *.scala, *.go, *.ts, *.js)
in Touched Files or the Diff with SUBSTANTIVE changes (>3 lines added or
removed): emit ≥1 hypothesis with `file` matching that path, OR cite
SKIP:<path> with reason (test-only, doc-only, config-only, message-only,
minor-change). Cover all substantive files before adding extras.
Do not anchor on the most salient hunk alone.

## INVESTIGATION FOCUS

Prioritize mechanisms visible in the diff:
- Guard or null-check removal exposing NPE or wrong execution path
- Concurrency change (synchronized, volatile, Lock) risking data races
- Lifecycle or ordering change (startup, shutdown, @Order) breaking initialization
- API signature change (removed method, erased generics) breaking callers
- Missing input validation on production code paths

Generate at least one hypothesis per required production file (see COVERAGE).
Add further hypotheses only when distinct mechanisms apply.
If no mechanism is visible, use an empty evidence_quote.
Do NOT include risk_level, confidence, follow_up_needed, or rubric assessment.
"""

COVERAGE_SECTION_HEADER = "## COVERAGE REQUIREMENT"

PRODUCTION_SOURCE_SUFFIXES = (".java", ".py", ".scala", ".go", ".ts", ".js")


def is_production_source_file(path: str) -> bool:
    """Return True if path looks like a production source file (not test/doc/config)."""
    lower = path.lower()
    if not lower.endswith(PRODUCTION_SOURCE_SUFFIXES):
        return False
    basename = lower.rsplit("/", 1)[-1]
    if basename.startswith("test") or basename.endswith("test.java"):
        return False
    if "/test/" in lower or "/tests/" in lower or lower.startswith("test/"):
        return False
    if "/docs/" in lower or lower.endswith(".md"):
        return False
    return True


def extract_coverage_section(prompt: str = HYPOTHESIS_SYSTEM_PROMPT) -> str:
    """Return the COVERAGE REQUIREMENT section body (header through next ## section)."""
    if COVERAGE_SECTION_HEADER not in prompt:
        return ""
    start = prompt.index(COVERAGE_SECTION_HEADER)
    rest = prompt[start + len(COVERAGE_SECTION_HEADER) :]
    next_header = rest.find("\n## ")
    if next_header == -1:
        return COVERAGE_SECTION_HEADER + rest
    return COVERAGE_SECTION_HEADER + rest[:next_header]


HYPOTHESIS_SYSTEM_PROMPT_H1H4T3 = """\
You are a commit risk investigator. Your task: identify specific failure modes
this commit COULD introduce based on the provided diff and context.

Reason symptom-first: imagine the user-visible failure BEFORE naming code structure.
Do not apply familiar framework templates without citing the exact changed line.

## OUTPUT FORMAT

Respond ONLY with valid JSON (no markdown, no text outside JSON):

{
  "summary": "1-2 sentence description of what changed and the stated intent",
  "hypotheses": [
    {
      "mechanism": "Observable: [user-visible failure]. Root change: [+/- line from diff]. Mechanism: [causal chain at file:area]",
      "evidence_quote": "exact line(s) from the diff showing this mechanism (empty string if not visible)",
      "file": "primary file this hypothesis concerns",
      "lines": [start_line, end_line],
      "suggested_action": "what to verify or fix if this hypothesis is confirmed (1 sentence)"
    }
  ]
}

## CHANGED-LINE EVIDENCE

For each primary hypothesis (first 3 in the list): if evidence_quote is non-empty,
it MUST contain at least one line starting with + or - (an actual code change from
the diff). Context-only lines (unchanged diff context without + or - prefix) do
NOT count. Diff file headers (+++ b/file, --- a/file) are NOT code changes.
Use empty evidence_quote if no changed line supports the mechanism.

""" + extract_coverage_section() + """

## INVESTIGATION FOCUS

Start from observable runtime symptoms, then trace to the specific +/- changed line:
- What user-visible error, crash, wrong output, or silent failure could occur?
- Which added (+) or removed (-) line enables that failure path?
- Guard or null-check removal exposing NPE or wrong execution path
- Concurrency change (synchronized, volatile, Lock) risking data races
- Lifecycle or ordering change (startup, shutdown, @Order) breaking initialization
- API signature change (removed method, erased generics) breaking callers
- Missing input validation on production code paths

Generate at least one hypothesis per required production file (see COVERAGE).
Add further hypotheses only when distinct mechanisms apply.
If no mechanism is visible, use an empty evidence_quote.
Do NOT include risk_level, confidence, follow_up_needed, or rubric assessment.
"""


class HypothesisSpec(BaseModel):
    """A single defect hypothesis generated by the LLM."""

    mechanism: str = Field(description="Failure mode description: If X then Y at file:area")
    evidence_quote: str = Field(default="", description="Exact diff line(s) supporting this hypothesis")
    file: str = Field(default="", description="Primary file this hypothesis concerns")
    lines: list[int] = Field(default_factory=list, description="[start_line, end_line] optional")
    suggested_action: str = Field(
        default="",
        description="What to verify or fix if this hypothesis is confirmed (1 sentence)",
    )


class HypothesisResponse(BaseModel):
    """LLM output: a list of defect hypotheses with a summary."""

    summary: str = Field(description="1-2 sentence description of what changed")
    hypotheses: list[HypothesisSpec] = Field(
        default_factory=list,
        description="List of specific failure mode hypotheses",
    )


def parse_hypothesis_response(raw: str) -> HypothesisResponse:
    """Parse LLM output into HypothesisResponse, stripping any markdown wrapper.

    Raises ValueError if the JSON is invalid or the schema doesn't match.
    """
    stripped = raw.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1]) if len(lines) > 2 else stripped

    data: dict[str, Any] = json.loads(stripped)
    return HypothesisResponse.model_validate(data)


def complete_with_parse_retry(
    llm: LLMProvider,
    messages: list[LLMMessage],
    tools_openai: list[dict[str, Any]] | None,
    parse_fn: Callable[[LLMResponse], HypothesisResponse],
    record_fn: Callable[[LLMResponse], None],
    parse_error: type[Exception],
) -> tuple[HypothesisResponse, LLMResponse]:
    """Call LLM and parse HypothesisResponse; retry once on parse failure (EC-1)."""
    for attempt in range(2):
        response = llm.complete(messages=messages, tools=tools_openai, temperature=0.0)
        record_fn(response)
        try:
            return parse_fn(response), response
        except parse_error as exc:
            if attempt == 0:
                messages.append(LLMMessage(
                    role="user",
                    content=(
                        f"Schema validation failed: {exc}. "
                        "Return valid JSON with 'summary' and 'hypotheses' fields only."
                    ),
                ))
                continue
            raise
    raise parse_error("No LLM response received")  # pragma: no cover


def _has_changed_line_citation(evidence_quote: str) -> bool:
    """Return True if evidence_quote cites at least one +/- changed diff line."""
    for line in evidence_quote.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("+++") or stripped.startswith("---"):
            continue
        if stripped[0] in "+-":
            return True
    return False


def _ungrounded_primary_hypotheses(response: HypothesisResponse) -> list[HypothesisSpec]:
    """Return primary hypotheses (first 3) with non-empty context-only evidence quotes."""
    ungrounded: list[HypothesisSpec] = []
    for hyp in response.hypotheses[:3]:
        if hyp.evidence_quote.strip() and not _has_changed_line_citation(hyp.evidence_quote):
            ungrounded.append(hyp)
    return ungrounded


def _build_mechanism_challenge(ungrounded: list[HypothesisSpec]) -> str:
    """Build a user message challenging context-only evidence citations."""
    lines = [
        "Mechanism evidence must cite at least one +/- changed diff line (not context-only).",
        "Revise the following primary hypotheses with evidence_quote containing a + or - line:",
    ]
    for hyp in ungrounded:
        lines.append(f"- mechanism: {hyp.mechanism!r}")
    return "\n".join(lines)


def mechanism_evaluator_loop(
    llm: LLMProvider,
    messages: list[LLMMessage],
    tools_openai: list[dict[str, Any]] | None,
    parse_fn: Callable[[LLMResponse], HypothesisResponse],
    record_fn: Callable[[LLMResponse], None],
    parse_error: type[Exception],
) -> tuple[HypothesisResponse, LLMResponse]:
    """T3 evaluator loop: grounded changed-line evidence, max 2 rounds.

    Uses Agent.create()+agent.send() multi-turn when the provider supports it
    (CursorSDKProvider), so the challenge lands as a real follow-up message
    rather than being embedded as conversation text in a one-shot prompt.
    Falls back to sequential complete() calls for other providers.
    """
    supports_multi_turn = isinstance(llm, CursorSDKProvider)

    if supports_multi_turn:
        return _mechanism_evaluator_loop_multi_turn(
            llm, messages, parse_fn, record_fn, parse_error,
        )
    return _mechanism_evaluator_loop_single(
        llm, messages, tools_openai, parse_fn, record_fn, parse_error,
    )


def _mechanism_evaluator_loop_multi_turn(
    llm: CursorSDKProvider,
    messages: list[LLMMessage],
    parse_fn: Callable[[LLMResponse], HypothesisResponse],
    record_fn: Callable[[LLMResponse], None],
    parse_error: type[Exception],
) -> tuple[HypothesisResponse, LLMResponse]:
    """T3 via Agent.create()+agent.send() — challenge is a real follow-up turn."""
    turns: list[list[LLMMessage]] = [messages]
    responses = llm.complete_multi_turn(turns[:1])
    llm_response = responses[0]
    record_fn(llm_response)

    try:
        parsed = parse_fn(llm_response)
    except parse_error as exc:
        logger.warning("T3 round 1 parse failed: %s — retrying in same session", exc)
        retry_msg = LLMMessage(
            role="user",
            content=(
                f"Schema validation failed: {exc}. "
                "Return valid JSON with 'summary' and 'hypotheses' fields only."
            ),
        )
        retry_responses = llm.complete_multi_turn([[retry_msg]])
        llm_response = retry_responses[0]
        record_fn(llm_response)
        parsed = parse_fn(llm_response)

    ungrounded = _ungrounded_primary_hypotheses(parsed)
    if not ungrounded:
        logger.debug("T3 round 1: all primary hypotheses grounded — no challenge needed")
        return parsed, llm_response

    challenge_text = _build_mechanism_challenge(ungrounded)
    logger.info(
        "T3 round 1: %d ungrounded hypothesis(es) — sending challenge turn:\n%s",
        len(ungrounded), challenge_text,
    )

    challenge_responses = llm.complete_multi_turn([[LLMMessage(role="user", content=challenge_text)]])
    llm_response = challenge_responses[0]
    record_fn(llm_response)

    try:
        parsed = parse_fn(llm_response)
    except parse_error as exc:
        logger.warning("T3 round 2 parse failed: %s — returning round 1 result", exc)

    still_ungrounded = _ungrounded_primary_hypotheses(parsed)
    if still_ungrounded:
        logger.info(
            "T3 round 2: %d hypothesis(es) still ungrounded after challenge — accepting best effort",
            len(still_ungrounded),
        )
    else:
        logger.info("T3 round 2: all primary hypotheses grounded after challenge")

    return parsed, llm_response


def _mechanism_evaluator_loop_single(
    llm: LLMProvider,
    messages: list[LLMMessage],
    tools_openai: list[dict[str, Any]] | None,
    parse_fn: Callable[[LLMResponse], HypothesisResponse],
    record_fn: Callable[[LLMResponse], None],
    parse_error: type[Exception],
) -> tuple[HypothesisResponse, LLMResponse]:
    """T3 fallback for non-multi-turn providers: appends challenge to message list."""
    parsed: HypothesisResponse | None = None
    llm_response: LLMResponse | None = None

    for evaluator_round in range(2):
        parsed, llm_response = complete_with_parse_retry(
            llm, messages, tools_openai, parse_fn, record_fn, parse_error,
        )
        ungrounded = _ungrounded_primary_hypotheses(parsed)
        if not ungrounded:
            logger.debug("T3 round %d: all grounded", evaluator_round + 1)
            return parsed, llm_response

        if evaluator_round == 0:
            challenge = _build_mechanism_challenge(ungrounded)
            logger.info("T3 round 1 (single): %d ungrounded — appending challenge", len(ungrounded))
            messages.append(LLMMessage(role="user", content=challenge))
            continue
        break

    assert parsed is not None and llm_response is not None
    return parsed, llm_response


def build_investigation_messages(
    context: InvestigationContext,
    system_prompt: str = HYPOTHESIS_SYSTEM_PROMPT,
) -> list[LLMMessage]:
    """Assemble the LLM message list for hypothesis generation.

    Uses the pre-assembled smart diff from context.truncation_metadata
    when available, falling back to context.diff.
    """
    context_parts = [f"## Commit: {context.commit_id}\n## Project: {context.project}\n"]
    missing_reasons = list(context.missing_reasons)

    if context.message:
        context_parts.append(f"## Commit Message\n{context.message.strip()}\n")

    if context.diff:
        diff_text = context.diff
        tm: AssembledDiff | None = context.truncation_metadata
        if tm and tm.truncated_files:
            diff_text += (
                f"\n... (smart-truncated: {len(tm.truncated_files)} file(s) omitted: "
                + ", ".join(tm.truncated_files) + ")"
            )
        context_parts.append(f"## Diff\n```\n{diff_text}\n```\n")

    if context.touched_files:
        context_parts.append(
            "## Touched Files\n" + "\n".join(f"- {f}" for f in context.touched_files)
        )

    file_history_lines = _format_file_histories(context)
    if file_history_lines:
        context_parts.append("## File History\n" + "\n".join(file_history_lines))
    else:
        missing_reasons.append("File history unavailable — no prior commit data for changed files")

    author_stats_text = _format_author_stats(context)
    if author_stats_text:
        context_parts.append("## Author Stats\n" + author_stats_text)
    else:
        missing_reasons.append("Author statistics unavailable — author not in training data")

    if context.csv_features:
        feature_lines = [f"  {k}: {v}" for k, v in sorted(context.csv_features.items())]
        context_parts.append("## Numeric Features\n" + "\n".join(feature_lines))

    if context.router_probability is not None:
        context_parts.append(f"## Router Prior\nrouter_probability: {context.router_probability:.3f}\n")

    if missing_reasons:
        context_parts.append("## Missing Context\n" + "\n".join(f"- {r}" for r in missing_reasons))

    user_content = "\n\n".join(context_parts)
    return [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_content),
    ]


def _format_file_histories(context: InvestigationContext) -> list[str]:
    """Format file histories as markdown lines. Returns empty list if unavailable."""
    histories = context.file_histories
    if not histories:
        return []
    lines: list[str] = []
    for fpath, history in list(histories.items())[:5]:
        if not history:
            continue
        lines.append(f"### {fpath}")
        for entry in history[:5]:
            lines.append(f"  - {entry.commit_id[:8]} | {entry.date[:10]} | {entry.author} | {entry.message}")
    return lines


def _format_author_stats(context: InvestigationContext) -> str:
    """Format author stats as a markdown block. Returns empty string if unavailable."""
    s = context.author_stats
    if s is None:
        return ""
    return (
        f"- Author: {s.author}\n"
        f"- Total commits: {s.total_commits}\n"
        f"- Buggy rate: {s.buggy_rate:.2%}\n"
        f"- Avg files changed: {s.avg_files_changed:.1f}"
    )
