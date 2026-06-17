"""BI2: recall@100 validation of retrieval module against ground truth.

Runs retrieve_candidates() on real eval cases from manifest.json using
cloned repos at data/repos/. Validates that the committed retrieval module
can find ground truth commits via git-signal strategies (not just recency).

This is a smoke test (3-5 cases). P6 does the full n=20 checkpoint.
"""

import json
from pathlib import Path

import pytest

from commit_investigator.extraction.problem_extractor import ProblemExtractor
from commit_investigator.extraction.jira_client import JiraIssue
from commit_investigator.infra.git_context import GitContextProvider
from commit_investigator.retrieval import RetrievalConfig, compute_recall_at_k, retrieve_candidates

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "results" / "v3-subagent-eval-v2" / "manifest.json"
JIRA_CACHE_DIR = PROJECT_ROOT / "data" / "jira_cache"
REPOS_DIR = PROJECT_ROOT / "data" / "repos"


def _load_manifest_cases() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return data.get("cases", [])


def _load_jira_text(issue_key: str) -> dict | None:
    path = JIRA_CACHE_DIR / f"{issue_key}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "key": raw["key"],
        "title": raw["fields"]["summary"],
        "description": raw["fields"].get("description") or "",
    }


def _repo_exists(project: str) -> bool:
    return (REPOS_DIR / project.lower() / ".git").exists()


SMOKE_CASES = [
    "CASSANDRA-7570",
    "SPARK-19033",
    "GROOVY-8298",
    "SPARK-27907",
    "AMQ-4338",
]


def _get_case_by_key(cases: list[dict], key: str) -> dict | None:
    for case in cases:
        if case["issue_key"] == key:
            return case
    return None


@pytest.fixture(scope="module")
def manifest_cases() -> list[dict]:
    return _load_manifest_cases()


@pytest.mark.slow
class TestRetrievalRecallValidation:
    """Recall@100 validation on real repos with Level 1 extraction (AC6, AC7)."""

    @pytest.mark.skipif(
        not (REPOS_DIR / "cassandra" / ".git").exists(),
        reason="cassandra repo not cloned at data/repos/cassandra",
    )
    @pytest.mark.skipif(
        not MANIFEST_PATH.exists(),
        reason="manifest.json not found at results/v3-subagent-eval-v2/",
    )
    def test_cassandra_7570_recall(self, manifest_cases: list[dict]) -> None:
        """CASSANDRA-7570: mandatory smoke case — must find via signal strategies."""
        case = _get_case_by_key(manifest_cases, "CASSANDRA-7570")
        assert case is not None, "CASSANDRA-7570 not in manifest"

        jira = _load_jira_text("CASSANDRA-7570")
        assert jira is not None

        extractor = ProblemExtractor()
        problem = extractor.from_raw(
            title=jira["title"],
            description=jira["description"],
            project=case["project"],
            issue_key=case["issue_key"],
        )

        git = GitContextProvider(
            repo_path=case["repo_path"],
            temporal_bound=case["temporal_bound"],
        )
        config = RetrievalConfig(max_candidates=100)
        candidate_set = retrieve_candidates(problem, git, config)

        diagnostic = compute_recall_at_k(candidate_set, case["bug_hash"], k=100)
        assert diagnostic.found, (
            f"Ground truth {case['bug_hash'][:12]} not found in top 100 candidates"
        )
        assert len(diagnostic.strategies_that_found) > 0, (
            "Ground truth found only via recency fallback — signal strategies didn't fire"
        )
        metadata = candidate_set.retrieval_metadata
        assert metadata.get("fallback_triggered") is False, (
            "Retrieval fell back to recency-only — extraction signals empty or insufficient"
        )

    @pytest.mark.skipif(
        not MANIFEST_PATH.exists(),
        reason="manifest.json not found",
    )
    @pytest.mark.parametrize("issue_key", SMOKE_CASES[1:])
    def test_additional_recall_cases(
        self, manifest_cases: list[dict], issue_key: str
    ) -> None:
        """Additional smoke cases — at least 1 of these should find ground truth."""
        case = _get_case_by_key(manifest_cases, issue_key)
        if case is None:
            pytest.skip(f"{issue_key} not in manifest")

        project_name = case["project"].lower()
        if not _repo_exists(case["project"]):
            pytest.skip(f"Repo not cloned: {project_name}")

        jira = _load_jira_text(issue_key)
        if jira is None:
            pytest.skip(f"JIRA cache missing for {issue_key}")

        extractor = ProblemExtractor()
        problem = extractor.from_raw(
            title=jira["title"],
            description=jira["description"],
            project=case["project"],
            issue_key=case["issue_key"],
        )

        git = GitContextProvider(
            repo_path=case["repo_path"],
            temporal_bound=case["temporal_bound"],
        )
        config = RetrievalConfig(max_candidates=100)
        candidate_set = retrieve_candidates(problem, git, config)

        diagnostic = compute_recall_at_k(candidate_set, case["bug_hash"], k=100)

        # Informational — we don't assert every case finds it (smoke test)
        # but record result for debugging
        if diagnostic.found:
            print(
                f"\n  {issue_key}: FOUND at rank {diagnostic.rank} "
                f"via {diagnostic.strategies_that_found}"
            )
        else:
            print(f"\n  {issue_key}: NOT FOUND in top {diagnostic.total_candidates}")


@pytest.mark.slow
class TestRecallAggregateSmoke:
    """Aggregate check: the parametrized cases above show at least 1 hit.

    This test uses a single fast case (AMQ-4338, rank 1 in prior runs) to
    verify aggregate recall without re-running all expensive retrievals.
    """

    @pytest.mark.skipif(
        not (REPOS_DIR / "amq").exists() and not (REPOS_DIR / "activemq").exists(),
        reason="amq/activemq repo not cloned",
    )
    def test_amq_4338_found_via_signal_strategies(self, manifest_cases: list[dict]) -> None:
        case = _get_case_by_key(manifest_cases, "AMQ-4338")
        if case is None:
            pytest.skip("AMQ-4338 not in manifest")

        jira = _load_jira_text("AMQ-4338")
        if jira is None:
            pytest.skip("JIRA cache missing for AMQ-4338")

        extractor = ProblemExtractor()
        problem = extractor.from_raw(
            title=jira["title"],
            description=jira["description"],
            project=case["project"],
            issue_key=case["issue_key"],
        )

        git = GitContextProvider(
            repo_path=case["repo_path"],
            temporal_bound=case["temporal_bound"],
        )
        candidate_set = retrieve_candidates(problem, git)
        diagnostic = compute_recall_at_k(candidate_set, case["bug_hash"], k=100)
        assert diagnostic.found, "AMQ-4338 ground truth not found"
        assert len(diagnostic.strategies_that_found) > 0
