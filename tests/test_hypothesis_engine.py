"""Unit tests for hypothesis_engine.py (iter-3e AC-1, AC-2, AC-3, AC-4, AC-5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from commit_investigator.context.context_builder import InvestigationContext  # noqa: E402
from commit_investigator.hypothesis.hypothesis_engine import (  # noqa: E402
    COVERAGE_SECTION_HEADER,
    HYPOTHESIS_SYSTEM_PROMPT,
    HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE,
    HYPOTHESIS_SYSTEM_PROMPT_H1H4T3,
    HypothesisResponse,
    HypothesisSpec,
    _format_author_stats,
    _format_file_histories,
    has_changed_line_citation,
    build_investigation_messages,
    extract_coverage_section,
    generate_contrastive_hypotheses,
    is_production_source_file,
    parse_hypothesis_response,
    _select_primary_sort_key,
    select_primary_by_evidence,
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
# iter-2d: COVERAGE REQUIREMENT (AC-1, EC-1, EC-2, EC-4)
# ---------------------------------------------------------------------------


class TestCoverageRequirement:
    """Anti-anchoring per-production-file coverage in HYPOTHESIS_SYSTEM_PROMPT."""

    def test_coverage_section_present(self):
        assert COVERAGE_SECTION_HEADER in HYPOTHESIS_SYSTEM_PROMPT

    def test_coverage_requires_hypothesis_or_skip_per_file(self):
        section = extract_coverage_section()
        assert "≥1 hypothesis" in section or ">=1 hypothesis" in section
        assert "SKIP:" in section
        assert "message-only" in section
        assert "*.java" in section

    def test_coverage_section_at_most_8_lines(self):
        section_lines = extract_coverage_section().splitlines()
        assert len(section_lines) <= 8, (
            f"COVERAGE section is {len(section_lines)} lines — EC-4 limit is 8"
        )

    def test_coverage_no_injected_file_names_or_risk_labels(self):
        """EC-1: no commit-specific paths, rubric tiers, or risk labels in system prompt."""
        forbidden = (
            "2213f71944ae",
            "409664582f53",
            "572f3cee35fe",
            "XmppGroupChatProducer",
            "DataFormatConfiguration",
            "HIGH:",
            "MEDIUM:",
            "LOW:",
            "CRITICAL:",
        )
        for token in forbidden:
            assert token not in HYPOTHESIS_SYSTEM_PROMPT, f"Forbidden token {token!r} in prompt"

    def test_coverage_uses_touched_files_from_context_not_prompt(self):
        """EC-1: file list comes from user context, not hardcoded in system prompt."""
        ctx = _make_context(
            touched_files=["components/camel-quartz/src/main/java/TriggerBuilder.java"],
        )
        msgs = build_investigation_messages(ctx)
        assert "TriggerBuilder.java" in msgs[1].content
        assert "TriggerBuilder.java" not in msgs[0].content

    def test_single_production_file_context_in_user_message(self):
        """EC-2: single-file commit — production path available in context for coverage."""
        prod_file = (
            "components/camel-quartz/src/main/java/org/apache/camel/component/quartz/"
            "QuartzTrigger.java"
        )
        ctx = _make_context(
            commit_id="409664582f53",
            touched_files=[prod_file],
            diff=f"--- a/{prod_file}\n+++ b/{prod_file}\n+ reschedule fix",
        )
        msgs = build_investigation_messages(ctx)
        assert prod_file in msgs[1].content
        assert "`file` matching" in msgs[0].content or "`file`" in msgs[0].content

    def test_is_production_source_file_filters_tests_and_docs(self):
        assert is_production_source_file("src/main/Foo.java")
        assert not is_production_source_file("src/test/FooTest.java")
        assert not is_production_source_file("README.md")
        assert not is_production_source_file("pom.xml")


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
        from commit_investigator.context.smart_diff import AssembledDiff

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


# ---------------------------------------------------------------------------
# HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE — prompt structure
# ---------------------------------------------------------------------------


_CONTRASTIVE_CATEGORIES = (
    "null-reference",
    "lifecycle-ordering",
    "concurrency",
    "api-contract",
    "input-validation",
    "resource-leak",
    "error-handling",
    "logic-error",
)


class TestContrastivePrompt:
    def test_prompt_distinct_from_baseline_and_h1h4t3(self):
        assert HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE != HYPOTHESIS_SYSTEM_PROMPT
        assert HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE != HYPOTHESIS_SYSTEM_PROMPT_H1H4T3

    def test_prompt_contains_diversity_requirement(self):
        assert "CONTRASTIVE" in HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE

    def test_prompt_lists_all_eight_causal_categories(self):
        for category in _CONTRASTIVE_CATEGORIES:
            assert category in HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE

    def test_prompt_requires_category_label_in_brackets(self):
        assert "category label in brackets" in HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE

    def test_prompt_enforces_distinct_categories_in_first_3(self):
        assert "No two of the first 3" in HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE

    def test_prompt_includes_changed_line_evidence_requirement(self):
        assert "CHANGED-LINE EVIDENCE" in HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE

    def test_prompt_includes_coverage_section(self):
        assert COVERAGE_SECTION_HEADER in HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE

    def test_prompt_includes_output_format(self):
        assert "summary" in HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE
        assert "hypotheses" in HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE
        assert "mechanism" in HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE

    def test_prompt_forbids_rubric_output(self):
        assert "Do NOT include risk_level" in HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE

    def test_prompt_anchors_h1_highest_confidence(self):
        """AC-7: position-0 anchor co-occurs with highest-confidence language."""
        prompt = HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE.lower()
        assert "highest-confidence" in prompt or "highest confidence" in prompt
        assert "position 0" in prompt or "first hypothesis" in prompt
        assert "h2" in prompt or "second" in prompt or "beyond" in prompt


# ---------------------------------------------------------------------------
# select_primary_by_evidence — unit tests
# ---------------------------------------------------------------------------


def _make_hyp(
    mechanism: str = "m",
    evidence_quote: str = "",
    file: str = "f.py",
) -> HypothesisSpec:
    return HypothesisSpec(mechanism=mechanism, evidence_quote=evidence_quote, file=file)


class TestSelectPrimaryByEvidence:
    def test_single_hypothesis_unchanged(self):
        hyp = _make_hyp(evidence_quote="+ foo = bar")
        result = select_primary_by_evidence([hyp])
        assert result == [hyp]

    def test_empty_list_returns_empty(self):
        assert select_primary_by_evidence([]) == []

    def test_grounded_promoted_over_ungrounded(self):
        ungrounded = _make_hyp("m1", evidence_quote="context line only")
        grounded = _make_hyp("m2", evidence_quote="+ new_field = get_value()")
        result = select_primary_by_evidence([ungrounded, grounded])
        assert result[0].mechanism == "m2"
        assert result[1].mechanism == "m1"

    def test_already_best_first_order_preserved(self):
        h1 = _make_hyp("m1", evidence_quote="+ add line")
        h2 = _make_hyp("m2", evidence_quote="- remove line")
        h3 = _make_hyp("m3", evidence_quote="")
        result = select_primary_by_evidence([h1, h2, h3])
        assert result[0].mechanism == "m1"
        assert result[1].mechanism == "m2"
        assert result[2].mechanism == "m3"

    def test_sort_key_all_ungrounded_is_two_tuple(self):
        """EC-1: no grounded candidates → (citation_bit, original_index) only."""
        hyp = _make_hyp("m1", evidence_quote="context only")
        assert _select_primary_sort_key((3, hyp), any_grounded=False) == (1, 3)

    def test_sort_key_quote_len_after_index(self):
        """AC-2/EC-3: index precedes quote_len; longer quote breaks index ties only."""
        short = _make_hyp("s", evidence_quote="+ ab", file="src/Foo.java")
        long = _make_hyp("l", evidence_quote="+ longer", file="src/Foo.java")
        assert _select_primary_sort_key((0, short), any_grounded=True) == (0, -1, 0, -4)
        assert _select_primary_sort_key((0, long), any_grounded=True) == (0, -1, 0, -8)

    def test_all_ungrounded_preserves_original_order(self):
        h1 = _make_hyp("m1", evidence_quote="context only")
        h2 = _make_hyp("m2", evidence_quote="")
        h3 = _make_hyp("m3", evidence_quote="unchanged line")
        result = select_primary_by_evidence([h1, h2, h3])
        assert [r.mechanism for r in result] == ["m1", "m2", "m3"]

    def test_grounded_at_index_three_promoted_to_first(self):
        h1 = _make_hyp("m1", evidence_quote="")
        h2 = _make_hyp("m2", evidence_quote="")
        h3 = _make_hyp("m3", evidence_quote="")
        h4 = _make_hyp("m4", evidence_quote="+ grounded at index three")
        result = select_primary_by_evidence([h1, h2, h3, h4])
        assert result[0].mechanism == "m4"

    def test_eba3689_position5_grounded_promoted(self):
        hyps = [
            _make_hyp("m0", evidence_quote=""),
            _make_hyp("m1", evidence_quote="context only"),
            _make_hyp("m2", evidence_quote=""),
            _make_hyp("m3", evidence_quote="unchanged line"),
            _make_hyp("m4", evidence_quote="+ correct mechanism at position five"),
        ]
        result = select_primary_by_evidence(hyps)
        assert result[0] is hyps[4]
        assert result[0].mechanism == "m4"

    def test_third_grounded_candidate_promoted_to_first(self):
        h1 = _make_hyp("m1", evidence_quote="context")
        h2 = _make_hyp("m2", evidence_quote="")
        h3 = _make_hyp("m3", evidence_quote="- removed guard")
        result = select_primary_by_evidence([h1, h2, h3])
        assert result[0].mechanism == "m3"

    def test_returns_new_list_not_mutated(self):
        original = [_make_hyp("m1"), _make_hyp("m2", evidence_quote="+ x")]
        result = select_primary_by_evidence(original)
        assert result is not original

    def test_all_ungrounded_h1_anchor(self):
        """Position-0 preserved when all candidates lack changed-line citations."""
        h1 = _make_hyp("anchor_mechanism", evidence_quote="context only")
        h2 = _make_hyp("alt_m2", evidence_quote="")
        h3 = _make_hyp("alt_m3", evidence_quote="unchanged line")
        h4 = _make_hyp("alt_m4", evidence_quote="more context")
        result = select_primary_by_evidence([h1, h2, h3, h4])
        assert result[0].mechanism == "anchor_mechanism"
        assert [r.mechanism for r in result] == [
            "anchor_mechanism",
            "alt_m2",
            "alt_m3",
            "alt_m4",
        ]

    def test_composite_grounded_prefers_production_file(self):
        """Production-file citation beats test-file citation at equal quote length."""
        equal_quote = "+ x = getValue()"
        test_file = _make_hyp(
            "test_path",
            evidence_quote=equal_quote,
            file="src/FooTest.java",
        )
        production_file = _make_hyp(
            "prod_path",
            evidence_quote=equal_quote,
            file="src/FooService.java",
        )
        result = select_primary_by_evidence([test_file, production_file])
        assert result[0].mechanism == "prod_path"
        assert result[1].mechanism == "test_path"


# ---------------------------------------------------------------------------
# generate_contrastive_hypotheses — delegates to complete_with_parse_retry
# ---------------------------------------------------------------------------


class TestGenerateContrastiveHypotheses:
    @staticmethod
    def _run_with_counting_llm(payload: str) -> tuple[HypothesisResponse, int]:
        from commit_investigator.infra.llm import LLMMessage, LLMResponse, LLMProvider

        class _SingleCallLLM(LLMProvider):
            def __init__(self) -> None:
                self.calls = 0

            @property
            def model_name(self) -> str:
                return "single-call-test"

            def complete(self, messages, tools=None, temperature=0.0, max_tokens=4096):
                self.calls += 1
                return LLMResponse(
                    content=payload,
                    tool_calls=[],
                    tokens_used=10,
                    estimated_cost=0.0,
                    model=self.model_name,
                    finish_reason="stop",
                )

        llm = _SingleCallLLM()
        msgs = [LLMMessage(role="user", content="test")]

        def parse_fn(r):
            return parse_hypothesis_response(r.content or "")

        result, _ = generate_contrastive_hypotheses(
            llm, msgs, None, parse_fn, lambda r: None, ValueError,
        )
        return result, llm.calls

    def test_returns_hypothesis_response_from_mock_llm(self):
        payload = json.dumps({
            "summary": "test summary",
            "hypotheses": [
                {
                    "mechanism": "[null-reference] Observable: NPE. Root change: - null check removed. Mechanism: chain",
                    "evidence_quote": "- if (x != null)",
                    "file": "Foo.java",
                    "lines": [10, 12],
                    "suggested_action": "Add null guard back",
                }
            ],
        })
        result, calls = self._run_with_counting_llm(payload)
        assert isinstance(result, HypothesisResponse)
        assert result.summary == "test summary"
        assert len(result.hypotheses) == 1
        assert "null-reference" in result.hypotheses[0].mechanism
        assert calls == 1

    def test_single_llm_call_on_parse_success(self):
        payload = json.dumps({
            "summary": "ok",
            "hypotheses": [{"mechanism": "m", "evidence_quote": "+ line", "file": "a.py", "lines": []}],
        })
        _, calls = self._run_with_counting_llm(payload)
        assert calls == 1

    def test_ungrounded_hypotheses_still_single_call(self):
        payload = json.dumps({
            "summary": "all ungrounded",
            "hypotheses": [
                {"mechanism": "m1", "evidence_quote": "context only", "file": "a.py", "lines": []},
                {"mechanism": "m2", "evidence_quote": "", "file": "b.py", "lines": []},
            ],
        })
        result, calls = self._run_with_counting_llm(payload)
        assert calls == 1
        assert len(result.hypotheses) == 2
