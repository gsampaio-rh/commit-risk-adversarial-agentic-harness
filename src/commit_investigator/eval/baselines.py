"""Zero-LLM deterministic baselines for bug attribution.

These establish a performance floor — the attribution agent must
beat these to justify its LLM cost.

Baselines:
  git-blame-naive: Blame files at temporal bound, return most
      frequently appearing non-merge commits.
  file-history-recency: Most recent commits touching files
      mentioned in the problem description.
  random-commit: Pick 5 random commits from the repo history
      (absolute floor — expected Hit@5 ~ 5/N ≈ 0.01%).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from commit_investigator.infra.git_context import GitContextProvider
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.agent.orchestrator import (
    BugAttributionReport,
    SuspectCommit,
    ToolCallRecord,
)


@dataclass
class BaselineResult:
    """Result from a deterministic baseline."""

    name: str
    report: BugAttributionReport


def _extract_file_hints(text: str) -> list[str]:
    """Extract plausible file paths from problem text."""
    patterns = [
        re.findall(r"\b[\w/]+\.(?:java|py|xml|properties|yaml|yml|json|scala|groovy)\b", text),
        re.findall(r"\b(?:[A-Z][a-z]+){2,}\b", text),
    ]
    files = []
    for matches in patterns:
        files.extend(matches)
    return list(dict.fromkeys(files))[:10]


def _extract_class_names(text: str) -> list[str]:
    """Extract CamelCase class names from problem text."""
    return re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", text)


def git_blame_naive(
    problem: ProblemStatement,
    git_provider: GitContextProvider,
) -> BaselineResult:
    """Blame-based baseline: zero LLM.

    1. Extract file hints from problem text
    2. Run git blame on those files at the temporal bound
    3. Count commit appearances in blame output
    4. Return top 5 most-frequent commits as suspects
    """
    text = f"{problem.title} {problem.description}"
    file_hints = _extract_file_hints(text)
    class_hints = _extract_class_names(text)

    commit_counts: dict[str, int] = {}

    for hint in file_hints:
        blame = git_provider.get_blame(hint)
        if blame:
            _count_blame_commits(blame, commit_counts)

    for cls_name in class_hints[:5]:
        results = git_provider.search_commits_by_keyword(cls_name, max_results=5)
        for entry in results:
            commit_counts[entry.commit_id] = commit_counts.get(entry.commit_id, 0) + 1

    ranked = sorted(commit_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    suspects = [
        SuspectCommit(
            commit_id=cid,
            rank=i + 1,
            confidence=count / max(1, sum(c for _, c in ranked)),
            mechanism=f"Blame frequency: appeared {count} times in blamed files",
        )
        for i, (cid, count) in enumerate(ranked)
    ]

    return BaselineResult(
        name="git-blame-naive",
        report=BugAttributionReport(
            problem_title=problem.title,
            problem_description=problem.description,
            suspects=suspects,
            reasoning_summary=f"Blame-based baseline on {len(file_hints)} file hints, {len(class_hints)} class hints",
            tool_trace=[],
            metadata={"baseline": "git-blame-naive", "file_hints": file_hints[:5]},
        ),
    )


def file_history_recency(
    problem: ProblemStatement,
    git_provider: GitContextProvider,
) -> BaselineResult:
    """File-history-recency baseline: zero LLM.

    1. Extract file hints from problem text
    2. Find most recent commits touching those files
    3. Return top 5 most-recent distinct commits
    """
    text = f"{problem.title} {problem.description}"
    file_hints = _extract_file_hints(text)

    seen: dict[str, str] = {}
    for hint in file_hints:
        history = git_provider.search_commits_by_file(hint, max_results=5)
        for entry in history:
            if entry.commit_id not in seen:
                seen[entry.commit_id] = entry.date

    ranked = sorted(seen.items(), key=lambda x: x[1], reverse=True)[:5]

    suspects = [
        SuspectCommit(
            commit_id=cid,
            rank=i + 1,
            confidence=max(0.1, 1.0 - i * 0.2),
            mechanism=f"Most recent commit touching problem-mentioned files (date: {date[:10]})",
        )
        for i, (cid, date) in enumerate(ranked)
    ]

    return BaselineResult(
        name="file-history-recency",
        report=BugAttributionReport(
            problem_title=problem.title,
            problem_description=problem.description,
            suspects=suspects,
            reasoning_summary=f"Recency baseline on {len(file_hints)} file hints",
            tool_trace=[],
            metadata={"baseline": "file-history-recency", "file_hints": file_hints[:5]},
        ),
    )


def random_commit(
    problem: ProblemStatement,
    git_provider: GitContextProvider,
    n: int = 5,
    seed: int | None = None,
) -> BaselineResult:
    """Random commit baseline: absolute floor reference.

    Picks n random commits from the repo history before the temporal bound.
    Expected Hit@5 ≈ 5/total_commits (typically <0.01%).
    """
    all_commits = git_provider.search_commits_by_keyword("", max_results=500)
    if not all_commits:
        return BaselineResult(
            name="random-commit",
            report=BugAttributionReport(
                problem_title=problem.title,
                problem_description=problem.description,
                suspects=[],
                reasoning_summary="Random baseline: no commits found in repository",
                tool_trace=[],
                metadata={"baseline": "random-commit"},
            ),
        )

    rng = random.Random(seed)
    sample = rng.sample(all_commits, min(n, len(all_commits)))

    suspects = [
        SuspectCommit(
            commit_id=entry.commit_id,
            rank=i + 1,
            confidence=1.0 / n,
            mechanism=f"Random selection (commit {i + 1} of {n})",
        )
        for i, entry in enumerate(sample)
    ]

    return BaselineResult(
        name="random-commit",
        report=BugAttributionReport(
            problem_title=problem.title,
            problem_description=problem.description,
            suspects=suspects,
            reasoning_summary=f"Random baseline: {n} commits from pool of {len(all_commits)}",
            tool_trace=[],
            metadata={
                "baseline": "random-commit",
                "pool_size": len(all_commits),
                "seed": seed,
            },
        ),
    )


def _count_blame_commits(blame_output: str, counts: dict[str, int]) -> None:
    """Count commit SHA occurrences in blame output."""
    for line in blame_output.splitlines():
        match = re.match(r"^([0-9a-f]{8,40})\s", line)
        if match:
            sha = match.group(1)
            if len(sha) >= 8:
                counts[sha] = counts.get(sha, 0) + 1
