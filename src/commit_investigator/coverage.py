"""Coverage report: validates ground truth chain completeness against train/test CSVs.

Run as CLI: python -m commit_investigator.coverage --zip <path> --train <path> [--test <path>]
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from commit_investigator.ground_truth import GroundTruthGraph


@dataclass
class ProjectCoverage:
    """Coverage statistics for a single project."""

    project: str
    total_buggy: int
    with_fix_link: int
    with_issue_link: int

    @property
    def fix_coverage_pct(self) -> float:
        if self.total_buggy == 0:
            return 0.0
        return (self.with_fix_link / self.total_buggy) * 100

    @property
    def issue_coverage_pct(self) -> float:
        if self.total_buggy == 0:
            return 0.0
        return (self.with_issue_link / self.total_buggy) * 100


@dataclass
class CoverageReport:
    """Full coverage report across all projects and splits."""

    per_project: list[ProjectCoverage]
    total_buggy: int
    total_with_fix: int
    total_with_issue: int
    split_name: str

    @property
    def fix_coverage_pct(self) -> float:
        if self.total_buggy == 0:
            return 0.0
        return (self.total_with_fix / self.total_buggy) * 100

    @property
    def issue_coverage_pct(self) -> float:
        if self.total_buggy == 0:
            return 0.0
        return (self.total_with_issue / self.total_buggy) * 100


def coverage_report(
    graph: GroundTruthGraph,
    csv_path: str | Path,
    split_name: str = "unknown",
) -> CoverageReport:
    """Compute coverage of ground truth graph against a labeled CSV split.

    Checks how many buggy=True rows have entries in the graph (fix linkage + issue linkage).
    """
    csv_path = Path(csv_path)
    project_stats: dict[str, dict[str, int]] = {}

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            buggy = row.get("buggy", "").strip()
            if buggy not in ("True", "true", "1"):
                continue

            commit_id = row["commit_id"].strip()
            project = _normalize_project(row.get("project", "unknown"))

            if project not in project_stats:
                project_stats[project] = {"total": 0, "fix": 0, "issue": 0}

            project_stats[project]["total"] += 1

            if graph.has_bug(commit_id):
                project_stats[project]["fix"] += 1

            chain = graph.get_chain(commit_id)
            if chain.issue_keys:
                project_stats[project]["issue"] += 1

    per_project = []
    for proj, stats in sorted(project_stats.items()):
        per_project.append(
            ProjectCoverage(
                project=proj,
                total_buggy=stats["total"],
                with_fix_link=stats["fix"],
                with_issue_link=stats["issue"],
            )
        )

    total_buggy = sum(p.total_buggy for p in per_project)
    total_fix = sum(p.with_fix_link for p in per_project)
    total_issue = sum(p.with_issue_link for p in per_project)

    return CoverageReport(
        per_project=per_project,
        total_buggy=total_buggy,
        total_with_fix=total_fix,
        total_with_issue=total_issue,
        split_name=split_name,
    )


def print_report(report: CoverageReport) -> None:
    """Print a human-readable coverage report table."""
    print(f"\n{'='*70}")
    print(f"  Ground Truth Coverage Report — {report.split_name}")
    print(f"{'='*70}")
    print(f"{'Project':<20} {'Buggy':>8} {'Fix Link':>10} {'Issue Link':>12} {'Fix%':>7} {'Issue%':>7}")
    print(f"{'-'*70}")

    for p in report.per_project:
        print(
            f"{p.project:<20} {p.total_buggy:>8} {p.with_fix_link:>10} "
            f"{p.with_issue_link:>12} {p.fix_coverage_pct:>6.1f}% {p.issue_coverage_pct:>6.1f}%"
        )

    print(f"{'-'*70}")
    print(
        f"{'TOTAL':<20} {report.total_buggy:>8} {report.total_with_fix:>10} "
        f"{report.total_with_issue:>12} {report.fix_coverage_pct:>6.1f}% "
        f"{report.issue_coverage_pct:>6.1f}%"
    )
    print(f"{'='*70}\n")


def _normalize_project(project_raw: str) -> str:
    """Normalize project name from CSV format (e.g., 'apache/camel' → 'CAMEL')."""
    name = project_raw.strip()
    if "/" in name:
        name = name.split("/")[-1]
    return name.upper()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate ground truth chain coverage against ApacheJIT splits"
    )
    parser.add_argument(
        "--zip",
        required=True,
        help="Path to apachejit_dataset_replication.zip",
    )
    parser.add_argument(
        "--train",
        required=True,
        help="Path to train CSV (apachejit_train.csv)",
    )
    parser.add_argument(
        "--test",
        required=False,
        help="Path to test CSV (optional)",
    )

    args = parser.parse_args()

    print("Loading ground truth graph from replication zip...")
    graph = GroundTruthGraph.from_replication_zip(args.zip)
    print(
        f"  Loaded: {graph.total_bug_commits} bug commits, "
        f"{graph.total_fix_commits} fix commits, "
        f"{graph.total_issue_links} issue links across {len(graph.projects)} projects"
    )
    print(f"  Projects: {', '.join(graph.projects)}")

    train_report = coverage_report(graph, args.train, split_name="train")
    print_report(train_report)

    if args.test:
        test_report = coverage_report(graph, args.test, split_name="test")
        print_report(test_report)

    all_pass = train_report.fix_coverage_pct > 0
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
