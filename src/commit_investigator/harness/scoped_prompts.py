"""Prompt assembly for scoped investigation."""

from __future__ import annotations

import json
import re
from typing import Any

from commit_investigator.agent.tools import ToolRegistry
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.models.candidates import CandidateSet


def build_scoped_system_prompt(
    problem: ProblemStatement, candidate_set: CandidateSet, registry: ToolRegistry
) -> str:
    """Build the system prompt for scoped multi-turn investigation."""
    tools = "\n".join(
        f"**{t['function']['name']}**: {t['function']['description']}"
        for t in registry.to_openai_tools()
    )
    commits = sorted(candidate_set.commits, key=lambda c: c.rank)[:20]
    cands = "\n".join(
        f'{c.rank}. {c.commit_id} — "{c.summary}" [{c.retrieval_signal}] [{c.date}] '
        f'files: {", ".join(c.files_changed[:5])}'
        for c in commits
    )
    return (
        "You are a bug attribution agent. Examine candidate commits to find which one "
        "INTRODUCED the bug.\n\n"
        f"## Tools\nTo invoke: ```tool\\n{{\"tool\": \"<name>\", \"args\": {{...}}}}\\n```\n\n{tools}\n\n"
        "## Strategy\n1. Examine top-ranked candidates with get_commit_diff\n"
        "2. Look for changes that could CAUSE the described symptoms\n"
        "3. Conclude with ```suspects when you have evidence\n\n"
        "## Output\nWhen done:\n```suspects\n"
        '[{"commit_id": "<full SHA>", "confidence": 0.8, "mechanism": "...", "evidence_quotes": ["..."]}]\n'
        "```\nRank 3-5 suspects. Use ONLY SHAs from the candidate list. "
        "Examine at least one diff before concluding.\n\n"
        f"## Bug Report\n**Title:** {problem.title}\n\n"
        f"**Description:**\n{problem.description[:3000]}\n\n"
        f"## Candidates (top {len(commits)} of {len(candidate_set.commits)})\n{cands}"
    )


_TOOL_CALL_RE = re.compile(r"```tool\s*\n\s*(\{[^`]+?\})\s*\n\s*```", re.DOTALL)
_SUSPECTS_RE = re.compile(r"```suspects\s*\n\s*(\[[\s\S]+?\])\s*\n\s*```", re.DOTALL)


def parse_tool_calls(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for match in _TOOL_CALL_RE.finditer(text):
        try:
            payload = json.loads(match.group(1))
            if "tool" in payload:
                out.append(payload)
        except json.JSONDecodeError:
            continue
    return out


def parse_suspects(text: str) -> list[dict[str, Any]]:
    match = _SUSPECTS_RE.search(text)
    if not match:
        return []
    try:
        raw = json.loads(match.group(1))
        return raw if isinstance(raw, list) else []
    except (json.JSONDecodeError, TypeError):
        return []
