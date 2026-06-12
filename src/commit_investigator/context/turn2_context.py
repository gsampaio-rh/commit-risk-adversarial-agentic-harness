"""Turn-2 follow-up context: truncated files and git blame for multi-turn investigation.

Builds structured user messages for turn 2 (no generic prompts, no oracle leakage).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from commit_investigator.context.smart_diff import (
    _BUILD_FILE_RE,
    _PRODUCTION_SOURCE_RE,
    _TEST_FILE_RE,
)

if TYPE_CHECKING:
    from commit_investigator.context.context_builder import InvestigationContext
    from commit_investigator.context.git_context import GitContextProvider

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
    re.MULTILINE,
)

_MAX_FILE_CHARS = 4_000
_MAX_BLAME_FILES = 2
_MAX_BLAME_CHARS = 1_500
_MAX_TURN2_CHARS = 8_000


@dataclass
class Turn2ContextBundle:
    """Structured turn-2 injection payload and audit metadata."""

    message: str
    truncated_files: list[str] = field(default_factory=list)
    blame_files: list[str] = field(default_factory=list)
    has_truncated_section: bool = False
    has_blame_section: bool = False


def build_turn2_follow_up(
    context: InvestigationContext,
    git_provider: GitContextProvider,
) -> Turn2ContextBundle:
    """Build turn-2 user message with truncated file content and/or git blame."""
    sections: list[str] = [
        "Additional context for your investigation. "
        "Revise your hypotheses using the evidence below.",
    ]
    truncated_paths: list[str] = []
    blame_paths: list[str] = []

    tm = context.truncation_metadata
    if tm and tm.truncated_files:
        file_blocks = _build_truncated_file_section(
            context.commit_id, tm.truncated_files, git_provider,
        )
        if file_blocks:
            sections.append("## Truncated Files")
            sections.extend(file_blocks)
            truncated_paths = list(tm.truncated_files)

    blame_blocks = _build_blame_section(context, git_provider)
    if blame_blocks:
        sections.append("## Git Blame")
        sections.extend(blame_blocks)
        blame_paths = _production_touched_files(context)

    message = "\n\n".join(sections)
    if len(message) > _MAX_TURN2_CHARS:
        message = message[:_MAX_TURN2_CHARS] + "\n\n... (turn-2 context truncated for budget)"
    return Turn2ContextBundle(
        message=message,
        truncated_files=truncated_paths,
        blame_files=blame_paths,
        has_truncated_section=bool(truncated_paths),
        has_blame_section=bool(blame_blocks),
    )


def _build_truncated_file_section(
    commit_id: str,
    paths: list[str],
    git_provider: GitContextProvider,
) -> list[str]:
    blocks: list[str] = []
    for path in paths:
        content = git_provider.get_file_at_commit(commit_id, path)
        if not content:
            continue
        if len(content) > _MAX_FILE_CHARS:
            content = content[:_MAX_FILE_CHARS] + f"\n... (truncated at {_MAX_FILE_CHARS} chars)"
        blocks.append(f"### {path}\n```\n{content.strip()}\n```")
    return blocks


def _build_blame_section(
    context: InvestigationContext,
    git_provider: GitContextProvider,
) -> list[str]:
    if not context.diff:
        return []

    line_ranges = parse_diff_touched_lines(context.diff)
    blocks: list[str] = []

    for path in _production_touched_files(context):
        lines = line_ranges.get(path) or line_ranges.get(_basename(path))
        if not lines:
            continue
        start, end = min(lines), max(lines)
        blame = git_provider.get_blame_snippet(
            context.commit_id, path, start, end, context_lines=2,
        )
        if not blame:
            continue
        snippet = blame.strip()
        if len(snippet) > _MAX_BLAME_CHARS:
            snippet = snippet[:_MAX_BLAME_CHARS] + "\n... (blame truncated)"
        blocks.append(f"### {path} (lines {start}-{end})\n```\n{snippet}\n```")
        if len(blocks) >= _MAX_BLAME_FILES:
            break

    return blocks


def parse_diff_touched_lines(diff: str) -> dict[str, list[int]]:
    """Map file paths to new-file line numbers touched by additions/context in the diff."""
    result: dict[str, list[int]] = {}
    current_file: str | None = None
    new_line = 0

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            current_file = _path_from_diff_git_line(line)
            continue
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            continue
        if current_file is None:
            continue

        hunk_match = _HUNK_HEADER_RE.match(line)
        if hunk_match:
            new_line = int(hunk_match.group(3))
            continue

        if line.startswith("+") and not line.startswith("+++"):
            result.setdefault(current_file, []).append(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith(" ") or line.startswith("\t"):
            result.setdefault(current_file, []).append(new_line)
            new_line += 1

    return result


def _production_touched_files(context: InvestigationContext) -> list[str]:
    """Return touched production source files in stable order."""
    paths: list[str] = []
    for path in context.touched_files:
        if _is_production_source(path):
            paths.append(path)
    return paths


def _is_production_source(path: str) -> bool:
    if _TEST_FILE_RE.search(path) or _BUILD_FILE_RE.search(path):
        return False
    return bool(_PRODUCTION_SOURCE_RE.search(path))


def _path_from_diff_git_line(line: str) -> str | None:
    parts = line.split()
    if len(parts) >= 4 and parts[2].startswith("a/") and parts[3].startswith("b/"):
        return parts[3][2:]
    return None


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]
