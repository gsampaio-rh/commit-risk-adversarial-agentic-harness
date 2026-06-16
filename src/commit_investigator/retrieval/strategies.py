"""Git-signal retrieval strategies for V4 candidate assembly."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.infra.git_context import FileHistoryEntry, GitContextProvider

VALID_STRATEGIES = frozenset({"file_log", "keyword_grep", "pickaxe", "blame"})

_BLAME_SHA_PATTERN = re.compile(r"^([0-9a-fA-F]{7,40})\s")


@dataclass
class StrategyHit:
    """One commit discovered by a single strategy invocation."""

    commit_id: str
    strategy: str
    rank: int
    message: str = ""
    date: str = ""


@dataclass
class MergedCandidate:
    """Commit aggregated across strategies before final ranking."""

    commit_id: str
    strategies: set[str] = field(default_factory=set)
    best_rank: int = 0
    message: str = ""
    date: str = ""
    is_fallback: bool = False


def dedupe_preserve_order(items: list[str]) -> list[str]:
    """Return unique items preserving first occurrence order."""
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def filter_valid_strategies(strategies: list[str]) -> list[str]:
    """Keep only known strategy names in config order."""
    return [name for name in strategies if name in VALID_STRATEGIES]


def normalize_sha(git: GitContextProvider, sha: str) -> str | None:
    """Resolve abbreviated SHAs to full 40-char hex."""
    if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha.lower()):
        return sha.lower()
    resolved = git.resolve_ref(sha)
    if resolved is None:
        return None
    return resolved.lower()


def parse_blame_shas(blame_output: str, git: GitContextProvider) -> list[str]:
    """Extract unique full SHAs from git blame output, skipping boundary commits."""
    shas: list[str] = []
    seen: set[str] = set()
    for line in blame_output.splitlines():
        if line.startswith("^"):
            continue
        match = _BLAME_SHA_PATTERN.match(line)
        if not match:
            continue
        full_sha = normalize_sha(git, match.group(1))
        if full_sha is None or full_sha in seen:
            continue
        seen.add(full_sha)
        shas.append(full_sha)
    return shas


def resolve_paths_for_files(
    git: GitContextProvider,
    files: list[str],
) -> list[tuple[str, list[str]]]:
    """Map each unique file hint to resolved repo paths."""
    resolved: list[tuple[str, list[str]]] = []
    for filename in dedupe_preserve_order(files):
        paths = git.resolve_file_path(filename)
        if paths:
            resolved.append((filename, paths))
    return resolved


def _entry_to_hit(entry: FileHistoryEntry, strategy: str, rank: int) -> StrategyHit:
    return StrategyHit(
        commit_id=entry.commit_id.lower(),
        strategy=strategy,
        rank=rank,
        message=entry.message.splitlines()[0] if entry.message else "",
        date=entry.date,
    )


def _collect_entry_hits(
    entries_by_query: list[list[FileHistoryEntry]],
    strategy: str,
) -> list[StrategyHit]:
    """Merge entries from multiple queries, keeping best rank per SHA within one strategy."""
    best: dict[str, tuple[int, FileHistoryEntry]] = {}
    for entries in entries_by_query:
        for rank, entry in enumerate(entries, start=1):
            sha = entry.commit_id.lower()
            if sha not in best or rank < best[sha][0]:
                best[sha] = (rank, entry)
    return [
        _entry_to_hit(entry, strategy, rank)
        for sha, (rank, entry) in sorted(best.items(), key=lambda item: item[0])
    ]


def run_file_log(
    problem: ProblemStatement,
    git: GitContextProvider,
    max_results: int,
) -> list[StrategyHit]:
    """Find commits touching extracted file paths."""
    entry_groups: list[list[FileHistoryEntry]] = []
    for _filename, paths in resolve_paths_for_files(git, problem.extracted_files):
        for path in paths:
            entry_groups.append(git.search_commits_by_file(path, max_results=max_results))
    return _collect_entry_hits(entry_groups, "file_log")


def run_keyword_grep(
    problem: ProblemStatement,
    git: GitContextProvider,
    max_results: int,
) -> list[StrategyHit]:
    """Search commit messages for extracted keywords."""
    entry_groups = [
        git.search_commits_by_keyword(keyword, max_results=max_results)
        for keyword in dedupe_preserve_order(problem.extracted_keywords)
    ]
    return _collect_entry_hits(entry_groups, "keyword_grep")


def run_pickaxe(
    problem: ProblemStatement,
    git: GitContextProvider,
    max_results: int,
) -> list[StrategyHit]:
    """Find commits that add/remove extracted symbols."""
    entry_groups = [
        git.search_commits_by_pickaxe(symbol, max_results=max_results)
        for symbol in dedupe_preserve_order(problem.extracted_symbols)
    ]
    return _collect_entry_hits(entry_groups, "pickaxe")


def run_blame(
    problem: ProblemStatement,
    git: GitContextProvider,
    line_end: int,
) -> list[StrategyHit]:
    """Extract blame authorship commits for resolved file paths."""
    sha_best_rank: dict[str, int] = {}
    for _filename, paths in resolve_paths_for_files(git, problem.extracted_files):
        for path in paths:
            blame_output = git.get_blame(path, line_start=1, line_end=line_end)
            if not blame_output:
                continue
            for rank, sha in enumerate(parse_blame_shas(blame_output, git), start=1):
                if sha not in sha_best_rank or rank < sha_best_rank[sha]:
                    sha_best_rank[sha] = rank
    return [
        StrategyHit(commit_id=sha, strategy="blame", rank=rank)
        for sha, rank in sorted(sha_best_rank.items(), key=lambda item: item[0])
    ]


def merge_hits(hits: list[StrategyHit]) -> dict[str, MergedCandidate]:
    """Merge strategy hits by commit_id, tracking strategies and best_rank."""
    merged: dict[str, MergedCandidate] = {}
    for hit in hits:
        existing = merged.get(hit.commit_id)
        if existing is None:
            merged[hit.commit_id] = MergedCandidate(
                commit_id=hit.commit_id,
                strategies={hit.strategy},
                best_rank=hit.rank,
                message=hit.message,
                date=hit.date,
            )
            continue
        existing.strategies.add(hit.strategy)
        existing.best_rank = min(existing.best_rank, hit.rank)
        if not existing.message and hit.message:
            existing.message = hit.message
        if not existing.date and hit.date:
            existing.date = hit.date
    return merged
