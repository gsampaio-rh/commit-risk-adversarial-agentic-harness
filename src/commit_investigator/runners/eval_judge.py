"""LLM-as-judge for reasoning faithfulness evaluation.

Replaces naive word-overlap (D3) and stub (D5) with structured rubric scoring.
Also provides D6 automated evidence grounding (no LLM cost).

Each judge result includes a 1-sentence justification for human audit.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from commit_investigator.infra.jira_client import JiraIssue
from commit_investigator.infra.llm import LLMMessage, LLMProvider
from commit_investigator.analysis.report import CommitInvestigationReport

logger = logging.getLogger(__name__)


@dataclass
class JudgeResult:
    """Result from a single judge evaluation."""

    dimension: str
    score: int
    max_score: int
    normalized: float
    justification: str
    raw_response: str = ""

    @property
    def is_valid(self) -> bool:
        return 0 <= self.score <= self.max_score


D3_RUBRIC = """You are an evaluation judge. Score how faithfully the agent's reasoning matches the actual root cause documented in the JIRA issue and fix diff.

## Rubric (0-4):
- 0: Generic boilerplate — reasoning has no connection to the actual bug
- 1: Vaguely related — mentions the right area (e.g., "concurrency") but no specifics
- 2: Partially correct — identifies some aspects of the real issue but misses the core mechanism
- 3: Mostly correct — captures the key failure mechanism but misses important details
- 4: Precise match — reasoning accurately describes the root cause confirmed by the fix diff and JIRA

## Inputs

### Agent Reasoning
{agent_reasoning}

### Agent Findings
{agent_findings}

### JIRA Issue: {jira_key}
Summary: {jira_summary}
Description: {jira_description}

### Fix Diff Files
{fix_files}

## Instructions
Respond with ONLY a JSON object:
{{"score": <0-4>, "justification": "<one sentence explaining the score>"}}"""

D3_FIX_DIFF_FALLBACK_RUBRIC = """You are an evaluation judge. Score how faithfully the agent's reasoning matches the actual root cause visible in the fix diff. (No JIRA description available — using fix diff as oracle.)

## Rubric (0-4):
- 0: Generic boilerplate — reasoning has no connection to the actual code changes
- 1: Vaguely related — mentions the right area but no specifics from the diff
- 2: Partially correct — identifies some aspects of the change but misses the core mechanism
- 3: Mostly correct — captures the key change in the diff but misses important details
- 4: Precise match — reasoning accurately describes the root cause visible in the fix diff

## Inputs

### Agent Reasoning
{agent_reasoning}

### Agent Findings
{agent_findings}

### JIRA Issue: {jira_key}
Summary: {jira_summary}
(Description unavailable — using fix diff as oracle)

### Fix Diff Files
{fix_files}

## Instructions
Respond with ONLY a JSON object:
{{"score": <0-4>, "justification": "<one sentence explaining the score>"}}"""

D5_RUBRIC = """You are an evaluation judge. Score how relevant the agent's recommendations are to the actual fix that was applied.

## Rubric (0-3):
- 0: Irrelevant — recommendations have no connection to what the fix actually did
- 1: Tangentially related — right general direction but wrong specific action
- 2: Relevant — recommendations align with the fix pattern but lack precision
- 3: Precise match — recommendations directly describe the kind of fix that was applied

## Inputs

### Agent Recommendations
{agent_recommendations}

### JIRA Issue: {jira_key}
Summary: {jira_summary}
Resolution: {jira_resolution}

### Fix Diff Files
{fix_files}

## Instructions
Respond with ONLY a JSON object:
{{"score": <0-3>, "justification": "<one sentence explaining the score>"}}"""


class ReasoningJudge:
    """LLM-as-judge for reasoning faithfulness (D3, D5) and evidence grounding (D6).

    D3 and D5 use an LLM judge with structured rubrics. D6 is fully automated
    (no LLM cost) — it checks whether agent claims are grounded in actual diff/files.
    """

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    def score_d3_root_cause(
        self,
        report: CommitInvestigationReport,
        jira_issue: JiraIssue,
        fix_files: set[str] | None = None,
    ) -> JudgeResult:
        """D3: Score root-cause faithfulness (0-4 rubric)."""
        prompt = D3_RUBRIC.format(
            agent_reasoning=report.reasoning_summary,
            agent_findings="\n".join(f"- {f}" for f in report.findings),
            jira_key=jira_issue.key,
            jira_summary=jira_issue.summary,
            jira_description=_truncate(jira_issue.description or "(no description)", 2000),
            fix_files=", ".join(sorted(fix_files)) if fix_files else "(unavailable)",
        )
        return self._call_judge(prompt, dimension="D3_diagnosis", max_score=4)

    def score_d3_root_cause_fix_diff_fallback(
        self,
        report: CommitInvestigationReport,
        jira_issue: JiraIssue,
        fix_files: set[str] | None = None,
    ) -> JudgeResult:
        """D3 fallback: score root-cause faithfulness using fix diff when JIRA has no description."""
        prompt = D3_FIX_DIFF_FALLBACK_RUBRIC.format(
            agent_reasoning=report.reasoning_summary,
            agent_findings="\n".join(f"- {f}" for f in report.findings),
            jira_key=jira_issue.key,
            jira_summary=jira_issue.summary,
            fix_files=", ".join(sorted(fix_files)) if fix_files else "(unavailable)",
        )
        return self._call_judge(prompt, dimension="D3_diagnosis", max_score=4)

    def score_d5_recommendations(
        self,
        report: CommitInvestigationReport,
        jira_issue: JiraIssue,
        fix_files: set[str] | None = None,
    ) -> JudgeResult:
        """D5: Score recommendation relevance (0-3 rubric)."""
        recs = "\n".join(
            f"- [{r.priority.value}] {r.action}: {r.rationale}"
            for r in report.recommendations
        ) or "(no recommendations)"

        prompt = D5_RUBRIC.format(
            agent_recommendations=recs,
            jira_key=jira_issue.key,
            jira_summary=jira_issue.summary,
            jira_resolution=jira_issue.resolution or "(unresolved)",
            fix_files=", ".join(sorted(fix_files)) if fix_files else "(unavailable)",
        )
        return self._call_judge(prompt, dimension="D5_recommendations", max_score=3)

    @staticmethod
    def score_d6_evidence_grounding(
        report: CommitInvestigationReport,
        actual_diff: str | None = None,
        actual_files: set[str] | None = None,
    ) -> JudgeResult:
        """D6: Automated evidence grounding — no LLM cost.

        Checks whether agent evidence and localization claims reference
        real files/hunks from the commit diff. Catches boilerplate reports
        that mention no concrete artifacts.
        """
        grounding_points = 0
        max_points = 4
        reasons: list[str] = []

        # Point 1: Does the agent cite specific file paths in localization?
        has_localization = len(report.localization) > 0
        if has_localization:
            grounding_points += 1
            reasons.append(f"{len(report.localization)} localization claims")

        # Point 2: Do localization files overlap with actual touched files?
        if actual_files and has_localization:
            agent_files = {_basename(loc.file) for loc in report.localization}
            overlap = agent_files & {_basename(f) for f in actual_files}
            if overlap:
                grounding_points += 1
                reasons.append(f"localization overlaps actual files: {sorted(overlap)}")

        # Point 3: Does reasoning mention specific file names from the diff?
        if actual_files:
            reasoning_lower = report.reasoning_summary.lower()
            file_mentions = sum(
                1 for f in actual_files
                if _basename(f).lower() in reasoning_lower
            )
            if file_mentions > 0:
                grounding_points += 1
                reasons.append(f"reasoning mentions {file_mentions} actual file(s)")

        # Point 4: Does evidence content contain diff-specific strings (not generic)?
        has_specific_evidence = any(
            len(e.content) > 50 and e.content != "Numeric features only"
            for e in report.evidence
        )
        if has_specific_evidence:
            grounding_points += 1
            reasons.append("evidence contains diff-specific content")

        justification = "; ".join(reasons) if reasons else "no concrete grounding found"
        return JudgeResult(
            dimension="D6_evidence_grounding",
            score=grounding_points,
            max_score=max_points,
            normalized=grounding_points / max_points,
            justification=justification,
        )

    def _call_judge(self, prompt: str, dimension: str, max_score: int) -> JudgeResult:
        """Send rubric prompt to LLM and parse the structured response."""
        messages = [
            LLMMessage(role="system", content="You are a strict evaluation judge. Respond with JSON only."),
            LLMMessage(role="user", content=prompt),
        ]

        try:
            response = self._llm.complete(messages, temperature=0.0, max_tokens=256)
        except Exception as exc:
            logger.error("Judge LLM call failed for %s: %s", dimension, exc)
            return JudgeResult(
                dimension=dimension,
                score=0,
                max_score=max_score,
                normalized=0.0,
                justification=f"Judge call failed: {exc}",
            )

        parsed = _parse_judge_response(response.content)
        raw_score = parsed.get("score", 0)
        raw_score = max(0, min(max_score, int(raw_score)))

        return JudgeResult(
            dimension=dimension,
            score=raw_score,
            max_score=max_score,
            normalized=raw_score / max_score if max_score > 0 else 0.0,
            justification=parsed.get("justification", "No justification provided"),
            raw_response=response.content,
        )


def _parse_judge_response(text: str) -> dict[str, Any]:
    """Extract JSON from judge LLM output, tolerating markdown fences."""
    if not text:
        return {}

    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
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


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... (truncated, {len(text)} chars total)"


def _basename(path: str) -> str:
    """Extract filename from a path."""
    from pathlib import Path as _Path
    return _Path(path).name
