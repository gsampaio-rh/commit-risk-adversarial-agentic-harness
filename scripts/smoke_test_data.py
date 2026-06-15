#!/usr/bin/env python3
"""Smoke test: verify data infrastructure is ready for V3 eval.

Checks:
  1. GroundTruthGraph loads from replication zip
  2. Git repos exist and are accessible for n=20 eval cases
  3. JIRA cache has entries for n=20 eval cases (with descriptions)
  4. select_eval_cases returns exactly n cases

Usage:
    python scripts/smoke_test_data.py \
        --zip data/apachejit/apachejit_dataset_replication.zip \
        --repos-dir data/repos \
        --jira-cache data/jira_cache
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))


def check_ground_truth(zip_path: str) -> bool:
    from commit_investigator.infra.ground_truth import GroundTruthGraph

    print("1. Ground Truth Graph")
    try:
        gt = GroundTruthGraph.from_replication_zip(zip_path)
        print(f"   ✓ Loaded: {gt.total_bug_commits} bugs, {gt.total_fix_commits} fixes, {gt.total_issue_links} links")
        print(f"   ✓ Projects: {gt.projects}")
        return True
    except Exception as e:
        print(f"   ✗ FAILED: {e}")
        return False


def check_repos(repos_dir: str, projects: list[str]) -> tuple[bool, list[str]]:
    print("\n2. Git Repositories")
    repos_path = Path(repos_dir)
    missing = []
    ok = True

    for project in projects:
        repo_path = repos_path / project.lower()
        git_dir = repo_path / ".git"
        if repo_path.is_symlink():
            target = repo_path.resolve()
            if git_dir.exists():
                print(f"   ✓ {project:12s} → symlink to {target.name}, .git found")
            else:
                print(f"   ✗ {project:12s} → symlink to {target.name}, but .git NOT found")
                missing.append(project)
                ok = False
        elif git_dir.exists():
            print(f"   ✓ {project:12s} → .git found")
        else:
            print(f"   ✗ {project:12s} → NOT FOUND at {repo_path}")
            missing.append(project)
            ok = False

    return ok, missing


def check_jira_cache(jira_cache: str, n: int, seed: int, zip_path: str) -> tuple[bool, int]:
    from commit_investigator.infra.ground_truth import GroundTruthGraph
    from commit_investigator.infra.jira_client import JiraClient
    from commit_investigator.runners.run_eval import select_eval_cases

    print(f"\n3. JIRA Cache + Case Selection (n={n}, seed={seed})")
    gt = GroundTruthGraph.from_replication_zip(zip_path)
    jira = JiraClient(cache_dir=jira_cache)

    cases = select_eval_cases(gt, jira, n=n, seed=seed)
    print(f"   Selected {len(cases)} cases (requested {n})")

    cached_count = 0
    projects_in_cases = set()
    for case in cases:
        projects_in_cases.add(case.project)
        if jira.is_cached(case.issue_key):
            cached_count += 1

    print(f"   ✓ JIRA cached: {cached_count}/{len(cases)}")
    print(f"   ✓ Projects in cases: {sorted(projects_in_cases)}")

    if len(cases) < n:
        print(f"   ⚠ Only {len(cases)} cases selected (wanted {n}) — some JIRA tickets may lack descriptions")

    return len(cases) == n, len(cases)


def check_eval_readiness(repos_dir: str, jira_cache: str, n: int, seed: int, zip_path: str) -> bool:
    from commit_investigator.infra.ground_truth import GroundTruthGraph
    from commit_investigator.infra.jira_client import JiraClient
    from commit_investigator.runners.run_eval import select_eval_cases

    print(f"\n4. Eval Readiness (repos + JIRA for all {n} cases)")
    gt = GroundTruthGraph.from_replication_zip(zip_path)
    jira = JiraClient(cache_dir=jira_cache)
    cases = select_eval_cases(gt, jira, n=n, seed=seed)

    repos_path = Path(repos_dir)
    runnable = 0
    not_runnable = []

    for case in cases:
        repo_path = repos_path / case.project.lower()
        has_repo = (repo_path / ".git").exists()
        has_problem = case.problem is not None

        if has_repo and has_problem:
            runnable += 1
        else:
            reasons = []
            if not has_repo:
                reasons.append("no repo")
            if not has_problem:
                reasons.append("no problem statement")
            not_runnable.append(f"{case.project} {case.issue_key}: {', '.join(reasons)}")

    print(f"   Runnable: {runnable}/{len(cases)}")
    if not_runnable:
        print("   Not runnable:")
        for nr in not_runnable:
            print(f"     ✗ {nr}")

    return runnable == len(cases)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test data infrastructure")
    parser.add_argument("--zip", default="data/apachejit/apachejit_dataset_replication.zip")
    parser.add_argument("--repos-dir", default="data/repos")
    parser.add_argument("--jira-cache", default="data/jira_cache")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    print("=" * 60)
    print("DATA INFRASTRUCTURE SMOKE TEST")
    print("=" * 60)

    results = {}

    results["ground_truth"] = check_ground_truth(args.zip)

    all_eval_projects = [
        "CAMEL", "HADOOP", "HBASE", "SPARK", "GROOVY",
        "IGNITE", "HIVE", "FLINK", "CASSANDRA",
        "HDFS", "MAPREDUCE",
    ]
    repo_ok, missing = check_repos(args.repos_dir, all_eval_projects)
    results["repos"] = repo_ok

    jira_ok, case_count = check_jira_cache(args.jira_cache, args.n, args.seed, args.zip)
    results["jira"] = jira_ok

    results["eval_ready"] = check_eval_readiness(
        args.repos_dir, args.jira_cache, args.n, args.seed, args.zip
    )

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_pass = True
    for name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}  {name}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("All checks passed. Ready for first-live-eval.")
    else:
        print("Some checks failed. Fix issues above before running eval.")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
