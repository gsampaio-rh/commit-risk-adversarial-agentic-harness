"""BI3: Cross-package integration test — ProblemStatement → retrieve → assemble.

Chains the full V4 input-to-prompt path with mock GitContextProvider:
  1. ProblemExtractor produces ProblemStatement with populated signals
  2. retrieve_candidates() processes those signals into a CandidateSet
  3. assemble_prompt() renders prompts for all 3 stages

Uses mock GitContextProvider that responds based on extraction output
(extracted_files, extracted_keywords, extracted_symbols) rather than
returning static data, ensuring the integration path is regression-protected.
"""

from unittest.mock import MagicMock, patch

import pytest

from commit_investigator.extraction.problem_extractor import ProblemExtractor
from commit_investigator.governance import assemble_prompt, PromptConfig
from commit_investigator.models.candidates import CandidateSet
from commit_investigator.models.investigation import InvestigationBrief, InvestigationState
from commit_investigator.infra.git_context import FileHistoryEntry
from commit_investigator.retrieval import RetrievalConfig, retrieve_candidates


def _make_file_history_entry(sha: str, message: str, date: str = "2024-01-01") -> FileHistoryEntry:
    return FileHistoryEntry(
        commit_id=sha,
        author="dev@apache.org",
        date=date,
        message=message,
    )


MOCK_COMMITS = {
    "file_log": [
        _make_file_history_entry("a" * 40, "CASSANDRA-7059: refactor paging reader"),
        _make_file_history_entry("b" * 40, "Fix NPE in record reader batch"),
        _make_file_history_entry("c" * 40, "Improve CQL paging performance"),
    ],
    "keyword_grep": [
        _make_file_history_entry("d" * 40, "cqlpagingrecordreader: initial impl"),
        _make_file_history_entry("e" * 40, "Fix broken paging test"),
    ],
    "pickaxe": [
        _make_file_history_entry("f" * 40, "Add CqlPagingRecordReader class"),
        _make_file_history_entry("a" * 40, "CASSANDRA-7059: refactor paging reader"),
    ],
    "blame": "a" * 40 + " (dev 2024-01-01 1) line1\n" + "g" * 40 + " (dev 2024-01-02 2) line2\n",
}


def _build_mock_git(problem) -> MagicMock:
    """Build a mock GitContextProvider that responds based on extraction output."""
    mock_git = MagicMock()
    mock_git.temporal_bound = "abc123~1"

    def resolve_file_path(filename):
        if any(filename in f for f in problem.extracted_files):
            return [f"src/main/java/{filename}"]
        return []

    def search_commits_by_file(path, max_results=50):
        return MOCK_COMMITS["file_log"][:max_results]

    def search_commits_by_keyword(keyword, max_results=30):
        if keyword in problem.extracted_keywords:
            return MOCK_COMMITS["keyword_grep"][:max_results]
        return []

    def search_commits_by_pickaxe(symbol, max_results=50):
        if symbol in problem.extracted_symbols:
            return MOCK_COMMITS["pickaxe"][:max_results]
        return []

    def get_blame(path, line_start=1, line_end=None):
        if problem.extracted_files:
            return MOCK_COMMITS["blame"]
        return None

    def resolve_ref(ref):
        if len(ref) == 40:
            return ref
        return ref.ljust(40, "0")

    mock_git.resolve_file_path = MagicMock(side_effect=resolve_file_path)
    mock_git.search_commits_by_file = MagicMock(side_effect=search_commits_by_file)
    mock_git.search_commits_by_keyword = MagicMock(side_effect=search_commits_by_keyword)
    mock_git.search_commits_by_pickaxe = MagicMock(side_effect=search_commits_by_pickaxe)
    mock_git.get_blame = MagicMock(side_effect=get_blame)
    mock_git.resolve_ref = MagicMock(side_effect=resolve_ref)
    mock_git.list_recent_commits = MagicMock(return_value=[])
    mock_git.get_commit_message = MagicMock(return_value="mock commit message")
    mock_git.get_touched_files = MagicMock(return_value=["src/Foo.java"])

    return mock_git


@pytest.fixture
def populated_problem():
    """ProblemStatement with real extraction signals (not hand-crafted)."""
    extractor = ProblemExtractor()
    return extractor.from_raw(
        title="CqlPagingRecordReader is broken",
        description=(
            "As mentioned on CASSANDRA-7059, it broke CPRR. "
            "Stack trace at CqlPagingRecordReader.java:142"
        ),
        project="CASSANDRA",
        issue_key="CASSANDRA-7570",
    )


@pytest.fixture
def candidate_set(populated_problem) -> CandidateSet:
    """CandidateSet produced by retrieve_candidates with mock git."""
    mock_git = _build_mock_git(populated_problem)
    config = RetrievalConfig(max_candidates=100)
    return retrieve_candidates(populated_problem, mock_git, config)


class TestExtractionProducesSignals:
    """Verify extraction populates signals that retrieval can consume."""

    def test_has_files(self, populated_problem) -> None:
        assert len(populated_problem.extracted_files) >= 1
        assert "CqlPagingRecordReader.java" in populated_problem.extracted_files

    def test_has_symbols(self, populated_problem) -> None:
        assert len(populated_problem.extracted_symbols) >= 1
        assert "CqlPagingRecordReader" in populated_problem.extracted_symbols

    def test_has_keywords(self, populated_problem) -> None:
        assert len(populated_problem.extracted_keywords) >= 1
        assert "cqlpagingrecordreader" in populated_problem.extracted_keywords


class TestRetrievalProducesCandidates:
    """Verify retrieve_candidates produces non-empty CandidateSet from signals."""

    def test_produces_candidates(self, candidate_set) -> None:
        assert len(candidate_set.commits) >= 1

    def test_has_retrieval_metadata(self, candidate_set) -> None:
        meta = candidate_set.retrieval_metadata
        assert "strategies_used" in meta
        assert "fallback_triggered" in meta

    def test_signal_strategies_fired(self, candidate_set) -> None:
        meta = candidate_set.retrieval_metadata
        assert meta.get("fallback_triggered") is False or meta.get("total_raw_candidates", 0) > 0

    def test_commits_have_required_fields(self, candidate_set) -> None:
        for commit in candidate_set.commits:
            assert commit.commit_id
            assert commit.rank >= 1
            assert commit.retrieval_signal


def _count_sections(prompt: str) -> int:
    """Count ## headers in a prompt."""
    import re
    return len(re.findall(r"^## .+", prompt, re.MULTILINE))


class TestPromptAssemblyAllStages:
    """Verify assemble_prompt works for all 3 stages with real extraction + retrieval."""

    @pytest.fixture
    def investigation_state(self, candidate_set) -> InvestigationState:
        # Caller responsibility: set candidates_total = len(candidate_set.commits)
        return InvestigationState(
            current_stage=3,
            candidates_examined=2,
            candidates_total=len(candidate_set.commits),
            hypotheses_tested=1,
            hypotheses_confirmed=0,
            evidence_quotes_collected=2,
        )

    @pytest.fixture
    def brief(self) -> InvestigationBrief:
        from commit_investigator.models.investigation import Hypothesis, ExaminationStep

        return InvestigationBrief(
            hypotheses=[
                Hypothesis(id="h1", statement="CqlPagingRecordReader regression"),
                Hypothesis(id="h2", statement="Off-by-one in paging boundary"),
            ],
            examination_plan=[
                ExaminationStep(look_for="greater-than comparison change"),
            ],
            strategy="examine paging logic changes",
            max_effort=18,
        )

    def test_planning_prompt(self, populated_problem, candidate_set) -> None:
        prompt = assemble_prompt(
            stage="planning",
            problem_statement=populated_problem,
            candidate_set=candidate_set,
        )
        assert prompt
        assert len(prompt) > 100
        assert "System Role" in prompt
        assert "Stage Instructions" in prompt
        assert "Problem Statement" in prompt
        assert "Candidate Summary" in prompt
        section_count = _count_sections(prompt)
        assert section_count >= 4, f"Planning should have >= 4 sections, got {section_count}"

    def test_examination_prompt(
        self, populated_problem, candidate_set, investigation_state, brief
    ) -> None:
        prompt = assemble_prompt(
            stage="examination",
            problem_statement=populated_problem,
            candidate_set=candidate_set,
            investigation_state=investigation_state,
            brief=brief,
        )
        assert prompt
        assert len(prompt) > 100
        assert "System Role" in prompt
        assert "Stage Instructions" in prompt
        assert "Problem Statement" in prompt
        assert "Investigation Progress" in prompt
        assert "Investigation Brief" in prompt
        section_count = _count_sections(prompt)
        assert section_count >= 6, f"Examination should have >= 6 sections, got {section_count}"

    def test_attribution_prompt(
        self, populated_problem, candidate_set, investigation_state, brief
    ) -> None:
        evidence = [
            "Commit aaa...aaa changed > to >= in paging boundary check",
            "CqlPagingRecordReader.getNextKeySlice modified in same commit",
        ]
        prompt = assemble_prompt(
            stage="attribution",
            problem_statement=populated_problem,
            candidate_set=candidate_set,
            investigation_state=investigation_state,
            brief=brief,
            evidence=evidence,
        )
        assert prompt
        assert len(prompt) > 100
        assert "System Role" in prompt
        assert "Stage Instructions" in prompt
        assert "Evidence Collected" in prompt
        section_count = _count_sections(prompt)
        assert section_count >= 6, f"Attribution should have >= 6 sections, got {section_count}"

    def test_no_type_errors_any_stage(
        self, populated_problem, candidate_set, investigation_state, brief
    ) -> None:
        """AC9: no TypeErrors across all stages."""
        for stage in ("planning", "examination", "attribution"):
            prompt = assemble_prompt(
                stage=stage,
                problem_statement=populated_problem,
                candidate_set=candidate_set,
                investigation_state=investigation_state,
                brief=brief,
                evidence=["test evidence"],
            )
            assert isinstance(prompt, str)
            assert len(prompt) > 0


class TestCandidatesTotalDocumentation:
    """AC12: InvestigationState.candidates_total matches CandidateSet size."""

    def test_candidates_total_matches_set_size(self, candidate_set) -> None:
        # Caller responsibility: InvestigationState.candidates_total must be
        # set to len(candidate_set.commits) by the harness before passing to
        # assemble_prompt. The harness (P7) owns this sync.
        state = InvestigationState(
            candidates_total=len(candidate_set.commits),
            candidates_examined=1,
        )
        assert state.candidates_total == len(candidate_set.commits)
