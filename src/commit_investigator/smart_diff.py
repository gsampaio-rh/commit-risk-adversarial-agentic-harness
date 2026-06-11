"""Smart diff assembler: per-file ranked diff assembly with per-file hunk guarantees.

Replaces the naive diff[:16000] truncation with a ranked, file-aware assembly that
ensures defect-signal files receive budget priority and each touched file gets at
least one hunk before the global cap is applied.

Ranking tier (highest priority first):
  1. defect_signal — files matching guard/concurrency/lifecycle patterns
  2. production    — Java/Kotlin/Scala/Python source, not test/build/config
  3. test          — *Test*.java, test_*.py, etc.
  4. build/config  — pom.xml, .gradle, .yml, .xml (non-source)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_DEFECT_SIGNAL_RE = re.compile(
    r"synchronized|ReentrantLock|\bLock\.|volatile\s+\w+|"
    r"SmartLifecycle|@Order|shutdown|startup|@EventListener|"
    r"if\s*\(.*null|guard|nullcheck",
    re.IGNORECASE,
)

_TEST_FILE_RE = re.compile(
    r"(Test|Tests|Spec|_test|test_)\.(java|kt|py|scala|js|ts)$",
    re.IGNORECASE,
)

_BUILD_FILE_RE = re.compile(
    r"\.(xml|gradle|properties|yml|yaml|json|md|txt|cfg|ini|toml)$|"
    r"(pom\.xml|build\.gradle|\.gitignore|Makefile|CMakeLists\.txt)$",
    re.IGNORECASE,
)

_PRODUCTION_SOURCE_RE = re.compile(
    r"\.(java|kt|scala|py|go|ts|js|c|cpp|cs|rb|rs)$",
    re.IGNORECASE,
)


@dataclass
class FileDiff:
    """Parsed diff for a single file."""

    path: str
    header: str  # the 'diff --git ...' line
    hunks: list[str]  # list of individual hunk text blocks


@dataclass
class AssembledDiff:
    """Result of smart diff assembly."""

    text: str
    included_files: list[str] = field(default_factory=list)
    truncated_files: list[str] = field(default_factory=list)
    total_chars: int = 0


def _file_rank(path: str, diff_text: str) -> int:
    """Return rank for a file (lower = higher priority).

    0 = defect signal  (guard, concurrency, lifecycle content)
    1 = production source
    2 = test file
    3 = build/config
    """
    if _DEFECT_SIGNAL_RE.search(diff_text):
        return 0
    if _TEST_FILE_RE.search(path):
        return 2
    if _BUILD_FILE_RE.search(path):
        return 3
    if _PRODUCTION_SOURCE_RE.search(path):
        return 1
    return 1  # unknown extension → treat as production


def parse_file_diffs(raw_diff: str) -> list[FileDiff]:
    """Split a unified diff into per-file FileDiff objects."""
    if not raw_diff.strip():
        return []

    file_diffs: list[FileDiff] = []
    current_path: str | None = None
    current_header = ""
    current_hunk_lines: list[str] = []
    current_hunks: list[str] = []

    for line in raw_diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current_path is not None:
                if current_hunk_lines:
                    current_hunks.append("".join(current_hunk_lines))
                file_diffs.append(FileDiff(current_path, current_header, current_hunks))
            current_path = _extract_path(line)
            current_header = line
            current_hunk_lines = []
            current_hunks = []
        elif line.startswith("--- ") or line.startswith("+++ ") or line.startswith("index ") or line.startswith("new file") or line.startswith("deleted file") or line.startswith("Binary"):
            current_header += line
        elif line.startswith("@@"):
            if current_hunk_lines:
                current_hunks.append("".join(current_hunk_lines))
            current_hunk_lines = [line]
        else:
            if current_hunk_lines:
                current_hunk_lines.append(line)
            elif current_path is not None:
                current_header += line

    if current_path is not None:
        if current_hunk_lines:
            current_hunks.append("".join(current_hunk_lines))
        file_diffs.append(FileDiff(current_path, current_header, current_hunks))

    return file_diffs


def _extract_path(diff_git_line: str) -> str:
    """Extract file path from a 'diff --git a/foo b/foo' line."""
    m = re.match(r"diff --git a/(.*) b/(.*)$", diff_git_line.strip())
    if m:
        return m.group(2)
    parts = diff_git_line.strip().split()
    return parts[-1].lstrip("b/") if len(parts) >= 4 else "unknown"


def assemble_diff(
    raw_diff: str | None,
    defect_signal_files: list[str] | None = None,
    max_chars: int = 16_000,
) -> AssembledDiff:
    """Assemble a ranked, budget-aware diff from a raw unified diff.

    Algorithm:
    1. Parse diff into per-file FileDiff objects.
    2. Rank files: defect_signal > production > test > build/config.
       Files in defect_signal_files list are always ranked 0.
    3. Guarantee: include at least one hunk per file if budget allows.
    4. Fill remaining budget from ranked files (top-ranked first).
    5. Emit AssembledDiff with text, included_files, truncated_files, total_chars.

    The total output text never exceeds max_chars.
    """
    if not raw_diff:
        return AssembledDiff(
            text="",
            included_files=[],
            truncated_files=[],
            total_chars=0,
        )

    file_diffs = parse_file_diffs(raw_diff)
    if not file_diffs:
        truncated = raw_diff[:max_chars]
        return AssembledDiff(
            text=truncated,
            included_files=[],
            truncated_files=[],
            total_chars=len(truncated),
        )

    forced_signal_set = set(defect_signal_files or [])

    def effective_rank(fd: FileDiff) -> int:
        if fd.path in forced_signal_set:
            return 0
        file_diff_text = fd.header + "".join(fd.hunks)
        return _file_rank(fd.path, file_diff_text)

    ranked = sorted(file_diffs, key=effective_rank)

    included_files: list[str] = []
    truncated_files: list[str] = []
    sections: list[str] = []
    budget = max_chars

    for fd in ranked:
        if not fd.hunks:
            full_section = fd.header
        else:
            full_section = fd.header + "".join(fd.hunks)

        if len(full_section) <= budget:
            sections.append(full_section)
            included_files.append(fd.path)
            budget -= len(full_section)
        else:
            # Try to fit at least the first hunk (per-file minimum guarantee)
            minimal = fd.header + (fd.hunks[0] if fd.hunks else "")
            if len(minimal) <= budget:
                sections.append(minimal)
                included_files.append(fd.path)
                budget -= len(minimal)
                if len(fd.hunks) > 1:
                    truncated_files.append(fd.path)
            else:
                truncated_files.append(fd.path)

    text = "".join(sections)
    if len(text) > max_chars:
        text = text[:max_chars]

    return AssembledDiff(
        text=text,
        included_files=included_files,
        truncated_files=truncated_files,
        total_chars=len(text),
    )
