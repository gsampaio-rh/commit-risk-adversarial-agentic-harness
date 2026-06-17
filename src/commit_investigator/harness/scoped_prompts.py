"""Prompt assembly for scoped investigation."""

from __future__ import annotations

import json
import re
from typing import Any

from commit_investigator.agent.tools import ToolRegistry
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.models.candidates import CandidateSet
from commit_investigator.narrowing.models import TriageResult


def build_scoped_system_prompt(
    problem: ProblemStatement, candidate_set: CandidateSet, registry: ToolRegistry
) -> str:
    """Build V4.1 system prompt (20 candidates). Preserved for baseline."""
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


def _format_tool_descriptions(registry: ToolRegistry) -> str:
    return "\n".join(
        f"- **{t['function']['name']}**: {t['function']['description']}"
        for t in registry.to_openai_tools()
    )


def _format_must_examine(triage: TriageResult) -> str:
    lines: list[str] = []
    for c in triage.must_examine:
        files = ", ".join(c.files_changed[:5]) if c.files_changed else "(no files)"
        lines.append(
            f"  {c.tier_rank}. `{c.commit_id}` — \"{c.summary}\" "
            f"[pre_score={c.pre_score:.3f}, file_overlap={c.file_overlap:.2f}] "
            f"[{c.retrieval_signal}] [{c.date}]\n     files: {files}"
        )
    return "\n".join(lines)


def build_phase2_system_prompt(
    problem: ProblemStatement,
    triage: TriageResult,
    registry: ToolRegistry,
) -> str:
    """Build V4.2 Phase 2 system prompt — must-examine only (3 SHAs).

    The prompt focuses the LLM on the 3 must-examine candidates from
    deterministic triage. Tool scope (CandidateSet) is enforced separately
    by build_scoped_tools().
    """
    tools = _format_tool_descriptions(registry)
    must_examine = _format_must_examine(triage)
    must_examine_shas = ", ".join(
        f"`{c.commit_id[:12]}`" for c in triage.must_examine
    )

    return (
        "You are a bug attribution agent. Your task: examine candidate commits "
        "to find which one INTRODUCED the bug described below.\n\n"

        "## Tools\n"
        "To invoke a tool, output a fenced block:\n"
        "```tool\n"
        '{"tool": "<name>", "args": {<arguments>}}\n'
        "```\n\n"
        f"{tools}\n\n"

        "## Strategy\n"
        f"You MUST examine each must-examine commit ({must_examine_shas}) "
        "with `get_commit_diff` before concluding.\n"
        "1. Call `get_commit_diff` on each must-examine SHA\n"
        "2. For each diff, assess whether the change could CAUSE the symptoms\n"
        "3. Use `get_blame` or `get_file_at_commit` for deeper analysis if needed\n"
        "4. Conclude with a `suspects` block when you have evidence\n\n"

        "## Output Format\n"
        "When ready to conclude:\n"
        "```suspects\n"
        "[{\n"
        '  "commit_id": "<full 40-char SHA>",\n'
        '  "confidence": 0.85,\n'
        '  "mechanism": "Explain HOW this commit caused the bug",\n'
        '  "evidence_quotes": ["relevant diff lines or blame output"]\n'
        "}]\n"
        "```\n"
        "Rank 3-5 suspects by confidence. Use ONLY full SHAs from the candidate list.\n\n"

        f"## Bug Report\n"
        f"**Title:** {problem.title}\n\n"
        f"**Description:**\n{problem.description[:3000]}\n\n"

        f"## Must-Examine Candidates ({len(triage.must_examine)} commits)\n"
        "These are the highest-priority candidates — examine ALL of them:\n"
        f"{must_examine}"
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
