"""Cursor SDK native investigator.

Uses the Cursor SDK Agent's built-in tools (shell, file read) to investigate
bugs directly — the Cursor agent runs git commands in the repo instead of
our text-based tool loop.

Architecture:
  - Agent.prompt() with cwd=repo_path
  - Agent uses shell to run git log, blame, diff, show
  - Temporal bound enforced via prompt instructions + git log range limits
  - Agent returns suspects in JSON format
  - We parse suspects and assemble BugAttributionReport
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from commit_investigator.infra.git_context import GitContextProvider
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.agent.orchestrator import (
    BugAttributionReport,
    SuspectCommit,
    ToolCallRecord,
    _attach_evidence_scores,
)

logger = logging.getLogger(__name__)

_CURSOR_INVESTIGATION_PROMPT = """\
You are a bug attribution agent. You are investigating a git repository to find
the commit that most likely INTRODUCED a reported bug.

## CRITICAL: Temporal Boundary

You MUST NOT examine any commit at or after: {temporal_bound_commit}
All git log commands must be limited to: git log {temporal_bound_ref} ...
All git blame commands must use: git blame {temporal_bound_ref} -- <file>

## Bug Report

**{title}**

{description}

## Instructions

1. You are in the git repository for the "{project}" project.
2. Use shell commands to search the git history for the bug-introducing commit.
3. Start broad: search for files/classes mentioned in the bug report.
4. Examine diffs of candidate commits to find the one that introduced the bug.
5. You have a budget of ~25 shell commands. Be efficient.

## Useful git commands

- `git log {temporal_bound_ref} --oneline --all -n 20 -- <path>` — commits touching a file
- `git log {temporal_bound_ref} --oneline --all --grep="<keyword>" -n 10` — search commit messages
- `git log {temporal_bound_ref} --oneline -n 20` — recent commits
- `git show <commit> --stat` — files changed in a commit
- `git show <commit> -- <file>` — diff for specific file in commit
- `git diff <commit>^..<commit>` — full diff for a commit
- `git blame {temporal_bound_ref} -- <file> | head -50` — blame at temporal bound
- `git log {temporal_bound_ref} --oneline --all -S "<string>" -n 10` — commits adding/removing a string

## Output Format

When you have identified your suspects, output EXACTLY this JSON block at the
end of your response (this is critical — I will parse it programmatically):

```json
{{
  "suspects": [
    {{
      "commit_id": "<full 40-char SHA>",
      "confidence": 0.8,
      "mechanism": "If <specific change> then <specific consequence>",
      "evidence_quotes": ["exact text from the diff"]
    }}
  ],
  "reasoning_summary": "Brief summary of your investigation"
}}
```

Rank suspects by confidence (highest first). Include 1-5 suspects.
For commit_id, ALWAYS use the full 40-character SHA (use `git rev-parse <short>` if needed).
"""


_SUSPECTS_JSON_PATTERN = re.compile(
    r"```json\s*\n\s*(\{[\s\S]+?\})\s*\n\s*```",
    re.DOTALL,
)

_SUSPECTS_ARRAY_PATTERN = re.compile(
    r'"suspects"\s*:\s*(\[[\s\S]+?\])',
    re.DOTALL,
)


def _parse_cursor_response(text: str) -> tuple[list[SuspectCommit], str]:
    """Parse suspects and reasoning from the Cursor agent's response."""
    json_match = _SUSPECTS_JSON_PATTERN.search(text)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            suspects_raw = data.get("suspects", [])
            reasoning = data.get("reasoning_summary", "")
            suspects = _raw_to_suspects(suspects_raw)
            return suspects, reasoning
        except (json.JSONDecodeError, KeyError):
            pass

    array_match = _SUSPECTS_ARRAY_PATTERN.search(text)
    if array_match:
        try:
            suspects_raw = json.loads(array_match.group(1))
            suspects = _raw_to_suspects(suspects_raw)
            return suspects, ""
        except json.JSONDecodeError:
            pass

    return [], ""


def _raw_to_suspects(raw: list[dict]) -> list[SuspectCommit]:
    """Convert raw JSON array to SuspectCommit list."""
    suspects = []
    for i, item in enumerate(raw):
        cid = item.get("commit_id", "")
        if not cid or len(cid) < 7:
            continue
        suspects.append(SuspectCommit(
            commit_id=cid,
            rank=i + 1,
            confidence=float(item.get("confidence", 0.0)),
            mechanism=item.get("mechanism", ""),
            evidence_quotes=item.get("evidence_quotes", []),
        ))
    return suspects


@dataclass
class CursorSDKInvestigator:
    """Investigator that uses Cursor SDK Agent's native tools.

    Instead of our text-based tool loop, the Cursor agent runs git commands
    directly in the repository via its shell tool.
    """

    api_key: str = ""
    model: str = "claude-sonnet-4-6"

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("CURSOR_API_KEY", "")
        if not self.api_key:
            raise ValueError("CURSOR_API_KEY required for CursorSDKInvestigator")

    max_retries: int = 3
    retry_cooldown_s: float = 30.0
    inter_case_cooldown_s: float = 10.0

    def investigate(
        self,
        problem: ProblemStatement,
        git_provider: GitContextProvider,
    ) -> BugAttributionReport:
        """Run investigation using Cursor SDK Agent with retry logic."""
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions, close_default_client

        repo_path = str(git_provider.repo_path)
        temporal_bound = git_provider.temporal_bound or "HEAD"

        prompt = _CURSOR_INVESTIGATION_PROMPT.format(
            temporal_bound_commit=temporal_bound,
            temporal_bound_ref=temporal_bound,
            title=problem.title,
            description=problem.description,
            project=problem.project,
        )

        start_time = time.time()
        last_error = ""

        result = None
        for attempt in range(self.max_retries):
            if attempt > 0:
                wait = self.retry_cooldown_s * (2 ** (attempt - 1))
                logger.info("Retry %d/%d after %.0fs cooldown...",
                            attempt + 1, self.max_retries, wait)
                time.sleep(wait)

            try:
                result = Agent.prompt(
                    prompt,
                    AgentOptions(
                        api_key=self.api_key,
                        model=self.model,
                        local=LocalAgentOptions(cwd=repo_path),
                    ),
                )
                break
            except Exception as e:
                last_error = str(e)
                logger.warning("Cursor SDK attempt %d failed: %s", attempt + 1, e)
                self._cleanup_client(close_default_client)

        self._cleanup_client(close_default_client)
        time.sleep(self.inter_case_cooldown_s)

        if result is None:
            logger.error("All %d attempts failed", self.max_retries)
            return self._empty_report(problem, temporal_bound, start_time, last_error)

        elapsed_ms = (time.time() - start_time) * 1000
        response_text = result.result or ""

        suspects, reasoning = _parse_cursor_response(response_text)
        if not reasoning:
            reasoning = response_text[:2000]

        word_count = len(response_text.split())
        token_estimate = word_count + len(prompt.split())

        evidence_scores = _attach_evidence_scores(suspects, git_provider)

        return BugAttributionReport(
            problem_title=problem.title,
            problem_description=problem.description,
            suspects=suspects,
            reasoning_summary=reasoning,
            tool_trace=[],
            metadata={
                "turns_used": 1,
                "tool_calls": 0,
                "tokens_used": token_estimate,
                "total_cost_usd": token_estimate * 0.000003,
                "elapsed_ms": elapsed_ms,
                "temporal_bound": temporal_bound,
                "model": f"cursor-sdk/{self.model}",
                "budget_exceeded": False,
                "evidence_scores": evidence_scores,
                "evidence_scoring_applied": True,
                "post_processing_applied": False,
                "investigator": "cursor-sdk-native",
            },
        )

    @staticmethod
    def _cleanup_client(close_fn: Any) -> None:
        """Safely close the default SDK client to free bridge resources."""
        try:
            close_fn()
        except Exception:
            pass

    def _empty_report(
        self,
        problem: ProblemStatement,
        temporal_bound: str,
        start_time: float,
        error: str,
    ) -> BugAttributionReport:
        elapsed_ms = (time.time() - start_time) * 1000
        return BugAttributionReport(
            problem_title=problem.title,
            problem_description=problem.description,
            suspects=[],
            reasoning_summary=f"Investigation failed: {error}",
            tool_trace=[],
            metadata={
                "turns_used": 0,
                "tool_calls": 0,
                "tokens_used": 0,
                "total_cost_usd": 0.0,
                "elapsed_ms": elapsed_ms,
                "temporal_bound": temporal_bound,
                "model": f"cursor-sdk/{self.model}",
                "budget_exceeded": False,
                "evidence_scores": [],
                "evidence_scoring_applied": True,
                "post_processing_applied": False,
                "investigator": "cursor-sdk-native",
                "error": error,
            },
        )
