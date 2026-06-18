"""Shared helpers for evaluation scripts.

Provides JIRA loading, ground truth hash resolution, eval case construction,
run folder management, and standard paths. Used by run_phase1_checkpoint.py
and run_scoped_eval.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from commit_investigator.eval.ground_truth import GroundTruthGraph

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESULTS_DIR = PROJECT_ROOT / "results"
JIRA_CACHE_DIR = PROJECT_ROOT / "data" / "jira_cache"
REPOS_DIR = PROJECT_ROOT / "data" / "repos"
ZIP_PATH = PROJECT_ROOT / "data" / "apachejit" / "apachejit_dataset_replication.zip"
RECALL100_PATH = PROJECT_ROOT / "results" / "2026-06-16T20-08-v4-checkpoint-retrieval" / "summary.json"


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


def create_run_folder(label: str, model: str = "", n: int = 0) -> Path:
    """Create a timestamped run folder under results/.

    Naming: {YYYY-MM-DDTHH-MM}-{label}[-{model}-n{N}]
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
    parts = [ts, label]
    if model:
        safe_model = model.replace("/", "-").replace(":", "-")
        parts.append(safe_model)
    if n > 0:
        parts.append(f"n{n}")
    name = "-".join(parts)
    path = RESULTS_DIR / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "traces").mkdir(exist_ok=True)
    return path


def write_run_config(run_dir: Path, **kwargs: Any) -> Path:
    """Write config.json capturing run parameters and environment."""
    config: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": _get_git_sha(),
        **kwargs,
    }
    path = run_dir / "config.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def update_latest_symlink(run_dir: Path) -> None:
    """Create or update results/latest symlink to the given run folder."""
    link_path = RESULTS_DIR / "latest"
    if link_path.is_symlink() or link_path.exists():
        link_path.unlink()
    link_path.symlink_to(run_dir.name)


def _get_git_sha() -> str:
    """Return current git HEAD short SHA, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(PROJECT_ROOT),
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""
