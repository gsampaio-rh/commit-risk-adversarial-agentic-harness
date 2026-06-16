"""Tests for V4 trace writer and V4 investigation runner."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from commit_investigator.harness.llm_protocol import LLMResponse
from commit_investigator.harness.trace_writer import (
    EvidenceRecord,
    InvestigationTrace,
    OutcomeRecord,
    TraceWriter,
    TurnRecord,
)
from commit_investigator.harness.v4_runner import (
    V4InvestigationResult,
    _extract_top_confidence,
    run_v4_investigation,
)
from commit_investigator.models.investigation import (
    ExaminationStep,
    Hypothesis,
    InvestigationBrief,
)


class TestInvestigationTrace:
    def test_to_dict_has_all_fields(self) -> None:
        trace = InvestigationTrace(
            issue_key="SPARK-123",
            temporal_bound="abc~1",
            candidate_set_size=50,
            retrieval_recall_100=True,
        )
        d = trace.to_dict()
        assert d["issue_key"] == "SPARK-123"
        assert d["candidate_set_size"] == 50
        assert d["retrieval_recall_100"] is True
        assert "trace_id" in d
        assert "run_id" in d
        assert "outcome" in d
        assert "examination_turns" in d

    def test_outcome_record(self) -> None:
        outcome = OutcomeRecord(
            suspect_count=3,
            top_confidence=0.85,
            degraded=False,
            hit_at_5=True,
            mrr=1.0,
        )
        d = outcome.to_dict()
        assert d["suspect_count"] == 3
        assert d["top_confidence"] == 0.85
        assert d["hit_at_5"] is True

    def test_evidence_record(self) -> None:
        ev = EvidenceRecord(
            commit_id="abc123",
            quote="removed null check",
            grounded=True,
            hypothesis_id="h1",
            turn=2,
        )
        d = ev.to_dict()
        assert d["commit_id"] == "abc123"
        assert d["grounded"] is True

    def test_turn_record(self) -> None:
        turn = TurnRecord(
            turn=1,
            tool_calls=[{"tool": "get_diff", "args": {}, "summary": "checked diff"}],
            hypothesis_updates=["h1"],
            completion_check={"evidence_met": True},
        )
        d = turn.to_dict()
        assert d["turn"] == 1
        assert len(d["tool_calls"]) == 1


class TestTraceWriter:
    def test_write_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = TraceWriter(traces_dir=tmpdir)
            trace = InvestigationTrace(
                issue_key="TEST-1",
                candidate_set_size=10,
            )
            path = writer.write(trace)

            assert path.exists()
            assert path.parent.name == "TEST-1"
            data = json.loads(path.read_text())
            assert data["issue_key"] == "TEST-1"
            assert data["candidate_set_size"] == 10

    def test_write_creates_nested_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = TraceWriter(traces_dir=Path(tmpdir) / "deep" / "nested")
            trace = InvestigationTrace(issue_key="DEEP-1")
            path = writer.write(trace)
            assert path.exists()


class TestExtractTopConfidence:
    def test_empty_suspects(self) -> None:
        assert _extract_top_confidence([]) == 0.0

    def test_extracts_max(self) -> None:
        suspects = [
            {"confidence": 0.3},
            {"confidence": 0.9},
            {"confidence": 0.5},
        ]
        assert _extract_top_confidence(suspects) == 0.9

    def test_missing_confidence_key(self) -> None:
        suspects = [{"commit_id": "abc"}]
        assert _extract_top_confidence(suspects) == 0.0


class TestV4Runner:
    @patch("commit_investigator.harness.v4_runner.prepare_investigation")
    def test_end_to_end_mock(self, mock_prepare) -> None:
        """V4 runner wires input pipeline → harness → trace."""
        from commit_investigator.extraction.problem_extractor import ProblemStatement
        from commit_investigator.models.candidates import CandidateCommit, CandidateSet
        from commit_investigator.retrieval.pipeline import RetrievalResult

        problem = ProblemStatement(
            title="Bug title",
            description="Bug desc",
            project="TEST",
            extracted_files=["Foo.java"],
            extracted_symbols=["FooBar"],
            extracted_keywords=["bug"],
        )
        candidate_set = CandidateSet(
            commits=[
                CandidateCommit(
                    commit_id="a" * 40,
                    rank=1,
                    retrieval_signal="file_log",
                    summary="fix foo",
                    files_changed=["Foo.java"],
                )
            ],
            retrieval_metadata={"strategies_used": ["file_log"]},
            temporal_bound="abc~1",
        )
        mock_prepare.return_value = RetrievalResult(
            problem_statement=problem,
            candidate_set=candidate_set,
            metadata={"retry_triggered": False},
        )

        brief_json = json.dumps(InvestigationBrief(
            hypotheses=[
                Hypothesis(id="h1", statement="Bug because null check removed"),
                Hypothesis(id="h2", statement="Bug caused by refactoring"),
            ],
            examination_plan=[ExaminationStep(look_for="null check")],
            strategy="Examine top candidates for changes",
            max_effort=18,
        ).to_dict())

        class FakeLLM:
            def __init__(self):
                self._calls = 0

            def generate(self, prompt, **kwargs):
                self._calls += 1
                if self._calls == 1:
                    return LLMResponse(content=brief_json, tokens_used=50)
                if self._calls <= 4:
                    return LLMResponse(content="Evidence: found change", tokens_used=30)
                return LLMResponse(
                    content=json.dumps({"suspects": [{"commit_id": "a" * 40, "confidence": 0.8}]}),
                    tokens_used=40,
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_v4_investigation(
                title="Bug title",
                description="Bug desc",
                project="TEST",
                issue_key="TEST-1",
                repo_path="/fake/repo",
                temporal_bound="abc~1",
                ground_truth_sha="a" * 40,
                llm=FakeLLM(),
                traces_dir=tmpdir,
            )

            assert result.issue_key == "TEST-1"
            assert result.retrieval_recall is True
            assert result.trace is not None
            assert result.trace.candidate_set_size == 1
            assert result.error is None

            trace_dir = Path(tmpdir) / "TEST-1"
            assert trace_dir.exists()
            trace_files = list(trace_dir.glob("*.json"))
            assert len(trace_files) == 1

    @patch("commit_investigator.harness.v4_runner.prepare_investigation")
    def test_input_pipeline_failure(self, mock_prepare) -> None:
        mock_prepare.side_effect = RuntimeError("repo exploded")

        result = run_v4_investigation(
            title="t",
            description="d",
            project="P",
            issue_key="P-1",
            repo_path="/fake",
            temporal_bound="x~1",
            ground_truth_sha="a" * 40,
            llm=MagicMock(),
        )

        assert result.error is not None
        assert "Input pipeline failed" in result.error
