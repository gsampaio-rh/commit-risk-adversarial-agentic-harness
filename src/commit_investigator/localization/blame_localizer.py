"""Reverse SZZ blame localizer: fix diff → blame → candidate SHAs.

Given a fix commit, identifies the code lines that were changed (the buggy code),
then runs git blame at fix_hash~1 to find the commits that last touched those
lines — the likely bug introducers.

This module uses subprocess directly rather than GitContextProvider because it
operates on fix_hash (beyond the temporal bound the agent sees).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from commit_investigator.retrieval.strategies import StrategyHit

STRATEGY_NAME = "localization_blame"

_SOURCE_EXTENSIONS = frozenset({
    ".java", ".groovy", ".scala", ".py", ".sh", ".kt", ".rs", ".go",
    ".js", ".ts", ".tsx", ".jsx", ".c", ".cpp", ".h", ".hpp",
})

_EXCLUDED_FILENAMES = frozenset({
    "CHANGES.txt", "CHANGELOG.md", "RELEASE_NOTES.md",
    "pom.xml", "build.gradle", "build.sbt",
})

_SHA_RE = re.compile(r"^([0-9a-f]{40})\s", re.MULTILINE)
_HUNK_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+")


def localize_via_fix_diff(
    repo_path: str | Path,
    fix_hash: str,
    *,
    extensions: frozenset[str] | None = None,
) -> list[StrategyHit]:
    """Run reverse SZZ: diff the fix commit, blame changed lines, return hits.

    Args:
        repo_path: Path to the git repository.
        fix_hash: The fix commit SHA.
        extensions: Source file extensions to include (default: _SOURCE_EXTENSIONS).

    Returns:
        StrategyHit objects with strategy='localization_blame', one per unique
        blamed SHA. Returns empty list if fix_hash is invalid or no source
        files are in the fix diff.
    """
    repo = Path(repo_path)
    exts = extensions or _SOURCE_EXTENSIONS

    changed_files = _get_fix_changed_files(repo, fix_hash)
    if not changed_files:
        return []

    source_files = _filter_source_files(changed_files, exts)
    if not source_files:
        return []

    all_blame_shas: dict[str, int] = {}

    for filepath in source_files:
        hunks = _get_old_side_hunks(repo, fix_hash, filepath)
        if not hunks:
            continue

        for start, end in hunks:
            shas = _blame_lines(repo, f"{fix_hash}~1", filepath, start, end)
            for rank_offset, sha in enumerate(shas):
                if sha not in all_blame_shas:
                    all_blame_shas[sha] = rank_offset + 1

    return [
        StrategyHit(
            commit_id=sha,
            strategy=STRATEGY_NAME,
            rank=rank,
        )
        for sha, rank in sorted(all_blame_shas.items(), key=lambda x: x[1])
    ]


def _run_git(repo: Path, *args: str, timeout: int = 30) -> str:
    """Run a git command and return stdout. Returns empty string on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return ""
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _get_fix_changed_files(repo: Path, fix_hash: str) -> list[str]:
    """Get files changed by the fix commit via diff-tree."""
    output = _run_git(repo, "diff-tree", "--no-commit-id", "-r", "--name-only", fix_hash)
    return [f.strip() for f in output.strip().split("\n") if f.strip()]


def _filter_source_files(files: list[str], extensions: frozenset[str]) -> list[str]:
    """Keep only source files by extension, excluding known noise files."""
    result = []
    for f in files:
        path = Path(f)
        if path.name in _EXCLUDED_FILENAMES:
            continue
        if path.suffix.lower() in extensions:
            result.append(f)
    return result


def _get_old_side_hunks(
    repo: Path, fix_hash: str, filepath: str,
) -> list[tuple[int, int]]:
    """Parse unified diff for old-side (pre-fix) line ranges.

    Returns (start, end) tuples for lines that existed before the fix —
    these are the buggy code locations to blame.
    """
    output = _run_git(repo, "diff", f"{fix_hash}~1", fix_hash, "--", filepath)
    hunks: list[tuple[int, int]] = []
    for match in _HUNK_RE.finditer(output):
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) else 1
        if count > 0:
            hunks.append((start, start + count - 1))
    return hunks


def _blame_lines(
    repo: Path, ref: str, filepath: str, start: int, end: int,
) -> list[str]:
    """Run git blame --porcelain on a line range and return unique SHAs.

    Filters out boundary markers (^sha) and zero-SHAs.
    """
    output = _run_git(
        repo, "blame", "--porcelain",
        f"-L{start},{end}", ref, "--", filepath,
        timeout=60,
    )
    shas: list[str] = []
    seen: set[str] = set()
    for match in _SHA_RE.finditer(output):
        sha = match.group(1)
        if sha.startswith("0000000") or sha in seen:
            continue
        seen.add(sha)
        shas.append(sha)
    return shas
