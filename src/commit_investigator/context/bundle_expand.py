"""Optional test-adjacency and blame context expansion for turn-1 diff bundles."""

from __future__ import annotations

import re

from commit_investigator.context.git_context import GitContextProvider
from commit_investigator.context.smart_diff import (
    FileDiff,
    _BUILD_FILE_RE,
    _PRODUCTION_SOURCE_RE,
    _TEST_FILE_RE,
    _file_rank,
    parse_file_diffs,
)
from commit_investigator.context.turn2_context import parse_diff_touched_lines

EXPANSION_RESERVED_CHARS = 2500
_MAX_BLAME_FILES = 2
_BLAME_CONTEXT_LINES = 2

_JAVA_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_JAVA_KEYWORDS = frozenset({
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
    "class", "const", "continue", "default", "do", "double", "else", "enum",
    "extends", "final", "finally", "float", "for", "goto", "if", "implements",
    "import", "instanceof", "int", "interface", "long", "native", "new", "null",
    "package", "private", "protected", "public", "return", "short", "static",
    "strictfp", "super", "switch", "synchronized", "this", "throw", "throws",
    "transient", "try", "void", "volatile", "while", "true", "false",
})


def extract_java_identifiers(text: str) -> set[str]:
    """Return Java-like identifier tokens (length≥3, not keyword)."""
    tokens: set[str] = set()
    for match in _JAVA_IDENT_RE.finditer(text):
        token = match.group(0)
        if len(token) >= 3 and token not in _JAVA_KEYWORDS:
            tokens.add(token)
    return tokens


def hunks_share_identifier(production_hunk: str, test_hunk: str) -> bool:
    """True when production and test hunks share at least one identifier token."""
    prod_ids = extract_java_identifiers(production_hunk)
    test_ids = extract_java_identifiers(test_hunk)
    return bool(prod_ids & test_ids)


def _is_test_path(path: str) -> bool:
    return bool(_TEST_FILE_RE.search(path))


def _is_production_path(path: str) -> bool:
    if _TEST_FILE_RE.search(path) or _BUILD_FILE_RE.search(path):
        return False
    return bool(_PRODUCTION_SOURCE_RE.search(path))


def _defect_signal_production_paths(raw_diff: str, max_files: int = _MAX_BLAME_FILES) -> list[str]:
    """Production paths ranked defect-signal first, then other production."""
    file_diffs = parse_file_diffs(raw_diff)
    production = [fd for fd in file_diffs if _is_production_path(fd.path)]
    production.sort(
        key=lambda fd: (
            _file_rank(fd.path, fd.header + "".join(fd.hunks)),
            fd.path,
        ),
    )
    return [fd.path for fd in production[:max_files]]


def build_test_adjacency_section(raw_diff: str) -> tuple[str, bool]:
    """Build ## Test Adjacency block from commit-diff test hunks paired by identifier overlap.

    Returns (section_text, no_pairs) where no_pairs is True when no section was emitted.
    """
    file_diffs = parse_file_diffs(raw_diff)
    production = [fd for fd in file_diffs if _is_production_path(fd.path)]
    tests = [fd for fd in file_diffs if _is_test_path(fd.path)]
    if not production or not tests:
        return "", True

    prod_hunks = [
        hunk for fd in production for hunk in fd.hunks if hunk.strip()
    ]
    if not prod_hunks:
        return "", True

    blocks: list[str] = []
    for test_fd in tests:
        paired_hunks = [
            hunk for hunk in test_fd.hunks
            if hunk.strip()
            and any(hunks_share_identifier(prod, hunk) for prod in prod_hunks)
        ]
        if not paired_hunks:
            continue
        body = test_fd.header + "".join(paired_hunks)
        blocks.append(body.rstrip())

    if not blocks:
        return "", True

    section = "## Test Adjacency\n\n" + "\n\n".join(blocks)
    return section, False


def build_blame_section(
    commit_id: str,
    raw_diff: str,
    git_provider: GitContextProvider,
) -> str:
    """Build ## Git Blame block for up to 2 defect-signal-tier production files."""
    line_ranges = parse_diff_touched_lines(raw_diff)
    blocks: list[str] = []

    for path in _defect_signal_production_paths(raw_diff):
        lines = line_ranges.get(path)
        if not lines:
            basename = path.rsplit("/", 1)[-1]
            lines = line_ranges.get(basename)
        if not lines:
            continue

        start, end = min(lines), max(lines)
        blame = git_provider.get_blame_snippet(
            commit_id,
            path,
            start,
            end,
            context_lines=_BLAME_CONTEXT_LINES,
        )
        if not blame:
            continue
        blocks.append(
            f"### {path} (lines {start}-{end})\n```\n{blame.strip()}\n```",
        )
        if len(blocks) >= _MAX_BLAME_FILES:
            break

    if not blocks:
        return ""
    return "## Git Blame\n\n" + "\n\n".join(blocks)


def append_context_expansion(
    base_diff: str,
    raw_diff: str | None,
    commit_id: str,
    git_provider: GitContextProvider,
    *,
    include_test_adjacency: bool,
    include_blame_snippets: bool,
    max_diff_chars: int,
    missing_reasons: list[str],
) -> str:
    """Append expansion sections to assembled diff within remaining char budget."""
    if raw_diff is None:
        return base_diff

    sections: list[str] = []
    if include_test_adjacency:
        adjacency, no_pairs = build_test_adjacency_section(raw_diff)
        if adjacency:
            sections.append(adjacency)
        elif no_pairs:
            missing_reasons.append("test_adjacency: no paired hunks in diff")

    if include_blame_snippets:
        blame = build_blame_section(commit_id, raw_diff, git_provider)
        if blame:
            sections.append(blame)

    if not sections:
        return base_diff

    expansion = "\n\n".join(sections)
    remaining = max(0, max_diff_chars - len(base_diff))
    if remaining <= 0:
        return base_diff

    if len(expansion) > remaining:
        expansion = expansion[:remaining]
        if not expansion.endswith("\n"):
            expansion = expansion.rstrip()
        expansion = f"{expansion}\n... (expansion truncated for budget)"

    separator = "" if base_diff.endswith("\n") or not base_diff else "\n\n"
    return f"{base_diff}{separator}{expansion}"
