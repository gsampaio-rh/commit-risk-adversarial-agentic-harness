#!/usr/bin/env python3
"""Pre-fetch JIRA tickets for V3 eval cases into disk cache.

Warms the cache by running through the same candidate selection logic as
select_eval_cases(seed=42) and fetching each JIRA ticket. This avoids
live HTTP calls during eval runs and enables deterministic case selection.

Usage:
    python scripts/warm_jira_cache.py \
        --zip data/apachejit/apachejit_dataset_replication.zip \
        --jira-cache data/jira_cache \
        --n 20 --fetch-extra 80
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from commit_investigator.eval.ground_truth import GroundTruthGraph
from commit_investigator.extraction.jira_client import JiraClient, JiraClientError

logger = logging.getLogger(__name__)

PROJECT_TO_REPO = {
    "AMQ": "activemq",
    "CAMEL": "camel",
    "CASSANDRA": "cassandra",
    "FLINK": "flink",
    "GROOVY": "groovy",
    "HBASE": "hbase",
    "HDFS": "hadoop",
    "HIVE": "hive",
    "IGNITE": "ignite",
    "KAFKA": "kafka",
    "MAPREDUCE": "hadoop",
    "SPARK": "spark",
    "ZEPPELIN": "zeppelin",
    "ZOOKEEPER": "zookeeper",
}


def build_candidate_list(gt: GroundTruthGraph, seed: int) -> list[tuple[str, str, str, str]]:
    """Replicate select_eval_cases candidate ordering (deterministic)."""
    rng = random.Random(seed)
    candidates: list[tuple[str, str, str, str]] = []

    for project in gt.projects:
        for bug_hash in gt._bug_to_fixes:
            chain = gt.get_chain(bug_hash)
            for ik in chain.issue_keys:
                if ik.startswith(project + "-"):
                    if chain.fix_hashes and chain.issue_keys:
                        fix_hash = min(chain.fix_hashes)
                        issue_key = chain.issue_keys[0]
                        candidates.append((bug_hash, fix_hash, project, issue_key))
                    break

    rng.shuffle(candidates)
    return candidates


def _process_candidate(
    jira: JiraClient, bug_hash: str, fix_hash: str, project: str,
    issue_key: str, stats: dict,
) -> dict | None:
    """Fetch one candidate's JIRA issue. Returns case dict if usable, else None."""
    was_cached = jira.is_cached(issue_key)
    try:
        issue = jira.get_issue(issue_key)
        if was_cached:
            stats["cached"] += 1
        else:
            stats["fetched"] += 1
            time.sleep(0.3)

        has_desc = bool(issue.description and issue.description.strip())
        if has_desc:
            stats["has_desc"] += 1
            return {
                "bug_hash": bug_hash, "fix_hash": fix_hash,
                "project": project, "issue_key": issue_key,
                "repo_dir": PROJECT_TO_REPO.get(project, project.lower()),
                "summary": issue.summary[:80],
            }
        stats["no_desc"] += 1
        logger.debug("Skip %s — no description", issue_key)
        return None

    except JiraClientError as e:
        stats["errors"] += 1
        logger.warning("Error fetching %s: %s", issue_key, e)
        return None


def warm_cache(
    gt: GroundTruthGraph,
    jira: JiraClient,
    n: int = 20,
    seed: int = 42,
    fetch_extra: int = 80,
) -> list[dict]:
    """Fetch JIRA tickets for eval candidates, return selected cases."""
    candidates = build_candidate_list(gt, seed)
    total_to_fetch = min(n + fetch_extra, len(candidates))

    logger.info("Warming JIRA cache: fetching up to %d candidates to select %d cases", total_to_fetch, n)

    selected: list[dict] = []
    stats = {"fetched": 0, "cached": 0, "has_desc": 0, "no_desc": 0, "errors": 0}

    for i, (bug_hash, fix_hash, project, issue_key) in enumerate(candidates[:total_to_fetch]):
        if len(selected) >= n and i >= total_to_fetch:
            break

        case = _process_candidate(jira, bug_hash, fix_hash, project, issue_key, stats)
        if case and len(selected) < n:
            selected.append(case)
            logger.info("[%d/%d] SELECTED %s %s (repo=%s)",
                        len(selected), n, project, issue_key, case["repo_dir"])

        if (i + 1) % 10 == 0:
            logger.info("Progress: %d/%d fetched, %d selected so far",
                        i + 1, total_to_fetch, len(selected))

    logger.info(
        "Done. fetched=%d, cached=%d, has_desc=%d, no_desc=%d, errors=%d, selected=%d",
        stats["fetched"], stats["cached"], stats["has_desc"], stats["no_desc"],
        stats["errors"], len(selected),
    )

    projects_needed = sorted(set(c["repo_dir"] for c in selected))
    logger.info("Repos needed for n=%d: %s", n, projects_needed)

    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Warm JIRA cache for V3 eval cases")
    parser.add_argument("--zip", default="data/apachejit/apachejit_dataset_replication.zip")
    parser.add_argument("--jira-cache", default="data/jira_cache")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fetch-extra", type=int, default=80,
                        help="Fetch extra candidates beyond n (covers JIRA filter drops)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    gt = GroundTruthGraph.from_replication_zip(args.zip)
    logger.info("Ground truth: %d bugs, %d fixes, %d links",
                gt.total_bug_commits, gt.total_fix_commits, gt.total_issue_links)

    jira = JiraClient(cache_dir=args.jira_cache)

    selected = warm_cache(gt, jira, n=args.n, seed=args.seed, fetch_extra=args.fetch_extra)

    print("\n" + "=" * 70)
    print(f"SELECTED {len(selected)} EVAL CASES")
    print("=" * 70)
    for i, case in enumerate(selected, 1):
        print(f"  {i:2d}. {case['project']:12s} {case['issue_key']:22s} repo={case['repo_dir']}")
    print()

    projects = sorted(set(c["repo_dir"] for c in selected))
    print(f"Repos needed: {projects}")
    print(f"Clone command: ./scripts/clone_apache_repos.sh data/repos")


if __name__ == "__main__":
    main()
