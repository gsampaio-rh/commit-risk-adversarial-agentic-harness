#!/usr/bin/env python3
"""Build commit→JIRA mapping CSV for hypothesis prompt injection.

TEMPORAL MODEL: Extracts JIRA keys from COMMIT MESSAGES (commit-time info).
The developer referenced this ticket when writing the commit — it existed
before the commit was made. This is NOT oracle information.

DO NOT use GroundTruthGraph.get_chain() — that resolves the future fix→issue
linkage which is oracle/post-hoc information.

Output CSV columns: commit_id,jira_key,jira_title,jira_type

Usage:
    python scripts/build_jira_csv.py \
        --test data/apachejit/apachejit_test_small.csv \
        --repos-dir data/repos \
        --output data/jira_context.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from commit_investigator.context.git_context import GitContextProvider, GitRepoNotFoundError
from commit_investigator.infra.jira_client import JiraClient, JiraClientError

_JIRA_KEY_PATTERN = re.compile(r"([A-Z][A-Z0-9]+-\d+)")

V1_PROJECTS = {"camel", "hadoop"}


def _normalize_project(raw: str) -> str:
    """Normalize 'apache/camel' → 'camel'."""
    return raw.split("/")[-1].lower().strip()


def _extract_jira_key_from_message(message: str) -> str | None:
    """Extract first JIRA key from a commit message (commit-time info)."""
    if not message:
        return None
    match = _JIRA_KEY_PATTERN.search(message)
    return match.group(1) if match else None


def _resolve_commit_time_keys(
    test_csv: str,
    git_providers: dict[str, GitContextProvider],
) -> dict[str, tuple[str, str]]:
    """For each test commit, extract JIRA key from its commit message.

    Returns {commit_id: (jira_key, project)}.
    This is commit-time information — the ticket existed before the commit.
    """
    mapping: dict[str, tuple[str, str]] = {}
    no_key = 0
    no_message = 0

    with open(test_csv, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = row.get("commit_id", "").strip()
            project = _normalize_project(row.get("project", ""))
            if not cid or project not in git_providers:
                continue

            provider = git_providers[project]
            try:
                message = provider.get_commit_message(cid)
            except Exception:
                no_message += 1
                continue

            jira_key = _extract_jira_key_from_message(message)
            if jira_key:
                mapping[cid] = (jira_key, project)
            else:
                no_key += 1

    return mapping


def _fetch_jira_metadata(
    jira: JiraClient,
    issue_keys: set[str],
    *,
    rate_limit_delay: float = 0.5,
    skip_uncached: bool = False,
) -> dict[str, tuple[str, str]]:
    """Fetch title + issue type for each JIRA key. Returns {key: (title, type)}."""
    results: dict[str, tuple[str, str]] = {}
    total = len(issue_keys)
    errors: list[str] = []
    skipped = 0

    for i, key in enumerate(sorted(issue_keys), 1):
        was_cached = jira.is_cached(key)
        if skip_uncached and not was_cached:
            skipped += 1
            continue

        try:
            issue = jira.get_issue(key)
            issue_type = ""
            if issue.raw and "fields" in issue.raw:
                it = issue.raw["fields"].get("issuetype", {})
                issue_type = it.get("name", "") if it else ""
            results[key] = (issue.summary, issue_type)
            status = "cached" if was_cached else "fetched"
            print(f"  [{i}/{total}] {key}: {status} — {issue.summary[:60]}", file=sys.stderr)
            if not was_cached:
                time.sleep(rate_limit_delay)
        except (JiraClientError, Exception) as exc:
            errors.append(f"{key}: {exc}")
            print(f"  [{i}/{total}] {key}: ERROR — {exc}", file=sys.stderr)

    if skipped:
        print(f"  Skipped {skipped} uncached issue(s) (--skip-uncached)", file=sys.stderr)
    if errors:
        print(f"\n  {len(errors)} issue(s) failed to fetch:", file=sys.stderr)
        for e in errors[:10]:
            print(f"    {e}", file=sys.stderr)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build commit→JIRA context CSV (commit-message extraction, temporally valid)"
    )
    parser.add_argument(
        "--test",
        default="data/apachejit/apachejit_test_small.csv",
        help="Path to test split CSV",
    )
    parser.add_argument(
        "--repos-dir",
        default="data/repos",
        help="Path to git repo clones",
    )
    parser.add_argument(
        "--output",
        default="data/jira_context.csv",
        help="Output CSV path",
    )
    parser.add_argument(
        "--cache-dir",
        default="data/jira_cache",
        help="JIRA API response cache directory",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=0.5,
        help="Seconds between uncached JIRA API calls",
    )
    parser.add_argument(
        "--skip-uncached",
        action="store_true",
        help="Only use already-cached JIRA issues (no API calls)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP timeout for JIRA API calls (seconds)",
    )
    args = parser.parse_args()

    print("Initializing git providers...", file=sys.stderr)
    repos_dir = Path(args.repos_dir)
    git_providers: dict[str, GitContextProvider] = {}
    for project in V1_PROJECTS:
        try:
            git_providers[project] = GitContextProvider.for_project(project, repos_dir)
        except GitRepoNotFoundError:
            print(f"  WARNING: {project} repo not found at {repos_dir / project}", file=sys.stderr)
    print(f"  Available: {sorted(git_providers.keys())}", file=sys.stderr)

    print("Extracting JIRA keys from commit messages (commit-time info)...", file=sys.stderr)
    commit_to_key = _resolve_commit_time_keys(args.test, git_providers)
    print(f"  {len(commit_to_key)} commits have JIRA keys in their message", file=sys.stderr)

    unique_keys = {key for key, _ in commit_to_key.values()}
    print(f"\nFetching metadata for {len(unique_keys)} unique JIRA issues...", file=sys.stderr)
    jira = JiraClient(cache_dir=args.cache_dir, timeout=args.timeout)
    key_metadata = _fetch_jira_metadata(
        jira, unique_keys, rate_limit_delay=args.rate_limit, skip_uncached=args.skip_uncached,
    )
    print(f"  {len(key_metadata)} issues fetched successfully", file=sys.stderr)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows_written = 0
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["commit_id", "jira_key", "jira_title", "jira_type"])
        for cid in sorted(commit_to_key.keys()):
            key, _ = commit_to_key[cid]
            if key in key_metadata:
                title, issue_type = key_metadata[key]
                writer.writerow([cid, key, title, issue_type])
                rows_written += 1

    print(f"\nOutput: {output_path} ({rows_written} rows)", file=sys.stderr)


if __name__ == "__main__":
    main()
