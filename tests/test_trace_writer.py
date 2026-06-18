"""Tests for trace writer."""

import json
import tempfile
from pathlib import Path

from commit_investigator.investigation.trace_writer import (
    EvidenceRecord,
    InvestigationTrace,
    OutcomeRecord,
    TraceWriter,
    TurnRecord,
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
