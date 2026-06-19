"""Tests for V4 deterministic retrieval module."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from commit_investigator.infra.git_context import FileHistoryEntry
from commit_investigator.retrieval import (
    RecallDiagnostic,
    RetrievalConfig,
    compute_recall_at_k,
    retrieve_candidates,
)
from commit_investigator.retrieval.strategies import (
    dedupe_preserve_order,
    merge_hits,
    parse_blame_shas,
    run_blame,
    run_file_log,
    run_keyword_grep,
    run_pickaxe,
)


def _sha(label: str) -> str:
    """Build a deterministic 40-char hex SHA from a single-char label."""
    return (label * 40)[:40]


def _entry(sha_label: str, message: str = "msg", date: str = "2024-01-01") -> FileHistoryEntry:
    return FileHistoryEntry(
        commit_id=_sha(sha_label),
        author="author",
        date=date,
        message=message,
    )


@dataclass
class FakeProblem:
    title: str = "bug"
    description: str = "desc"
    project: str = "SPARK"
    extracted_files: list[str] = field(default_factory=list)
    extracted_symbols: list[str] = field(default_factory=list)
    extracted_keywords: list[str] = field(default_factory=list)


def _mock_git(**overrides: object) -> MagicMock:
    git = MagicMock()
    git.temporal_bound = overrides.get("temporal_bound", "abc123~1")
    git.resolve_file_path.return_value = overrides.get("resolve_paths", [])
    git.search_commits_by_file.return_value = overrides.get("file_entries", [])
    git.search_commits_by_keyword.return_value = overrides.get("keyword_entries", [])
    git.search_commits_by_pickaxe.return_value = overrides.get("pickaxe_entries", [])
    git.get_blame.return_value = overrides.get("blame_output")
    git.list_recent_commits.return_value = overrides.get("recent_entries", [])
    git.resolve_ref.side_effect = lambda ref: _sha(ref[0]) if ref and len(ref) < 40 else (ref.lower() if ref else None)
    git.get_touched_files.return_value = overrides.get("touched_files", ["src/Foo.java"])
    git.get_commit_message.return_value = overrides.get("commit_message", "full message")
    return git


class TestPackageExports:
    def test_import_public_api(self) -> None:
        from commit_investigator.retrieval import (
            RecallDiagnostic,
            RetrievalConfig,
            compute_recall_at_k,
            retrieve_candidates,
        )

        assert callable(retrieve_candidates)
        assert callable(compute_recall_at_k)


class TestRetrievalConfig:
    def test_defaults(self) -> None:
        cfg = RetrievalConfig()
        assert cfg.max_candidates == 100
        assert cfg.strategies == ["file_log", "keyword_grep", "pickaxe", "blame", "localization_blame"]
        assert cfg.fallback_recency_threshold == 10
        assert cfg.file_log_per_file == 50
        assert cfg.keyword_grep_per_kw == 30
        assert cfg.pickaxe_per_symbol == 50
        assert cfg.blame_per_file == 100

    def test_override_max_candidates(self) -> None:
        cfg = RetrievalConfig(max_candidates=50)
        assert cfg.max_candidates == 50

    def test_only_blame_strategy_invoked(self) -> None:
        git = _mock_git(
            resolve_paths=["src/Foo.java"],
            blame_output=f"{_sha('a')[:7]} (Author 2024-01-01 1) line\n",
        )
        problem = FakeProblem(extracted_files=["Foo.java"])
        cfg = RetrievalConfig(strategies=["blame", "invalid_name"], max_candidates=10)
        retrieve_candidates(problem, git, cfg)
        git.search_commits_by_file.assert_not_called()
        git.search_commits_by_keyword.assert_not_called()
        git.search_commits_by_pickaxe.assert_not_called()
        git.get_blame.assert_called_once()


class TestCombinedRanking:
    def test_signal_count_and_best_rank_ordering(self) -> None:
        sha_a = _sha("a")
        git = _mock_git()
        git.search_commits_by_file.return_value = [_entry("a"), _entry("b")]
        git.search_commits_by_keyword.return_value = [_entry("a"), _entry("c")]
        git.search_commits_by_pickaxe.return_value = [_entry("a")]
        git.resolve_file_path.return_value = []

        problem = FakeProblem(extracted_keywords=["bug"], extracted_symbols=["Sym"])
        result = retrieve_candidates(
            problem,
            git,
            RetrievalConfig(
                strategies=["file_log", "keyword_grep", "pickaxe"],
                max_candidates=10,
                fallback_recency_threshold=0,
            ),
        )

        assert result.commits[0].commit_id == sha_a


class TestFileLogStrategy:
    def test_multi_path_dedup_and_max_results(self) -> None:
        git = _mock_git()
        git.resolve_file_path.return_value = ["path/a.java", "path/b.java"]
        git.search_commits_by_file.side_effect = [
            [_entry("a"), _entry("b")],
            [_entry("a"), _entry("c")],
        ]
        problem = FakeProblem(extracted_files=["Foo.java"])

        hits = run_file_log(problem, git, 25)
        assert git.search_commits_by_file.call_count == 2
        git.search_commits_by_file.assert_any_call("path/a.java", max_results=25)
        assert len(hits) == 3


class TestKeywordGrepStrategy:
    def test_unique_keywords_and_max_results(self) -> None:
        git = _mock_git()
        git.search_commits_by_keyword.return_value = [_entry("a")]
        problem = FakeProblem(extracted_keywords=["bug", "bug", "fix"])

        run_keyword_grep(problem, git, 15)
        assert git.search_commits_by_keyword.call_count == 2
        git.search_commits_by_keyword.assert_any_call("bug", max_results=15)
        git.search_commits_by_keyword.assert_any_call("fix", max_results=15)


class TestPickaxeStrategy:
    def test_deduped_symbols(self) -> None:
        git = _mock_git()
        git.search_commits_by_pickaxe.return_value = [_entry("a")]
        problem = FakeProblem(extracted_symbols=["Foo", "Foo", "Bar"])

        run_pickaxe(problem, git, 40)
        assert git.search_commits_by_pickaxe.call_count == 2


class TestBlameStrategy:
    def test_abbreviated_and_boundary_shas(self) -> None:
        short = _sha("a")[:7]
        boundary = _sha("b")[:7]
        blame = (
            f"^{boundary} (Author 2024-01-01 1) old line\n"
            f"{short} (Author 2024-01-01 2) current line\n"
        )
        git = _mock_git(blame_output=blame, resolve_paths=["src/Foo.java"])
        git.resolve_ref.side_effect = lambda ref: _sha(ref[0]) if len(ref) < 40 else ref.lower()

        hits = run_blame(FakeProblem(extracted_files=["Foo.java"]), git, 100)
        assert len(hits) == 1
        assert hits[0].commit_id == _sha("a")

    def test_no_private_resolve_ref_in_retrieval_package(self) -> None:
        retrieval_dir = Path(__file__).resolve().parents[1] / "src" / "commit_investigator" / "retrieval"
        for path in retrieval_dir.glob("*.py"):
            assert "_resolve_ref" not in path.read_text()


class TestRankingTieBreaks:
    def test_best_rank_and_commit_id_tie_break(self) -> None:
        from commit_investigator.retrieval.strategies import StrategyHit

        hits = [
            StrategyHit(_sha("b"), "file_log", 2),
            StrategyHit(_sha("a"), "keyword_grep", 1),
            StrategyHit(_sha("c"), "pickaxe", 1),
        ]
        merged = merge_hits(hits)
        ranked = sorted(
            merged.values(),
            key=lambda item: (-len(item.strategies), item.best_rank, item.commit_id),
        )
        assert [c.commit_id for c in ranked] == [_sha("a"), _sha("c"), _sha("b")]


class TestRecencyFallback:
    def test_empty_signals_trigger_fallback(self) -> None:
        git = _mock_git(recent_entries=[_entry("r", "recent msg")])
        result = retrieve_candidates(FakeProblem(), git, RetrievalConfig(max_candidates=5))
        assert result.retrieval_metadata["fallback_triggered"] is True
        assert len(result.commits) == 1
        assert result.commits[0].retrieval_signal == "recency_fallback"

    def test_fallback_dedupes_existing_signal_commits(self) -> None:
        git = _mock_git()
        git.search_commits_by_keyword.return_value = [_entry("a")]
        git.list_recent_commits.return_value = [_entry("a"), _entry("b")]
        problem = FakeProblem(extracted_keywords=["bug"])
        cfg = RetrievalConfig(
            strategies=["keyword_grep"],
            max_candidates=10,
            fallback_recency_threshold=5,
        )
        result = retrieve_candidates(problem, git, cfg)
        fallback_commits = [c for c in result.commits if c.retrieval_signal == "recency_fallback"]
        assert len(fallback_commits) == 1
        assert fallback_commits[0].commit_id == _sha("b")


class TestMetadata:
    def test_metadata_keys_and_signal_counts(self) -> None:
        git = _mock_git()
        git.search_commits_by_file.return_value = [_entry("a"), _entry("b")]
        git.search_commits_by_keyword.return_value = [_entry("a")]
        git.resolve_file_path.return_value = ["src/f.java"]
        problem = FakeProblem(extracted_files=["f.java"], extracted_keywords=["k"])
        cfg = RetrievalConfig(
            strategies=["file_log", "keyword_grep"],
            max_candidates=10,
            fallback_recency_threshold=0,
        )
        result = retrieve_candidates(problem, git, cfg)
        meta = result.retrieval_metadata
        assert set(meta.keys()) == {
            "strategies_used",
            "total_raw_candidates",
            "signal_counts",
            "fallback_triggered",
            "temporal_bound",
        }
        assert meta["total_raw_candidates"] == 2
        assert sum(meta["signal_counts"].values()) == meta["total_raw_candidates"]


class TestCandidateCommitFields:
    def test_retrieval_signal_and_touched_files(self) -> None:
        git = _mock_git()
        git.resolve_file_path.return_value = ["src/F.java"]
        git.search_commits_by_file.return_value = [_entry("a", "file summary")]
        git.get_blame.return_value = f"{_sha('a')[:7]} (Author 2024-01-01 1) line\n"
        problem = FakeProblem(extracted_files=["F.java"])
        cfg = RetrievalConfig(
            strategies=["file_log", "blame"],
            max_candidates=5,
            fallback_recency_threshold=0,
        )
        result = retrieve_candidates(problem, git, cfg)
        commit = result.commits[0]
        assert commit.retrieval_signal == "blame,file_log"
        assert commit.summary == "file summary"
        git.get_touched_files.assert_called()


class TestMaxCandidatesCap:
    def test_trims_lowest_ranked(self) -> None:
        git = _mock_git()
        entries = [_entry(label) for label in "abcdefgh"]
        git.search_commits_by_keyword.return_value = entries
        problem = FakeProblem(extracted_keywords=["k"])
        cfg = RetrievalConfig(
            strategies=["keyword_grep"],
            max_candidates=5,
            fallback_recency_threshold=0,
        )
        result = retrieve_candidates(problem, git, cfg)
        assert len(result.commits) == 5
        assert [c.rank for c in result.commits] == [1, 2, 3, 4, 5]


class TestRecallDiagnostic:
    def _build_set(self, labels: list[str]) -> object:
        from commit_investigator.models.candidates import CandidateCommit, CandidateSet

        commits = [
            CandidateCommit(
                commit_id=_sha(label),
                rank=i,
                retrieval_signal="keyword_grep",
                summary="s",
            )
            for i, label in enumerate(labels, start=1)
        ]
        return CandidateSet(commits=commits)

    def test_found_at_rank(self) -> None:
        candidate_set = self._build_set(list("abcde"))
        diag = compute_recall_at_k(candidate_set, _sha("c"), k=100)
        assert isinstance(diag, RecallDiagnostic)
        assert diag.found is True
        assert diag.rank == 3
        assert diag.strategies_that_found == ["keyword_grep"]

    def test_not_found(self) -> None:
        candidate_set = self._build_set(list("abc"))
        diag = compute_recall_at_k(candidate_set, _sha("z"), k=100)
        assert diag.found is False
        assert diag.rank is None

    def test_beyond_k(self) -> None:
        labels = [chr(c) for c in range(ord("a"), ord("a") + 150)]
        candidate_set = self._build_set(labels)
        beyond_label = chr(ord("a") + 100)
        diag = compute_recall_at_k(candidate_set, _sha(beyond_label), k=100)
        assert diag.found is False


class TestPackageIsolation:
    def test_no_forbidden_imports(self) -> None:
        retrieval_dir = Path(__file__).resolve().parents[1] / "src" / "commit_investigator" / "retrieval"
        forbidden = ("agent", "eval", "governance")
        for path in retrieval_dir.glob("*.py"):
            for module in forbidden:
                assert f"from commit_investigator.{module}" not in path.read_text()


class TestFileSizing:
    def test_modules_under_300_lines(self) -> None:
        retrieval_dir = Path(__file__).resolve().parents[1] / "src" / "commit_investigator" / "retrieval"
        for path in retrieval_dir.glob("*.py"):
            line_count = len(path.read_text().splitlines())
            assert line_count <= 300, f"{path.name} has {line_count} lines"


class TestEdgeCases:
    def test_e2_unresolvable_file_skipped(self) -> None:
        git = _mock_git()
        git.resolve_file_path.side_effect = lambda name: [] if name == "missing.java" else ["ok.java"]
        git.search_commits_by_keyword.return_value = [_entry("a")]
        problem = FakeProblem(extracted_files=["missing.java"], extracted_keywords=["bug"])
        cfg = RetrievalConfig(strategies=["file_log", "blame", "keyword_grep"], fallback_recency_threshold=0)
        result = retrieve_candidates(problem, git, cfg)
        git.get_blame.assert_not_called()
        git.search_commits_by_file.assert_not_called()
        assert len(result.commits) == 1

    def test_e3_all_strategies_same_commit(self) -> None:
        git = _mock_git(resolve_paths=["src/F.java"])
        git.search_commits_by_file.return_value = [_entry("a")]
        git.search_commits_by_keyword.return_value = [_entry("a")]
        git.search_commits_by_pickaxe.return_value = [_entry("a")]
        git.get_blame.return_value = f"{_sha('a')[:7]} (Author 2024-01-01 1) line\n"
        problem = FakeProblem(
            extracted_files=["F.java"],
            extracted_keywords=["k"],
            extracted_symbols=["S"],
        )
        cfg = RetrievalConfig(max_candidates=10, fallback_recency_threshold=0)
        result = retrieve_candidates(problem, git, cfg)
        assert len(result.commits) == 1
        assert result.commits[0].rank == 1
        assert result.commits[0].retrieval_signal == "blame,file_log,keyword_grep,pickaxe"

    def test_e5_empty_strategy_does_not_block_others(self) -> None:
        git = _mock_git()
        git.search_commits_by_keyword.return_value = []
        git.search_commits_by_pickaxe.return_value = [_entry("p")]
        problem = FakeProblem(extracted_keywords=["k"], extracted_symbols=["Sym"])
        cfg = RetrievalConfig(
            strategies=["keyword_grep", "pickaxe"],
            fallback_recency_threshold=0,
            max_candidates=5,
        )
        result = retrieve_candidates(problem, git, cfg)
        assert result.commits[0].commit_id == _sha("p")

    def test_e7_max_candidates_zero(self) -> None:
        git = _mock_git()
        git.search_commits_by_keyword.return_value = [_entry("a")]
        problem = FakeProblem(extracted_keywords=["k"])
        cfg = RetrievalConfig(max_candidates=0, fallback_recency_threshold=0)
        result = retrieve_candidates(problem, git, cfg)
        assert result.commits == []
        assert result.retrieval_metadata["total_raw_candidates"] == 1

    def test_e8_temporal_bound(self) -> None:
        bounded = _mock_git(temporal_bound="abc123~1")
        bounded.search_commits_by_keyword.return_value = [_entry("a")]
        result = retrieve_candidates(
            FakeProblem(extracted_keywords=["k"]),
            bounded,
            RetrievalConfig(max_candidates=1, fallback_recency_threshold=0),
        )
        assert result.temporal_bound == "abc123~1"

        unbounded = _mock_git(temporal_bound=None)
        unbounded.search_commits_by_keyword.return_value = [_entry("a")]
        result2 = retrieve_candidates(
            FakeProblem(extracted_keywords=["k"]),
            unbounded,
            RetrievalConfig(max_candidates=1, fallback_recency_threshold=0),
        )
        assert result2.temporal_bound == ""


class TestDedupeHelpers:
    def test_dedupe_preserve_order(self) -> None:
        assert dedupe_preserve_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

    def test_parse_blame_skips_boundary(self) -> None:
        git = MagicMock()
        git.resolve_ref.side_effect = lambda ref: _sha("x")
        output = f"^{_sha('b')[:7]} (Author 2024-01-01 1) line\n"
        assert parse_blame_shas(output, git) == []
