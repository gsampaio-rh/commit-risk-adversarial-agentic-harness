"""D3 Attribution Quality LLM Judge.

Evaluates causal mechanism quality on a 0-4 scale:
  0 = Absent/wrong
  1 = Vague
  2 = Partial
  3 = Sound
  4 = Precise

The judge receives the bug report, suspect mechanism + evidence,
and the commit diff. It does NOT receive ground truth — it evaluates
reasoning quality, not correctness.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from commit_investigator.context.git_context import GitContextProvider
from commit_investigator.infra.llm import LLMMessage, LLMProvider, LLMResponse
from commit_investigator.pipeline.orchestrator import SuspectCommit

logger = logging.getLogger(__name__)

D3_JUDGE_PROMPT = """\
You are an expert code reviewer evaluating the quality of a bug attribution.

A bug attribution agent was given a bug report and searched a git repository
to find the commit that introduced the bug. You must evaluate how well the
agent's causal explanation connects the suspect commit to the reported bug.

## Bug Report

**{title}**

{description}

## Agent's Attribution

**Suspect commit:** `{commit_id}`
**Confidence:** {confidence}

**Causal mechanism:**
{mechanism}

**Evidence quotes:**
{evidence_quotes}

## Commit Diff

```
{diff}
```

## Scoring Rubric (0-4)

| Score | Label | Criteria |
|-------|-------|----------|
| 0 | Absent/wrong | No mechanism provided, or completely incorrect (wrong commit, wrong files, wrong behavior) |
| 1 | Vague | Generic explanation ("this commit changed something related") with no specific causal chain |
| 2 | Partial | Identifies the right area of code but wrong or incomplete causal chain (right file wrong function, right symptom wrong cause) |
| 3 | Sound | Correct causal chain linking the specific change to the reported symptom, but missing precise details (e.g. "removed null check" without specifying which method) |
| 4 | Precise | Complete "If <specific change> then <specific consequence matching symptom>" with code-level specificity |

## Instructions

1. Read the bug report to understand the reported problem.
2. Read the commit diff to understand what changed.
3. Read the agent's mechanism and evidence to understand its reasoning.
4. Score the mechanism 0-4 against the rubric above.

Output EXACTLY this JSON (nothing else):

```json
{{"score": <0-4>, "rationale": "<1-2 sentence justification>"}}
```"""

MAX_DIFF_CHARS = 6000


@dataclass
class D3Score:
    """D3 Attribution Quality score for a single suspect."""

    commit_id: str
    score: int
    rationale: str
    tokens_used: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_id": self.commit_id,
            "score": self.score,
            "rationale": self.rationale,
            "tokens_used": self.tokens_used,
            "cost_usd": self.cost_usd,
        }


@dataclass
class D3JudgeResult:
    """Aggregate D3 result for all suspects in a report."""

    scores: list[D3Score] = field(default_factory=list)
    avg_score: float = 0.0
    top_suspect_score: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "avg_score": round(self.avg_score, 3),
            "top_suspect_score": self.top_suspect_score,
            "total_tokens": self.total_tokens,
            "total_cost": round(self.total_cost, 6),
            "scores": [s.to_dict() for s in self.scores],
        }


def _truncate_diff(diff: str) -> str:
    """Truncate diff to fit in LLM context while keeping start and end."""
    if len(diff) <= MAX_DIFF_CHARS:
        return diff
    half = MAX_DIFF_CHARS // 2
    return (
        diff[:half]
        + f"\n\n... [{len(diff) - MAX_DIFF_CHARS} chars truncated] ...\n\n"
        + diff[-half:]
    )


def _format_evidence(quotes: list[str]) -> str:
    if not quotes:
        return "(none provided)"
    return "\n".join(f"- `{q}`" for q in quotes)


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)
_BARE_JSON_RE = re.compile(r'\{\s*"score"\s*:.*?\}', re.DOTALL)


def parse_judge_response(text: str) -> tuple[int, str]:
    """Extract score and rationale from judge LLM output.

    Returns (score, rationale). Falls back to (0, "parse_error: ...") on failure.
    """
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        match = _BARE_JSON_RE.search(text)

    if match:
        raw = match.group(1) if match.lastindex else match.group(0)
        try:
            data = json.loads(raw)
            score = int(data.get("score", 0))
            score = max(0, min(4, score))
            rationale = str(data.get("rationale", ""))
            return score, rationale
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    digits = re.findall(r"\b([0-4])\b", text)
    if digits:
        return int(digits[0]), f"Extracted from unstructured response: {text[:200]}"

    return 0, f"parse_error: could not extract score from: {text[:200]}"


def score_suspect_d3(
    suspect: SuspectCommit,
    title: str,
    description: str,
    diff: str | None,
    llm: LLMProvider,
) -> D3Score:
    """Score a single suspect's mechanism quality using LLM-as-judge."""
    if not suspect.mechanism or suspect.mechanism.strip() == "":
        return D3Score(
            commit_id=suspect.commit_id,
            score=0,
            rationale="No mechanism provided by agent.",
        )

    diff_text = _truncate_diff(diff) if diff else "(diff not available)"

    prompt = D3_JUDGE_PROMPT.format(
        title=title,
        description=description[:2000],
        commit_id=suspect.commit_id,
        confidence=suspect.confidence,
        mechanism=suspect.mechanism,
        evidence_quotes=_format_evidence(suspect.evidence_quotes),
        diff=diff_text,
    )

    response = llm.complete(
        messages=[LLMMessage(role="user", content=prompt)],
        temperature=0.0,
        max_tokens=256,
    )

    score, rationale = parse_judge_response(response.content)

    return D3Score(
        commit_id=suspect.commit_id,
        score=score,
        rationale=rationale,
        tokens_used=response.tokens_used,
        cost_usd=response.estimated_cost,
    )


def judge_attribution_d3(
    suspects: list[SuspectCommit],
    title: str,
    description: str,
    git_provider: GitContextProvider | None,
    llm: LLMProvider,
    max_suspects: int = 3,
) -> D3JudgeResult:
    """Score the top-k suspects from a report for D3 attribution quality.

    Only the top `max_suspects` are judged to limit LLM cost.
    """
    scored = suspects[:max_suspects]
    scores: list[D3Score] = []

    for suspect in scored:
        diff: str | None = None
        if git_provider and suspect.commit_id:
            try:
                diff = git_provider.get_diff(suspect.commit_id)
            except Exception:
                logger.warning("Could not fetch diff for %s", suspect.commit_id)

        d3 = score_suspect_d3(suspect, title, description, diff, llm)
        scores.append(d3)

    total_tokens = sum(s.tokens_used for s in scores)
    total_cost = sum(s.cost_usd for s in scores)
    avg = sum(s.score for s in scores) / len(scores) if scores else 0.0
    top_score = scores[0].score if scores else 0

    return D3JudgeResult(
        scores=scores,
        avg_score=avg,
        top_suspect_score=top_score,
        total_tokens=total_tokens,
        total_cost=total_cost,
    )
