"""Ground truth graph: indexes ApacheJIT replication package linkage files.

Resolves the chain: bug_hash → fix_hash → issue_key for all 15 projects.
Used exclusively at eval time — never during agent investigation.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CommitChain:
    """Full ground truth chain for a bug-inducing commit."""

    bug_hash: str
    fix_hashes: list[str]
    issue_keys: list[str]


@dataclass
class GroundTruthGraph:
    """Indexes bug→fix and commit→issue linkage from the ApacheJIT replication package.

    Loads commit_links_{PROJECT}.csv and {PROJECT}.csv from inside the zip
    without extracting files to disk.
    """

    _bug_to_fixes: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    _fix_to_bugs: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    _commit_to_issues: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    _issue_to_commits: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    _projects: set[str] = field(default_factory=set)

    @property
    def projects(self) -> list[str]:
        """All projects loaded from the replication package."""
        return sorted(self._projects)

    @classmethod
    def from_replication_zip(cls, zip_path: str | Path) -> GroundTruthGraph:
        """Build graph by reading linkage CSVs directly from the replication zip."""
        graph = cls()
        zip_path = Path(zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()

            commit_link_files = [n for n in names if "commit_links_" in n and n.endswith(".csv")]
            for cl_file in commit_link_files:
                project = _extract_project_from_commit_links(cl_file)
                graph._projects.add(project)
                graph._load_commit_links(zf, cl_file)

            project_csv_files = [
                n
                for n in names
                if n.endswith(".csv")
                and "commit_links_" not in n
                and _is_project_csv(n, graph._projects)
            ]
            for pf in project_csv_files:
                graph._load_project_issues(zf, pf)

        return graph

    def get_fix_commits(self, bug_hash: str) -> list[str]:
        """Return fix hashes that fixed the given bug-inducing commit."""
        return self._bug_to_fixes.get(bug_hash, [])

    def get_bug_commits(self, fix_hash: str) -> list[str]:
        """Return bug-inducing hashes that the given fix addresses."""
        return self._fix_to_bugs.get(fix_hash, [])

    def get_issue_keys(self, commit_id: str) -> list[str]:
        """Return JIRA issue keys linked to a commit (fix or bug)."""
        return self._commit_to_issues.get(commit_id, [])

    def get_chain(self, bug_hash: str) -> CommitChain:
        """Return full ground truth chain for a bug-inducing commit."""
        fix_hashes = self.get_fix_commits(bug_hash)
        issue_keys: list[str] = []
        seen_issues: set[str] = set()

        for fh in fix_hashes:
            for ik in self.get_issue_keys(fh):
                if ik not in seen_issues:
                    issue_keys.append(ik)
                    seen_issues.add(ik)

        for ik in self.get_issue_keys(bug_hash):
            if ik not in seen_issues:
                issue_keys.append(ik)
                seen_issues.add(ik)

        return CommitChain(bug_hash=bug_hash, fix_hashes=fix_hashes, issue_keys=issue_keys)

    def has_bug(self, commit_id: str) -> bool:
        """Check if a commit is known as a bug-inducing commit."""
        return commit_id in self._bug_to_fixes

    def has_fix(self, commit_id: str) -> bool:
        """Check if a commit is known as a fixing commit."""
        return commit_id in self._fix_to_bugs

    @property
    def total_bug_commits(self) -> int:
        return len(self._bug_to_fixes)

    @property
    def total_fix_commits(self) -> int:
        return len(self._fix_to_bugs)

    @property
    def total_issue_links(self) -> int:
        return len(self._commit_to_issues)

    def _load_commit_links(self, zf: zipfile.ZipFile, filename: str) -> None:
        """Parse commit_links_{PROJECT}.csv: fix_hash,fix_date,bug_hash,bug_date,project."""
        content = zf.read(filename).decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))

        for row in reader:
            fix_hash = row["fix_hash"].strip()
            bug_hash = row["bug_hash"].strip()

            if fix_hash and bug_hash:
                self._bug_to_fixes[bug_hash].append(fix_hash)
                self._fix_to_bugs[fix_hash].append(bug_hash)

    def _load_project_issues(self, zf: zipfile.ZipFile, filename: str) -> None:
        """Parse {PROJECT}.csv: issue_key,commit_id."""
        content = zf.read(filename).decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))

        for row in reader:
            issue_key = row["issue_key"].strip()
            commit_id = row["commit_id"].strip()

            if issue_key and commit_id:
                self._commit_to_issues[commit_id].append(issue_key)
                self._issue_to_commits[issue_key].append(commit_id)


def _extract_project_from_commit_links(filename: str) -> str:
    """Extract project name from path like 'apachejit/data/commit_links_CAMEL.csv'."""
    basename = Path(filename).stem  # commit_links_CAMEL
    return basename.replace("commit_links_", "")


def _is_project_csv(filename: str, known_projects: set[str]) -> bool:
    """Check if a CSV file is a project issue-mapping file (e.g., CAMEL.csv)."""
    basename = Path(filename).stem
    parent = Path(filename).parent.name
    return parent == "data" and basename in known_projects
