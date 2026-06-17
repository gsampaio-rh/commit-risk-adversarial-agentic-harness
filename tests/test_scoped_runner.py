"""Tests for V4.1 scoped tools and ScopedInvestigator."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from commit_investigator.agent.tools import build_scoped_tools
from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.harness.scoped_prompts import parse_suspects, parse_tool_calls
from commit_investigator.harness.scoped_runner import (
    ScopedInvestigationResult,
    ScopedInvestigator,
    run_scoped_investigation,
)
from commit_investigator.harness.trace_writer import InvestigationTrace
from commit_investigator.harness.v4_runner import V4InvestigationResult
from commit_investigator.infra.llm import LLMResponse, MockLLMProvider
from commit_investigator.models.candidates import CandidateCommit, CandidateSet
from commit_investigator.retrieval import prepare_investigation
from commit_investigator.retrieval.pipeline import RetrievalResult


SHA_IN = "aaa111bbb222ccc333ddd444eee555fff666aaa1"
SHA_OUT = "zzz999yyy888xxx777www666vvv555uuu444ttt3"


def _make_candidate_set(*shas: str) -> CandidateSet:
    return CandidateSet(
        commits=[
            CandidateCommit(
                commit_id=sha,
                rank=i + 1,
                retrieval_signal="test",
                summary=f"Commit {sha[:8]}",
                files_changed=["src/main.java"],
                date="2024-01-01",
            )
            for i, sha in enumerate(shas)
        ],
        temporal_bound="abc123~1",
    )


def _mock_git():
    git = MagicMock()
    git.get_diff.return_value = "diff --git a/file.java\n+added line"
    git.get_commit_message.return_value = "Fix: updated logic"
    git.get_blame.return_value = "abc123 (Author 2024-01-01) line content"
    git.get_file_at_commit.return_value = "public class Main {}"
    return git


class TestBuildScopedTools:
    def test_only_examination_tools(self):
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_IN))
        names = registry.tool_names
        assert "get_commit_diff" in names
        assert "search_commits_by_file" not in names

    def test_rejects_out_of_set_sha(self):
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_IN))
        result = registry.execute("get_commit_diff", commit_id=SHA_OUT)
        assert "not in the CandidateSet" in result

    def test_accepts_short_sha_prefix(self):
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_IN))
        assert "Error" not in registry.execute("get_commit_diff", commit_id=SHA_IN[:12])

    def test_get_file_at_commit_rejects_out_of_set(self):
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_IN))
        result = registry.execute("get_file_at_commit", commit_id=SHA_OUT, path="file.java")
        assert "not in the CandidateSet" in result

    def test_get_commit_message_scoped(self):
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_IN))
        assert "Error" not in registry.execute("get_commit_message", commit_id=SHA_IN)
        assert "not in the CandidateSet" in registry.execute("get_commit_message", commit_id=SHA_OUT)

    def test_empty_candidate_set_rejects_all_shas(self):
        registry = build_scoped_tools(_mock_git(), CandidateSet())
        result = registry.execute("get_commit_diff", commit_id=SHA_IN)
        assert "not in the CandidateSet" in result

    def test_blame_does_not_validate_sha(self):
        registry = build_scoped_tools(_mock_git(), _make_candidate_set(SHA_IN))
        assert "Error" not in registry.execute("get_blame", path="src/main.java")


class _CountingLLM(MockLLMProvider):
    """Tracks how many times complete() is invoked."""

    def __init__(self, content: str = "Still thinking..."):
        self._content = content
        self.complete_count = 0

    def complete(self, messages, **kwargs) -> LLMResponse:
        self.complete_count += 1
        return LLMResponse(
            content=self._content, tokens_used=10, estimated_cost=0.0, model="mock",
        )


class _OosThenValidLLM(MockLLMProvider):
    """Turn 1: OOS SHA diff; turn 2: valid diff; turn 3: suspects."""

    def __init__(self, valid_sha: str, invalid_sha: str = SHA_OUT):
        self._valid_sha = valid_sha
        self._invalid_sha = invalid_sha
        self._call_count = 0

    def complete(self, messages, **kwargs) -> LLMResponse:
        self._call_count += 1
        if self._call_count == 1:
            content = (
                f'```tool\n{{"tool": "get_commit_diff", '
                f'"args": {{"commit_id": "{self._invalid_sha}"}}}}\n```'
            )
        elif self._call_count == 2:
            content = (
                f'```tool\n{{"tool": "get_commit_diff", '
                f'"args": {{"commit_id": "{self._valid_sha}"}}}}\n```'
            )
        else:
            content = (
                f'```suspects\n[{{"commit_id": "{self._valid_sha}", '
                f'"confidence": 0.9, "mechanism": "test", "evidence_quotes": ["line"]}}]\n```'
            )
        return LLMResponse(content=content, tokens_used=50, estimated_cost=0.001, model="mock")


class TestParsing:
    def test_parse_tool_calls(self):
        text = '```tool\n{"tool": "get_commit_diff", "args": {"commit_id": "abc"}}\n```'
        assert parse_tool_calls(text)[0]["tool"] == "get_commit_diff"

    def test_parse_suspects(self):
        text = '```suspects\n[{"commit_id": "abc", "confidence": 0.9}]\n```'
        assert parse_suspects(text)[0]["commit_id"] == "abc"


class _ToolThenSuspectsLLM(MockLLMProvider):
    def __init__(self, sha: str, *, tool: str = "get_commit_diff"):
        self._sha = sha
        self._tool = tool
        self._call_count = 0

    def complete(self, messages, **kwargs) -> LLMResponse:
        self._call_count += 1
        if self._call_count == 1:
            content = (
                f'```tool\n{{"tool": "{self._tool}", '
                f'"args": {{"commit_id": "{self._sha}"}}}}\n```'
            )
        else:
            content = (
                f'```suspects\n[{{"commit_id": "{self._sha}", '
                f'"confidence": 0.85, "mechanism": "test", "evidence_quotes": ["line"]}}]\n```'
            )
        return LLMResponse(content=content, tokens_used=100, estimated_cost=0.001, model="mock")


class _StaticLLM(MockLLMProvider):
    def __init__(self, content: str):
        self._content = content

    def complete(self, messages, **kwargs) -> LLMResponse:
        return LLMResponse(content=self._content, tokens_used=10, estimated_cost=0.0, model="mock")


class TestScopedInvestigator:
    def test_produces_suspects_after_diff(self):
        inv = ScopedInvestigator(
            llm=_ToolThenSuspectsLLM(SHA_IN),
            problem=ProblemStatement(title="Bug", description="desc", project="TEST"),
            candidate_set=_make_candidate_set(SHA_IN),
            git=_mock_git(),
        )
        result = inv.investigate()
        assert len(result.suspects) == 1
        assert result.tool_trace[0].tool == "get_commit_diff"

    def test_rejects_first_turn_suspects_without_diff(self):
        suspects_only = '```suspects\n[{"commit_id": "abc", "confidence": 0.9}]\n```'
        inv = ScopedInvestigator(
            llm=_StaticLLM(suspects_only),
            problem=ProblemStatement(title="Bug", description="desc", project="TEST"),
            candidate_set=_make_candidate_set(SHA_IN),
            git=_mock_git(),
            max_turns=2,
        )
        result = inv.investigate()
        assert result.suspects == []
        assert result.diff_examined is False

    def test_blame_alone_does_not_satisfy_diff_requirement(self):
        inv = ScopedInvestigator(
            llm=_ToolThenSuspectsLLM(SHA_IN, tool="get_blame"),
            problem=ProblemStatement(title="Bug", description="desc", project="TEST"),
            candidate_set=_make_candidate_set(SHA_IN),
            git=_mock_git(),
            max_turns=3,
        )
        result = inv.investigate()
        assert result.suspects == []

    def test_respects_max_tool_calls(self):
        many_calls = "\n".join(
            f'```tool\n{{"tool": "get_commit_diff", "args": {{"commit_id": "{SHA_IN}"}}}}\n```'
            for _ in range(20)
        )
        inv = ScopedInvestigator(
            llm=_StaticLLM(many_calls),
            problem=ProblemStatement(title="Bug", description="desc", project="TEST"),
            candidate_set=_make_candidate_set(SHA_IN),
            git=_mock_git(),
            max_tool_calls=15,
            max_turns=1,
        )
        result = inv.investigate()
        assert len(result.tool_trace) == 15

    def test_empty_candidate_set_returns_empty_suspects(self):
        inv = ScopedInvestigator(
            llm=MockLLMProvider(),
            problem=ProblemStatement(title="Bug", description="desc", project="TEST"),
            candidate_set=CandidateSet(),
            git=_mock_git(),
            max_turns=2,
        )
        result = inv.investigate()
        assert isinstance(result, ScopedInvestigationResult)
        assert result.suspects == []

    def test_nudges_when_no_tools_or_suspects(self):
        inv = ScopedInvestigator(
            llm=_StaticLLM("Still thinking..."),
            problem=ProblemStatement(title="Bug", description="desc", project="TEST"),
            candidate_set=_make_candidate_set(SHA_IN),
            git=_mock_git(),
            max_turns=2,
        )
        result = inv.investigate()
        assert result.suspects == []
        assert result.metadata["tool_calls"] == 0

    def test_respects_max_turns(self):
        llm = _CountingLLM()
        inv = ScopedInvestigator(
            llm=llm,
            problem=ProblemStatement(title="Bug", description="desc", project="TEST"),
            candidate_set=_make_candidate_set(SHA_IN),
            git=_mock_git(),
            max_turns=3,
        )
        result = inv.investigate()
        assert llm.complete_count == 3
        assert result.suspects == []

    def test_ec1_out_of_set_sha_continues_investigation(self):
        inv = ScopedInvestigator(
            llm=_OosThenValidLLM(SHA_IN),
            problem=ProblemStatement(title="Bug", description="desc", project="TEST"),
            candidate_set=_make_candidate_set(SHA_IN),
            git=_mock_git(),
        )
        result = inv.investigate()
        assert len(result.suspects) == 1
        assert result.suspects[0]["commit_id"] == SHA_IN
        assert len(result.tool_trace) == 2
        assert "not in the CandidateSet" in result.tool_trace[0].result_preview


class TestRunScopedInvestigation:
    def test_returns_v4_investigation_result(self, tmp_path: Path):
        candidate = CandidateCommit(
            commit_id=SHA_IN,
            rank=1,
            retrieval_signal="test",
            summary="s",
            files_changed=["f.java"],
            date="2024-01-01",
        )
        retrieval = RetrievalResult(
            problem_statement=ProblemStatement(title="T", description="D", project="TEST"),
            candidate_set=CandidateSet(commits=[candidate], temporal_bound="abc~1"),
            metadata={},
        )
        llm = _ToolThenSuspectsLLM(SHA_IN)

        with patch(
            "commit_investigator.harness.scoped_runner.prepare_investigation",
            return_value=retrieval,
        ), patch(
            "commit_investigator.harness.scoped_runner.GitContextProvider",
            return_value=_mock_git(),
        ), patch(
            "commit_investigator.harness.scoped_runner.TraceWriter.write",
            return_value=tmp_path / "trace.json",
        ) as write_trace:
            result = run_scoped_investigation(
                title="T",
                description="D",
                project="TEST",
                issue_key="TEST-1",
                repo_path=tmp_path,
                temporal_bound="abc123~1",
                ground_truth_sha=SHA_IN,
                llm=llm,
                traces_dir=tmp_path,
            )

        assert isinstance(result, V4InvestigationResult)
        assert len(result.suspects) == 1
        assert isinstance(result.trace, InvestigationTrace)
        assert result.trace.temporal_bound == "abc123~1"
        write_trace.assert_called_once()

    def test_temporal_bound_not_doubled(self, tmp_path: Path):
        captured: dict[str, str] = {}

        class _CapturingGit:
            def __init__(self, repo_path, temporal_bound):
                captured["bound"] = temporal_bound

        retrieval = RetrievalResult(
            problem_statement=ProblemStatement(title="T", description="D", project="TEST"),
            candidate_set=_make_candidate_set(SHA_IN),
            metadata={},
        )
        with patch(
            "commit_investigator.harness.scoped_runner.prepare_investigation",
            return_value=retrieval,
        ), patch(
            "commit_investigator.harness.scoped_runner.GitContextProvider",
            _CapturingGit,
        ), patch(
            "commit_investigator.harness.scoped_runner.TraceWriter.write",
            return_value=tmp_path / "trace.json",
        ):
            run_scoped_investigation(
                title="T",
                description="D",
                project="TEST",
                issue_key="TEST-1",
                repo_path=tmp_path,
                temporal_bound="abc123~1",
                ground_truth_sha=SHA_IN,
                llm=_ToolThenSuspectsLLM(SHA_IN),
            )

        assert captured["bound"] == "abc123~1"
        assert not captured["bound"].endswith("~1~1")


SCOPED_E2E_CASES = [
    ("CASSANDRA-7570", "CASSANDRA", "7fa93a2ca7febbff593aafef0265daa8799a9fb3~1", "data/repos/cassandra"),
    ("SPARK-2583", "SPARK", "17caae48b3608552dd6e3ae652043831f932ce95~1", "data/repos/spark"),
    ("SPARK-19033", "SPARK", "4a4c3dc9ca10e52f7981b225ec44e97247986905~1", "data/repos/spark"),
]


@pytest.mark.slow
@pytest.mark.parametrize("issue_key,project,temporal_bound,repo_rel", SCOPED_E2E_CASES)
def test_run_scoped_investigation_e2e_real_repo(
    issue_key: str, project: str, temporal_bound: str, repo_rel: str, tmp_path: Path,
) -> None:
    """AC5: full run_scoped_investigation on real repo yields parseable suspects."""
    repo = Path(repo_rel)
    if not (repo / ".git").exists():
        pytest.skip(f"{repo} not cloned")

    jira_path = Path("data/jira_cache") / f"{issue_key}.json"
    if not jira_path.exists():
        pytest.skip(f"no JIRA cache for {issue_key}")
    raw = json.loads(jira_path.read_text(encoding="utf-8"))
    title = raw["fields"]["summary"]
    description = raw["fields"].get("description") or ""

    retrieval = prepare_investigation(
        source=(title, description),
        repo_path=repo,
        temporal_bound=temporal_bound,
        project=project,
        issue_key=issue_key,
    )
    assert retrieval.candidate_set.commits, f"{issue_key}: empty CandidateSet"
    top_sha = retrieval.candidate_set.commits[0].commit_id

    result = run_scoped_investigation(
        title=title,
        description=description,
        project=project,
        issue_key=issue_key,
        repo_path=repo,
        temporal_bound=temporal_bound,
        ground_truth_sha=top_sha,
        llm=_ToolThenSuspectsLLM(top_sha),
        traces_dir=tmp_path / "traces",
    )
    assert isinstance(result, V4InvestigationResult)
    assert result.error is None
    assert len(result.suspects) >= 1
    assert result.suspects[0].get("commit_id")
    assert result.trace is not None
    assert result.trace.issue_key == issue_key
    assert result.trace.temporal_bound == temporal_bound
