"""Shared helpers for evaluation scripts.

Provides JIRA loading, ground truth hash resolution, eval case construction,
and standard paths. Used by both run_phase1_checkpoint.py and run_scoped_eval.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from commit_investigator.eval.ground_truth import GroundTruthGraph

PROJECT_ROOT = Path(__file__).resolve().parents[3]
JIRA_CACHE_DIR = PROJECT_ROOT / "data" / "jira_cache"
REPOS_DIR = PROJECT_ROOT / "data" / "repos"
ZIP_PATH = PROJECT_ROOT / "data" / "apachejit" / "apachejit_dataset_replication.zip"
RECALL100_PATH = PROJECT_ROOT / "results" / "v4-checkpoints" / "retrieval-recall.json"


@dataclass
class EvalCase:
    """Single evaluation case resolved from ground truth."""

    issue_key: str
    project: str
    bug_hashes: list[str]
    fix_hash: str
    repo_path: Path
    temporal_bound: str


def load_jira_text(issue_key: str) -> dict[str, str] | None:
    """Load cached JIRA issue fields (title + description)."""
    path = JIRA_CACHE_DIR / f"{issue_key}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    return {
        "title": raw["fields"]["summary"],
        "description": raw["fields"].get("description") or "",
    }


def find_hashes(gt: GroundTruthGraph, issue_key: str) -> tuple[str, list[str]]:
    """Find (fix_hash, all_bug_hashes) for an issue key from ground truth."""
    commits = gt._issue_to_commits.get(issue_key, [])
    for commit_id in commits:
        if gt.has_fix(commit_id):
            bug_hashes = gt.get_bug_commits(commit_id)
            if bug_hashes:
                return commit_id, bug_hashes
    return "", []


def build_eval_cases(
    gt: GroundTruthGraph,
    max_n: int | None = None,
) -> list[EvalCase]:
    """Build eval cases from retrieval-recall.json + ground truth graph."""
    recall_data = json.loads(RECALL100_PATH.read_text(encoding="utf-8"))
    cases: list[EvalCase] = []
    for case_data in recall_data["cases"]:
        issue_key = case_data["issue_key"]
        project = case_data["project"]
        fix_hash, bug_hashes = find_hashes(gt, issue_key)
        if not fix_hash or not bug_hashes:
            continue
        cases.append(EvalCase(
            issue_key=issue_key,
            project=project,
            bug_hashes=bug_hashes,
            fix_hash=fix_hash,
            repo_path=REPOS_DIR / project.lower(),
            temporal_bound=f"{fix_hash}~1",
        ))
        if max_n and len(cases) >= max_n:
            break
    return cases


def gt_in_set(bug_hashes: list[str], sha_set: list[str]) -> bool:
    """Check if any bug hash appears in a SHA list (case-insensitive)."""
    targets = {bh.lower() for bh in bug_hashes}
    return bool(targets & {s.lower() for s in sha_set})
