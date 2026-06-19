"""Prompt assembly for scoped investigation."""

from __future__ import annotations

import json
import re
from typing import Any

from commit_investigator.investigation.tools import ToolRegistry
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.investigation.result import Suspect
from commit_investigator.narrowing.models import TriagedCandidate, TriageResult


def _format_tool_descriptions(registry: ToolRegistry) -> str:
    return "\n".join(
        f"- **{t['function']['name']}**: {t['function']['description']}"
        for t in registry.to_openai_tools()
    )


def _format_candidates(
    candidates: list[TriagedCandidate], *, use_tier_rank: bool = False
) -> str:
    """Format a list of TriagedCandidates for prompt inclusion.

    Args:
        use_tier_rank: If True, use c.tier_rank for numbering (Phase 2).
                       If False, use sequential 1-based index (Phase 2b).
    """
    lines: list[str] = []
    for i, c in enumerate(candidates, 1):
        rank = c.tier_rank if use_tier_rank else i
        files = ", ".join(c.files_changed[:5]) if c.files_changed else "(no files)"
        lines.append(
            f"  {rank}. `{c.commit_id}` — \"{c.summary}\" "
            f"[pre_score={c.pre_score:.3f}, file_overlap={c.file_overlap:.2f}] "
            f"[{c.retrieval_signal}] [{c.date}]\n     files: {files}"
        )
    return "\n".join(lines)


def _tools_section(registry: ToolRegistry) -> str:
    """Shared Tools section with invocation format."""
    tools = _format_tool_descriptions(registry)
    return (
        "## Tools\n"
        "To invoke a tool, output a fenced block:\n"
        "```tool\n"
        '{"tool": "<name>", "args": {<arguments>}}\n'
        "```\n\n"
        f"{tools}\n\n"
    )


def _output_format_section(rank_instruction: str = "Rank 3-5 suspects by confidence.") -> str:
    """Shared Output Format section."""
    return (
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
        f"{rank_instruction} Use ONLY full SHAs from the candidate list.\n\n"
    )


def _bug_report_section(problem: ProblemStatement) -> str:
    """Shared Bug Report section."""
    return (
        f"## Bug Report\n"
        f"**Title:** {problem.title}\n\n"
        f"**Description:**\n{problem.description[:3000]}\n\n"
    )


def build_phase2_system_prompt(
    problem: ProblemStatement,
    triage: TriageResult,
    registry: ToolRegistry,
) -> str:
    """Build V4.2 Phase 2 system prompt — must-examine only (3 SHAs)."""
    must_examine = _format_candidates(triage.must_examine, use_tier_rank=True)
    must_examine_shas = ", ".join(
        f"`{c.commit_id[:12]}`" for c in triage.must_examine
    )

    return (
        "You are a bug attribution agent. Your task: examine candidate commits "
        "to find which one INTRODUCED the bug described below.\n\n"
        + _tools_section(registry)
        + "## Strategy\n"
        f"You MUST call `get_commit_diff` on EVERY must-examine commit "
        f"({must_examine_shas}) before concluding. Do NOT skip any.\n"
        "1. Call `get_commit_diff` on each must-examine SHA — ALL of them\n"
        "2. For each diff, assess whether the change could CAUSE the symptoms\n"
        "3. Use `get_blame` or `get_file_at_commit` for deeper analysis if needed\n"
        "4. After examining ALL candidates, compare the evidence:\n"
        "   - Which commit changed the specific code path mentioned in the bug?\n"
        "   - Which commit's timing aligns with when the bug was introduced?\n"
        "   - Prefer commits with direct causal links over circumstantial matches\n"
        "5. Conclude with a `suspects` block ranking by strength of causal evidence\n\n"
        + _output_format_section("Rank 3-5 suspects by confidence.")
        + _bug_report_section(problem)
        + f"## Must-Examine Candidates ({len(triage.must_examine)} commits)\n"
        "These are the highest-priority candidates — examine ALL of them:\n"
        f"{must_examine}"
    )


def _reference_suspect_section(best_suspect: Suspect | None) -> str:
    """Build the Phase 2 best-suspect reference section (empty if no suspect)."""
    if not best_suspect or not best_suspect.commit_id:
        return ""
    return (
        f"\n## Phase 2 Best Suspect (reference)\n"
        f"Previous investigation found: `{best_suspect.commit_id[:12]}` "
        f"(confidence={best_suspect.confidence:.2f})\n"
        f"Mechanism: {best_suspect.mechanism}\n"
        f"Compare your findings against this suspect.\n"
    )


def build_phase2b_system_prompt(
    problem: ProblemStatement,
    triage: TriageResult,
    best_suspect: Suspect | None,
    registry: ToolRegistry,
) -> str:
    """Build V4.2 Phase 2b system prompt — watchlist candidates + P2 reference."""
    watchlist = _format_candidates(triage.watchlist, use_tier_rank=False)
    watchlist_shas = ", ".join(
        f"`{c.commit_id[:12]}`" for c in triage.watchlist
    )

    return (
        "You are a bug attribution agent. A prior investigation examined "
        "high-priority candidates but produced weak results. Your task: "
        "examine these WATCHLIST candidates to find which one INTRODUCED "
        "the bug described below.\n\n"
        + _tools_section(registry)
        + "## Strategy\n"
        f"You MUST call `get_commit_diff` on EVERY watchlist commit "
        f"({watchlist_shas}) before concluding. Do NOT skip any.\n"
        "1. Call `get_commit_diff` on EVERY watchlist SHA — examine ALL of them\n"
        "2. For each diff, assess whether the change could CAUSE the symptoms\n"
        "3. Use `get_blame` or `get_file_at_commit` for deeper analysis if needed\n"
        "4. After examining ALL candidates, compare the evidence:\n"
        "   - Which commit changed the specific code path mentioned in the bug?\n"
        "   - Which commit's timing aligns with when the bug was introduced?\n"
        "   - Prefer commits with direct causal links over circumstantial matches\n"
        "5. Conclude with a `suspects` block ranking by strength of causal evidence\n\n"
        + _output_format_section("Rank suspects by confidence.")
        + _bug_report_section(problem)
        + f"## Watchlist Candidates ({len(triage.watchlist)} commits)\n"
        "You MUST examine ALL of these before concluding:\n"
        f"{watchlist}"
        + _reference_suspect_section(best_suspect)
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
