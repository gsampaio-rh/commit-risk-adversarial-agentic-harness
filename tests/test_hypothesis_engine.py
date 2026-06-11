"""Unit tests for hypothesis_engine.py (iter-3e AC-1, AC-2, AC-3, AC-4, AC-5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from commit_investigator.context_builder import InvestigationContext  # noqa: E402
from commit_investigator.hypothesis_engine import (  # noqa: E402
    HYPOTHESIS_SYSTEM_PROMPT,
    HypothesisResponse,
    HypothesisSpec,
    _format_author_stats,
    _format_file_histories,
    build_investigation_messages,
    parse_hypothesis_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(**kwargs) -> InvestigationContext:
    defaults = {
        "commit_id": "abc123",
        "project": "test-project",
        "diff": "- old\n+ new",
        "message": "Fix bug",
        "touched_files": ["foo.py"],
        "missing_reasons": set(),
        "file_histories": {},
        "author_stats": None,
        "csv_features": {},
        "router_probability": None,
        "truncation_metadata": None,
    }
    defaults.update(kwargs)
    return InvestigationContext(**defaults)


# ---------------------------------------------------------------------------
# AC-1: HYPOTHESIS_SYSTEM_PROMPT ≤60 lines
# ---------------------------------------------------------------------------


class TestPromptConstraints:
    def test_prompt_length_at_most_60_lines(self):
        lines = HYPOTHESIS_SYSTEM_PROMPT.splitlines()
        assert len(lines) <= 60, f"Prompt is {len(lines)} lines — exceeds AC-1 limit of 60"

    def test_prompt_includes_json_schema_keys(self):
        assert "summary" in HYPOTHESIS_SYSTEM_PROMPT
        assert "hypotheses" in HYPOTHESIS_SYSTEM_PROMPT
        assert "mechanism" in HYPOTHESIS_SYSTEM_PROMPT
        assert "evidence_quote" in HYPOTHESIS_SYSTEM_PROMPT
        assert "suggested_action" in HYPOTHESIS_SYSTEM_PROMPT

    # AC-2: no rubric tier labels
    def test_prompt_has_no_rubric_tiers(self):
        for tier in ("HIGH:", "MEDIUM:", "LOW:", "CRITICAL:"):
            assert tier not in HYPOTHESIS_SYSTEM_PROMPT, f"Rubric tier {tier!r} found in prompt"

    # AC-3: no clean-commit discrimination
    def test_prompt_has_no_clean_commit_discrimination(self):
        discriminators = ("CLEAN-COMMIT", "clean commit", "CLEAN_COMMIT")
        for d in discriminators:
            assert d not in HYPOTHESIS_SYSTEM_PROMPT, f"Discriminator {d!r} found in prompt"

    def test_prompt_explicitly_forbids_risk_level_output(self):
        # Prompt must tell the LLM NOT to produce risk_level, confidence, follow_up_needed
        assert "Do NOT include" in HYPOTHESIS_SYSTEM_PROMPT
        assert "risk_level" in HYPOTHESIS_SYSTEM_PROMPT  # appears in the exclusion instruction
        assert "confidence" in HYPOTHESIS_SYSTEM_PROMPT  # appears in the exclusion instruction
        assert "follow_up_needed" in HYPOTHESIS_SYSTEM_PROMPT  # appears in the exclusion instruction


# ---------------------------------------------------------------------------
# HypothesisSpec / HypothesisResponse models
# ---------------------------------------------------------------------------


class TestHypothesisModels:
    def test_hypothesis_spec_defaults(self):
        spec = HypothesisSpec(mechanism="If X then Y at file.py:10")
        assert spec.evidence_quote == ""
        assert spec.file == ""
        assert spec.lines == []
        assert spec.suggested_action == ""

    def test_hypothesis_response_minimal(self):
        resp = HypothesisResponse(summary="Minor change", hypotheses=[])
        assert resp.summary == "Minor change"
        assert resp.hypotheses == []

    def test_hypothesis_response_full(self):
        spec = HypothesisSpec(
            mechanism="If null check removed then NPE at foo.py:10",
            evidence_quote="-  if (x != null) {",
            file="foo.py",
            lines=[10, 15],
        )
        resp = HypothesisResponse(summary="Removed null guard", hypotheses=[spec])
        assert len(resp.hypotheses) == 1
        assert resp.hypotheses[0].file == "foo.py"


# ---------------------------------------------------------------------------
# parse_hypothesis_response
# ---------------------------------------------------------------------------


class TestParseHypothesisResponse:
    def _valid_json(self, n_hypotheses: int = 1) -> str:
        hypotheses = [
            {
                "mechanism": f"If condition-{i} then failure-{i} at file.py:{i}",
                "evidence_quote": f"- line {i}",
                "file": "file.py",
                "lines": [i, i + 5],
            }
            for i in range(n_hypotheses)
        ]
        return json.dumps({"summary": "Test commit", "hypotheses": hypotheses})

    def test_valid_json_parses(self):
        result = parse_hypothesis_response(self._valid_json(2))
        assert len(result.hypotheses) == 2
        assert result.summary == "Test commit"

    def test_markdown_wrapper_stripped(self):
        wrapped = f"```json\n{self._valid_json()}\n```"
        result = parse_hypothesis_response(wrapped)
        assert result.summary == "Test commit"

    def test_empty_hypotheses_allowed(self):
        raw = json.dumps({"summary": "Trivial whitespace", "hypotheses": []})
        result = parse_hypothesis_response(raw)
        assert result.hypotheses == []

    def test_invalid_json_raises(self):
        with pytest.raises((ValueError, Exception)):
            parse_hypothesis_response("not json at all")

    def test_missing_summary_raises(self):
        raw = json.dumps({"hypotheses": []})
        with pytest.raises(Exception):
            parse_hypothesis_response(raw)


# ---------------------------------------------------------------------------
# build_investigation_messages
# ---------------------------------------------------------------------------


class TestBuildInvestigationMessages:
    def test_returns_two_messages(self):
        ctx = _make_context()
        msgs = build_investigation_messages(ctx)
        assert len(msgs) == 2
        assert msgs[0].role == "system"
        assert msgs[1].role == "user"

    def test_system_prompt_default(self):
        ctx = _make_context()
        msgs = build_investigation_messages(ctx)
        assert msgs[0].content == HYPOTHESIS_SYSTEM_PROMPT

    def test_custom_system_prompt(self):
        ctx = _make_context()
        msgs = build_investigation_messages(ctx, system_prompt="Custom prompt")
        assert msgs[0].content == "Custom prompt"

    def test_user_message_contains_commit_id(self):
        ctx = _make_context(commit_id="deadbeef99")
        msgs = build_investigation_messages(ctx)
        assert "deadbeef99" in msgs[1].content

    def test_user_message_contains_diff(self):
        ctx = _make_context(diff="+ added line\n- removed line")
        msgs = build_investigation_messages(ctx)
        assert "+ added line" in msgs[1].content

    def test_router_probability_injected(self):
        ctx = _make_context(router_probability=0.847)
        msgs = build_investigation_messages(ctx)
        assert "0.847" in msgs[1].content

    def test_no_diff_no_diff_section(self):
        ctx = _make_context(diff="")
        msgs = build_investigation_messages(ctx)
        assert "## Diff" not in msgs[1].content

    def test_touched_files_listed(self):
        ctx = _make_context(touched_files=["alpha.py", "beta.py"])
        msgs = build_investigation_messages(ctx)
        assert "alpha.py" in msgs[1].content
        assert "beta.py" in msgs[1].content

    def test_missing_context_appended_when_no_history(self):
        ctx = _make_context(file_histories={})
        msgs = build_investigation_messages(ctx)
        assert "Missing Context" in msgs[1].content or "File history unavailable" in msgs[1].content

    def test_truncation_metadata_note_added(self):
        from commit_investigator.smart_diff import AssembledDiff

        tm = AssembledDiff(
            text="+ new line",
            included_files=["a.py"],
            truncated_files=["big.py"],
            total_chars=100,
        )
        ctx = _make_context(diff="+ new line", truncation_metadata=tm)
        msgs = build_investigation_messages(ctx)
        assert "big.py" in msgs[1].content
        assert "smart-truncated" in msgs[1].content


# ---------------------------------------------------------------------------
# _format_file_histories
# ---------------------------------------------------------------------------


class TestFormatFileHistories:
    def _make_entry(self, commit_id: str = "abcdef01"):
        import types

        return types.SimpleNamespace(
            commit_id=commit_id,
            date="2024-01-15T10:00:00",
            author="Dev",
            message="Fix something",
        )

    def test_empty_dict_returns_empty_list(self):
        ctx = _make_context(file_histories={})
        assert _format_file_histories(ctx) == []

    def test_file_with_no_entries_skipped(self):
        ctx = _make_context(file_histories={"empty.py": []})
        assert _format_file_histories(ctx) == []

    def test_entries_capped_at_5(self):
        entries = [self._make_entry(f"{i:08x}") for i in range(10)]
        ctx = _make_context(file_histories={"foo.py": entries})
        lines = _format_file_histories(ctx)
        # header + max 5 entries
        entry_lines = [l for l in lines if l.startswith("  -")]
        assert len(entry_lines) == 5

    def test_files_capped_at_5(self):
        histories = {f"file{i}.py": [self._make_entry()] for i in range(8)}
        ctx = _make_context(file_histories=histories)
        lines = _format_file_histories(ctx)
        file_headers = [l for l in lines if l.startswith("###")]
        assert len(file_headers) == 5


# ---------------------------------------------------------------------------
# _format_author_stats
# ---------------------------------------------------------------------------


class TestFormatAuthorStats:
    def _make_stats(self, buggy_rate: float = 0.0667):
        import types

        return types.SimpleNamespace(
            author="jane",
            total_commits=30,
            buggy_rate=buggy_rate,
            avg_files_changed=2.5,
        )

    def test_none_returns_empty_string(self):
        ctx = _make_context(author_stats=None)
        assert _format_author_stats(ctx) == ""

    def test_author_present(self):
        ctx = _make_context(author_stats=self._make_stats())
        result = _format_author_stats(ctx)
        assert "jane" in result

    def test_buggy_rate_percent_format(self):
        ctx = _make_context(author_stats=self._make_stats(buggy_rate=0.0667))
        result = _format_author_stats(ctx)
        assert "6.67%" in result
