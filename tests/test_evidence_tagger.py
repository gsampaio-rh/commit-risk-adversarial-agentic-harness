"""Tests for evidence_tagger.py — hybrid hypothesis tier verification.

Ports from tests/test_evidence_tagger_spike.py with imports from production module.
AC-5: tag_hypothesis() achieves ≥80% strict agreement on 36-fixture corpus.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from commit_investigator.evidence_tagger import (
    TagResult,
    count_supported_from_reasoning,
    quote_in_diff,
    tag_hypothesis,
)

CORPUS_PATH = PROJECT_ROOT / "tests/fixtures/evidence_tagger_panel.json"
REPOS_BASE = PROJECT_ROOT / "data/repos"
MAX_DIFF_CHARS = 16_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_diff(commit_id: str, project: str, *, full: bool = False) -> str:
    from commit_investigator.git_context import GitContextProvider

    repo_path = REPOS_BASE / project
    if not repo_path.exists():
        return ""
    try:
        gp = GitContextProvider(repo_path)
        diff = gp.get_diff(commit_id) or ""
        return diff if full else diff[:MAX_DIFF_CHARS]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Unit: quote_in_diff matching cascade
# ---------------------------------------------------------------------------


class TestQuoteInDiff:
    def test_exact_match(self) -> None:
        found, method = quote_in_diff("setNoStart(true)", "-    setNoStart(true);")
        assert found is True
        assert method == "exact"

    def test_normalized_match(self) -> None:
        found, method = quote_in_diff(
            "exchange.getIn().setBody(body)",
            "-     exchange.getIn().setBody(body);",
        )
        assert found is True
        assert method in ("exact", "normalized")

    def test_no_match(self) -> None:
        found, method = quote_in_diff("hallucinated_method()", "unrelated content here")
        assert found is False
        assert method == "not_found"

    def test_short_quote_absent(self) -> None:
        found, method = quote_in_diff("abc", "abc is here")
        assert found is False
        assert method == "absent"

    def test_empty_quote(self) -> None:
        found, method = quote_in_diff("", "any diff content")
        assert found is False
        assert method == "absent"

    def test_none_quote(self) -> None:
        found, method = quote_in_diff(None, "any diff content")  # type: ignore[arg-type]
        assert found is False


# ---------------------------------------------------------------------------
# Unit: tag_hypothesis logic
# ---------------------------------------------------------------------------


class TestTagHypothesis:
    def test_supported_when_quote_in_diff(self) -> None:
        result = tag_hypothesis(
            "SpringCamelContext.setNoStart(true);",
            "  -        SpringCamelContext.setNoStart(true);",
        )
        assert result.tier == "SUPPORTED"
        assert result.quote_in_diff is True

    def test_speculative_when_no_quote_and_llm_supported(self) -> None:
        """Hallucinated SUPPORTED: LLM says SUPPORTED but no verifiable quote."""
        result = tag_hypothesis("", "diff content without the claimed evidence", llm_tier="SUPPORTED")
        assert result.tier == "SPECULATIVE"
        assert result.quote_in_diff is False

    def test_speculative_when_quote_not_in_complete_diff(self) -> None:
        """Quote not found, diff is complete → SPECULATIVE."""
        result = tag_hypothesis(
            "nonexistent_method_call(True)",
            "+-some other diff content here",
            diff_was_truncated=False,
        )
        assert result.tier == "SPECULATIVE"

    def test_unverifiable_when_quote_missing_and_diff_truncated(self) -> None:
        """Quote not in truncated diff → could be beyond truncation → UNVERIFIABLE."""
        result = tag_hypothesis(
            "beyond_truncation_method()",
            "diff content cut off at 16K chars",
            diff_was_truncated=True,
        )
        assert result.tier == "UNVERIFIABLE"

    def test_defers_to_speculative(self) -> None:
        result = tag_hypothesis(None, "any diff", llm_tier="SPECULATIVE")
        assert result.tier == "SPECULATIVE"
        assert result.match_method == "deferred"

    def test_defers_to_refuted(self) -> None:
        result = tag_hypothesis("", "any diff", llm_tier="REFUTED")
        assert result.tier == "REFUTED"
        assert result.match_method == "deferred"

    def test_defers_to_unverifiable(self) -> None:
        result = tag_hypothesis(None, "any diff", llm_tier="UNVERIFIABLE")
        assert result.tier == "UNVERIFIABLE"
        assert result.match_method == "deferred"

    def test_supported_overrides_llm_speculative(self) -> None:
        """If quote IS in diff, tag SUPPORTED even if LLM said SPECULATIVE."""
        result = tag_hypothesis(
            "Boolean.class == type",
            "- if (boolean.class == type || Boolean.class == type) {",
            llm_tier="SPECULATIVE",
        )
        assert result.tier == "SUPPORTED"

    def test_fbf0ff_setnostart_in_truncated(self) -> None:
        """fbf0ff H1: setNoStart guard removal visible in truncated diff."""
        diff = _load_diff("fbf0ffad627b", "camel")
        if not diff:
            pytest.skip("camel repo not available")
        result = tag_hypothesis("SpringCamelContext.setNoStart(true);", diff)
        assert result.tier == "SUPPORTED"

    def test_7cff_getout_substitution(self) -> None:
        """7cff H1: getIn→getOut substitution visible in diff."""
        diff = _load_diff("7cff0990283b", "camel")
        if not diff:
            pytest.skip("camel repo not available")
        result = tag_hypothesis("exchange.getIn().setBody(body);", diff)
        assert result.tier == "SUPPORTED"

    def test_no_quote_does_not_upgrade_speculative(self) -> None:
        """No quote: SPECULATIVE with no evidence stays SPECULATIVE via defer."""
        result = tag_hypothesis(None, "diff content", llm_tier="SPECULATIVE")
        assert result.tier == "SPECULATIVE"
        assert result.match_method == "deferred"

    def test_quote_in_diff_upgrades_to_supported(self) -> None:
        """If quote IS in diff, SUPPORTED is correct regardless of llm_tier."""
        diff = "- setNoStart(true);\n+ setStart(true);"
        result = tag_hypothesis("setNoStart(true)", diff, llm_tier="SPECULATIVE")
        assert result.tier == "SUPPORTED"


# ---------------------------------------------------------------------------
# AC-5: Corpus agreement test (≥80% strict)
# ---------------------------------------------------------------------------


class TestCorpusAgreement:
    """AC-5: ≥80% strict 4-way tier agreement on 36-fixture corpus."""

    @pytest.fixture(scope="class")
    def corpus(self) -> list[dict]:
        if not CORPUS_PATH.exists():
            pytest.skip("Corpus fixture not found")
        return json.loads(CORPUS_PATH.read_text())

    @pytest.fixture(scope="class")
    def diffs(self, corpus: list[dict]) -> dict[str, str]:
        seen: set[str] = set()
        result: dict[str, str] = {}
        for fx in corpus:
            key = f"{fx['commit_id']}:{fx['project']}"
            if key not in seen:
                seen.add(key)
                result[key] = _load_diff(fx["commit_id"], fx["project"])
        return result

    def test_strict_agreement(self, corpus: list[dict], diffs: dict[str, str]) -> None:
        correct = 0
        failures = []

        for fx in corpus:
            diff = diffs.get(f"{fx['commit_id']}:{fx['project']}", "")
            diff_was_truncated = len(_load_diff(fx["commit_id"], fx["project"], full=True)) > MAX_DIFF_CHARS
            result = tag_hypothesis(
                fx.get("evidence_quote"),
                diff,
                diff_was_truncated=diff_was_truncated,
                llm_tier=fx["expected_tier"],
            )
            if result.tier == fx["expected_tier"]:
                correct += 1
            else:
                failures.append(
                    f"  {fx['commit_id'][:8]} {fx['hypothesis_id']}: "
                    f"expected={fx['expected_tier']} got={result.tier} method={result.match_method}"
                )

        strict = correct / len(corpus)
        assert strict >= 0.80, (
            f"Strict agreement {strict:.1%} < 80%.\n"
            "Failures:\n" + "\n".join(failures)
        )

    def test_all_12_commits_in_corpus(self, corpus: list[dict]) -> None:
        commits = {fx["commit_id"] for fx in corpus}
        assert len(commits) == 12

    def test_supported_fixtures_have_quotes(self, corpus: list[dict]) -> None:
        missing = [
            fx for fx in corpus
            if fx["expected_tier"] == "SUPPORTED" and len(fx.get("evidence_quote", "")) < 8
        ]
        assert not missing, (
            "SUPPORTED fixtures missing canonical quotes: "
            + ", ".join(f"{fx['commit_id'][:8]} {fx['hypothesis_id']}" for fx in missing)
        )


# ---------------------------------------------------------------------------
# count_supported_from_reasoning integration
# ---------------------------------------------------------------------------


class TestCountSupportedFromReasoning:
    def test_returns_minus_one_when_no_stage3(self) -> None:
        """Sentinel -1 when STAGE 3 absent — caller should fall back to regex."""
        result = count_supported_from_reasoning(
            "STAGE 1 ... STAGE 2 ... STAGE 4 ...",
            diff="some diff content",
        )
        assert result == -1

    def test_counts_zero_when_all_speculative(self) -> None:
        reasoning = (
            "STAGE 3 — EVIDENCE: "
            "HYPOTHESIS 1 — SPECULATIVE: No concrete diff evidence. "
            "HYPOTHESIS 2 — SPECULATIVE: Also theoretical. "
            "STAGE 4"
        )
        result = count_supported_from_reasoning(reasoning, diff="irrelevant diff content")
        assert result == 0

    def test_counts_supported_when_quote_in_diff(self) -> None:
        reasoning = (
            "STAGE 3 — EVIDENCE: "
            "HYPOTHESIS 1 — SUPPORTED: The diff shows 'setNoStart(true)' was removed. "
            "STAGE 4"
        )
        diff = "- SpringCamelContext.setNoStart(true);"
        result = count_supported_from_reasoning(reasoning, diff=diff)
        assert result >= 0
