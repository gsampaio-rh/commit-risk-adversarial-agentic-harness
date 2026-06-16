"""Ground truth noise analysis for the n=20 eval cases.

Examines each SZZ-derived bug_hash label to estimate noise rate:
- Fetches fix commit diff and bug commit diff
- Checks if the bug commit plausibly introduced the bug
- Categorizes: correct, plausible, questionable, noise

Usage: PYTHONPATH=src python scripts/gt_noise_analysis.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOS_DIR = PROJECT_ROOT / "data" / "repos"
REPLICATION_ZIP = PROJECT_ROOT / "data" / "apachejit" / "apachejit_dataset_replication.zip"
EVAL_RESULTS = PROJECT_ROOT / "results" / "v3-subagent-eval" / "comparison.json"
OUTPUT_FILE = PROJECT_ROOT / "results" / "gt-noise-analysis.json"

PROJECT_TO_REPO = {
    "AMQ": "amq",
    "CASSANDRA": "cassandra",
    "FLINK": "flink",
    "GROOVY": "groovy",
    "HBASE": "hbase",
    "HDFS": "hdfs",
    "HIVE": "hive",
    "IGNITE": "ignite",
    "SPARK": "spark",
}


@dataclass
class CaseAnalysis:
    issue_key: str
    project: str
    bug_hash: str
    fix_hashes: list[str]
    bug_commit_exists: bool
    fix_commit_exists: bool
    bug_commit_msg: str
    fix_commit_msg: str
    bug_files_changed: int
    fix_files_changed: int
    bug_diff_lines: int
    fix_diff_lines: int
    bug_is_merge: bool
    overlap_files: list[str]
    overlap_ratio: float
    category: str
    notes: str

    def to_dict(self) -> dict:
        return {
            "issue_key": self.issue_key,
            "project": self.project,
            "bug_hash": self.bug_hash[:12],
            "fix_hashes": [h[:12] for h in self.fix_hashes],
            "bug_commit_exists": self.bug_commit_exists,
            "fix_commit_exists": self.fix_commit_exists,
            "bug_commit_msg": self.bug_commit_msg[:200],
            "fix_commit_msg": self.fix_commit_msg[:200],
            "bug_files_changed": self.bug_files_changed,
            "fix_files_changed": self.fix_files_changed,
            "bug_diff_lines": self.bug_diff_lines,
            "fix_diff_lines": self.fix_diff_lines,
            "bug_is_merge": self.bug_is_merge,
            "overlap_files": self.overlap_files[:10],
            "overlap_ratio": round(self.overlap_ratio, 3),
            "category": self.category,
            "notes": self.notes,
        }


def _git(repo_dir: Path, *args: str) -> str | None:
    """Run git command, return stdout or None on error."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception:
        return None


def _commit_exists(repo_dir: Path, sha: str) -> bool:
    return _git(repo_dir, "cat-file", "-t", sha) == "commit"


def _commit_msg(repo_dir: Path, sha: str) -> str:
    return _git(repo_dir, "log", "-1", "--format=%s", sha) or ""


def _is_merge(repo_dir: Path, sha: str) -> bool:
    parents = _git(repo_dir, "rev-list", "--parents", "-1", sha)
    if parents:
        return len(parents.split()) > 2
    return False


def _changed_files(repo_dir: Path, sha: str) -> list[str]:
    out = _git(repo_dir, "diff-tree", "--no-commit-id", "-r", "--name-only", sha)
    if out:
        return [f for f in out.split("\n") if f.strip()]
    return []


def _diff_line_count(repo_dir: Path, sha: str) -> int:
    out = _git(repo_dir, "diff-tree", "-p", "--no-commit-id", sha)
    return len(out.split("\n")) if out else 0


def _classify(analysis: CaseAnalysis) -> tuple[str, str]:
    """Auto-classify a case based on heuristics. Returns (category, notes)."""
    if not analysis.bug_commit_exists:
        return "unknown", "Bug commit not found in repo"

    if analysis.bug_is_merge:
        return "questionable", "Bug commit is a merge commit (SZZ often mislabels merges)"

    if analysis.bug_files_changed > 50:
        return "questionable", f"Bug commit is a mass change ({analysis.bug_files_changed} files)"

    if analysis.overlap_ratio == 0.0 and analysis.fix_commit_exists:
        return "noise", "Zero file overlap between bug commit and fix commit"

    if analysis.overlap_ratio >= 0.3:
        return "plausible", f"Good file overlap ({analysis.overlap_ratio:.0%}) between bug and fix"

    if 0.0 < analysis.overlap_ratio < 0.3:
        return "questionable", f"Low file overlap ({analysis.overlap_ratio:.0%}) between bug and fix"

    msg_lower = analysis.bug_commit_msg.lower()
    noise_keywords = ["merge", "format", "style", "whitespace", "copyright", "license", "bump version"]
    if any(kw in msg_lower for kw in noise_keywords):
        return "noise", f"Bug commit message suggests non-functional change: '{analysis.bug_commit_msg[:80]}'"

    return "plausible", "Default: commit exists, no clear noise signals"


def analyze_case(
    issue_key: str,
    project: str,
    bug_hash: str,
    fix_hashes: list[str],
) -> CaseAnalysis:
    repo_name = PROJECT_TO_REPO.get(project, project.lower())
    repo_dir = REPOS_DIR / repo_name

    bug_exists = _commit_exists(repo_dir, bug_hash) if repo_dir.exists() else False
    bug_msg = _commit_msg(repo_dir, bug_hash) if bug_exists else ""
    bug_files = _changed_files(repo_dir, bug_hash) if bug_exists else []
    bug_diff_lines = _diff_line_count(repo_dir, bug_hash) if bug_exists else 0
    bug_merge = _is_merge(repo_dir, bug_hash) if bug_exists else False

    fix_exists = False
    fix_msg = ""
    fix_files: list[str] = []
    fix_diff_lines = 0

    for fh in fix_hashes:
        if _commit_exists(repo_dir, fh):
            fix_exists = True
            fix_msg = _commit_msg(repo_dir, fh)
            fix_files = _changed_files(repo_dir, fh)
            fix_diff_lines = _diff_line_count(repo_dir, fh)
            break

    overlap = sorted(set(bug_files) & set(fix_files))
    overlap_ratio = len(overlap) / max(len(fix_files), 1) if fix_files else 0.0

    analysis = CaseAnalysis(
        issue_key=issue_key,
        project=project,
        bug_hash=bug_hash,
        fix_hashes=fix_hashes,
        bug_commit_exists=bug_exists,
        fix_commit_exists=fix_exists,
        bug_commit_msg=bug_msg,
        fix_commit_msg=fix_msg,
        bug_files_changed=len(bug_files),
        fix_files_changed=len(fix_files),
        bug_diff_lines=bug_diff_lines,
        fix_diff_lines=fix_diff_lines,
        bug_is_merge=bug_merge,
        overlap_files=overlap,
        overlap_ratio=overlap_ratio,
        category="",
        notes="",
    )

    category, notes = _classify(analysis)
    analysis.category = category
    analysis.notes = notes
    return analysis


def _compute_adjusted_metrics(
    analyses: list[CaseAnalysis],
    v2_results_path: Path,
) -> dict:
    """Cross-reference noise labels with V2 eval results for adjusted metrics."""
    if not v2_results_path.exists():
        return {"error": "V2 results not found"}

    with open(v2_results_path) as f:
        v2 = json.load(f)

    noise_keys = {a.issue_key for a in analyses if a.category == "noise"}
    questionable_keys = {a.issue_key for a in analyses if a.category == "questionable"}
    plausible_keys = {a.issue_key for a in analyses if a.category == "plausible"}

    v2_hits = {c["issue_key"] for c in v2["per_case"] if c["hit_at_5"]}

    clean_cases = [c for c in v2["per_case"] if c["issue_key"] not in noise_keys]
    clean_hits = sum(1 for c in clean_cases if c["hit_at_5"])
    clean_total = len(clean_cases)

    plausible_cases = [c for c in v2["per_case"] if c["issue_key"] in plausible_keys]
    plausible_hits = sum(1 for c in plausible_cases if c["hit_at_5"])
    plausible_total = len(plausible_cases)

    return {
        "raw_hit_at_5": round(len(v2_hits) / len(v2["per_case"]), 3),
        "noise_cases_in_hits": sorted(v2_hits & noise_keys),
        "noise_cases_in_misses": sorted(noise_keys - v2_hits),
        "adjusted_hit_at_5_excl_noise": round(clean_hits / clean_total, 3) if clean_total else 0,
        "adjusted_denominator_excl_noise": clean_total,
        "adjusted_hit_at_5_plausible_only": round(plausible_hits / plausible_total, 3) if plausible_total else 0,
        "adjusted_denominator_plausible": plausible_total,
        "implication": (
            f"On plausible-only data ({plausible_total} cases), Hit@5 = "
            f"{plausible_hits}/{plausible_total} = "
            f"{plausible_hits / plausible_total:.0%}. "
            f"The {len(noise_keys)} noise cases inflate the denominator and "
            f"suppress the metric."
        ),
    }


def main() -> None:
    from commit_investigator.eval.ground_truth import GroundTruthGraph

    with open(EVAL_RESULTS) as f:
        eval_data = json.load(f)

    gt = GroundTruthGraph.from_replication_zip(str(REPLICATION_ZIP))

    results = []
    for case in eval_data["per_case"]:
        bug_hash = case["bug_hash"]
        chain = gt.get_chain(bug_hash)

        analysis = analyze_case(
            issue_key=case["issue_key"],
            project=case["project"],
            bug_hash=bug_hash,
            fix_hashes=chain.fix_hashes,
        )
        results.append(analysis)

    categories = {}
    for r in results:
        categories.setdefault(r.category, []).append(r.issue_key)

    total = len(results)
    noise_count = len(categories.get("noise", []))
    questionable_count = len(categories.get("questionable", []))
    clean_count = total - noise_count - questionable_count

    v2_results_path = PROJECT_ROOT / "results" / "v3-subagent-eval-v2" / "comparison.json"
    adjusted = _compute_adjusted_metrics(results, v2_results_path)

    report = {
        "total_cases": total,
        "summary": {
            "correct_or_plausible": clean_count,
            "questionable": questionable_count,
            "noise": noise_count,
            "estimated_noise_rate": round((noise_count + questionable_count * 0.5) / total, 3),
        },
        "adjusted_metrics": adjusted,
        "by_category": {k: v for k, v in sorted(categories.items())},
        "cases": [r.to_dict() for r in results],
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nGround Truth Noise Analysis — {total} cases")
    print(f"  Correct/Plausible: {clean_count}")
    print(f"  Questionable:      {questionable_count}")
    print(f"  Noise:             {noise_count}")
    print(f"  Est. noise rate:   {report['summary']['estimated_noise_rate']:.1%}")
    print(f"\nBy category:")
    for cat, cases in sorted(categories.items()):
        print(f"  {cat}: {', '.join(cases)}")
    if "error" not in adjusted:
        print(f"\nAdjusted metrics (V2 prompt):")
        print(f"  Raw Hit@5:              {adjusted['raw_hit_at_5']}")
        print(f"  Hit@5 excl noise:       {adjusted['adjusted_hit_at_5_excl_noise']} ({adjusted['adjusted_denominator_excl_noise']} cases)")
        print(f"  Hit@5 plausible only:   {adjusted['adjusted_hit_at_5_plausible_only']} ({adjusted['adjusted_denominator_plausible']} cases)")
        print(f"  Noise in hits:          {adjusted['noise_cases_in_hits']}")
        print(f"  Noise in misses:        {adjusted['noise_cases_in_misses']}")
    print(f"\nDetailed report: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
