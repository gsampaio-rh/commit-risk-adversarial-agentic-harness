#!/usr/bin/env python3
"""Build commit→JIRA mapping CSV for hypothesis prompt injection.

Reads the ApacheJIT replication zip to extract commit→issue_key links,
then fetches title + issue type from the public Apache JIRA REST API.
Output CSV has columns: commit_id,jira_key,jira_title,jira_type

Usage:
    python scripts/build_jira_csv.py \
        --zip data/apachejit/apachejit_dataset_replication.zip \
        --test data/apachejit/apachejit_test_small.csv \
        --output data/jira_context.csv

The script uses JiraClient's disk cache (data/jira_cache/) to avoid
redundant API calls across runs.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from commit_investigator.infra.ground_truth import GroundTruthGraph
from commit_investigator.infra.jira_client import JiraClient, JiraClientError


def _load_test_commit_ids(test_csv: str) -> list[str]:
    """Load all commit_ids from the test CSV."""
    commit_ids: list[str] = []
    with open(test_csv, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = row.get("commit_id", "").strip()
            if cid:
                commit_ids.append(cid)
    return commit_ids


def _resolve_issue_keys(gt: GroundTruthGraph, commit_ids: list[str]) -> dict[str, str]:
    """For each commit, resolve the first JIRA issue key (via chain or direct link)."""
    mapping: dict[str, str] = {}
    for cid in commit_ids:
        chain = gt.get_chain(cid)
        if chain.issue_keys:
            mapping[cid] = chain.issue_keys[0]
        else:
            direct = gt.get_issue_keys(cid)
            if direct:
                mapping[cid] = direct[0]
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
    parser = argparse.ArgumentParser(description="Build commit→JIRA context CSV")
    parser.add_argument(
        "--zip",
        default="data/apachejit/apachejit_dataset_replication.zip",
        help="Path to ApacheJIT replication zip",
    )
    parser.add_argument(
        "--test",
        default="data/apachejit/apachejit_test_small.csv",
        help="Path to test split CSV (determines which commits get JIRA context)",
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

    print("Loading ground truth graph...", file=sys.stderr)
    gt = GroundTruthGraph.from_replication_zip(args.zip)
    print(f"  {gt.total_issue_links} commit→issue links loaded", file=sys.stderr)

    print("Loading test commit IDs...", file=sys.stderr)
    commit_ids = _load_test_commit_ids(args.test)
    print(f"  {len(commit_ids)} commits in test split", file=sys.stderr)

    print("Resolving commit→JIRA key mappings...", file=sys.stderr)
    commit_to_key = _resolve_issue_keys(gt, commit_ids)
    print(f"  {len(commit_to_key)} commits have JIRA keys", file=sys.stderr)

    unique_keys = set(commit_to_key.values())
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
        for cid, key in sorted(commit_to_key.items()):
            if key in key_metadata:
                title, issue_type = key_metadata[key]
                writer.writerow([cid, key, title, issue_type])
                rows_written += 1

    print(f"\nOutput: {output_path} ({rows_written} rows)", file=sys.stderr)


if __name__ == "__main__":
    main()
