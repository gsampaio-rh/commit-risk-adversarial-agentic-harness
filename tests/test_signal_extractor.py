"""Unit tests for signal_extractor confidence signals."""

from commit_investigator.context.context_builder import InvestigationContext
from commit_investigator.analysis.evidence_tagger import TagResult
from commit_investigator.analysis.signal_extractor import extract_confidence_signals
from commit_investigator.hypothesis.hypothesis_engine import HypothesisSpec


def _context(**overrides) -> InvestigationContext:
    defaults = {
        "commit_id": "abc123",
        "project": "camel",
        "diff": "diff --git a/Foo.java b/Foo.java\n- old\n+ new\n context",
        "message": "CAMEL-1234 fix null pointer",
        "touched_files": ["Foo.java"],
        "csv_features": {},
        "file_histories": {},
        "author_stats": None,
        "missing_reasons": [],
        "router_probability": None,
    }
    defaults.update(overrides)
    return InvestigationContext(**defaults)


def _tag(tier: str = "SUPPORTED") -> TagResult:
    return TagResult(tier=tier, quote_in_diff=True, match_method="exact")


def _hypothesis(mechanism: str, quote: str = "+    fix();") -> HypothesisSpec:
    return HypothesisSpec(mechanism=mechanism, evidence_quote=quote, file="Foo.java")


class TestSupportedCount:
    def test_positive_supported_tags(self):
        signals = extract_confidence_signals(
            _context(),
            [_tag("SUPPORTED"), _tag("SPECULATIVE")],
            [_hypothesis("[logic-error] x"), _hypothesis("[api-contract] y", "")],
        )
        assert signals.supported_count == 1

    def test_negative_no_supported(self):
        signals = extract_confidence_signals(_context(), [_tag("SPECULATIVE")], [])
        assert signals.supported_count == 0


class TestEvidenceDensity:
    def test_positive_all_have_quotes(self):
        hyps = [_hypothesis("[logic-error] a"), _hypothesis("[api-contract] b")]
        signals = extract_confidence_signals(_context(), [_tag(), _tag()], hyps)
        assert signals.evidence_density == 1.0

    def test_negative_empty_hypotheses(self):
        signals = extract_confidence_signals(_context(), [], [])
        assert signals.evidence_density == 0.0


class TestRouterAgreement:
    def test_positive_agreement_when_router_high_and_base_high(self):
        ctx = _context(router_probability=0.80)
        signals = extract_confidence_signals(ctx, [_tag("SUPPORTED")], [_hypothesis("[logic-error] a")])
        assert signals.router_agreement == 1.0

    def test_neutral_when_router_below_threshold(self):
        ctx = _context(router_probability=0.69)
        signals = extract_confidence_signals(ctx, [_tag("SPECULATIVE")], [_hypothesis("[logic-error] a", "")])
        assert signals.router_agreement == 0.5
        signals = extract_confidence_signals(_context(), [_tag()], [_hypothesis("[logic-error] a")])
        assert signals.router_agreement == 0.5

    def test_disagreement_when_router_high_but_no_evidence(self):
        ctx = _context(router_probability=0.85, message="chore: bump version")
        signals = extract_confidence_signals(ctx, [], [])
        assert signals.router_agreement == 0.0


class TestHypothesisDiversity:
    def test_positive_distinct_categories_in_first_three(self):
        hyps = [
            _hypothesis("[logic-error] a"),
            _hypothesis("[api-contract] b"),
            _hypothesis("[concurrency] c"),
        ]
        signals = extract_confidence_signals(_context(), [_tag()] * 3, hyps)
        assert signals.hypothesis_diversity == 3

    def test_negative_empty_hypotheses(self):
        signals = extract_confidence_signals(_context(), [], [])
        assert signals.hypothesis_diversity == 0


class TestDiffSignalRatio:
    def test_positive_changed_lines_present(self):
        diff = "diff --git a/F.java\n- removed\n+ added\n unchanged"
        signals = extract_confidence_signals(_context(diff=diff), [], [])
        assert signals.diff_signal_ratio > 0.0

    def test_negative_empty_diff(self):
        signals = extract_confidence_signals(_context(diff=None), [], [])
        assert signals.diff_signal_ratio == 0.0


class TestMissingContextFlags:
    def test_positive_counts_missing_reasons(self):
        ctx = _context(missing_reasons=["no blame", "no tests"])
        signals = extract_confidence_signals(ctx, [], [])
        assert signals.missing_context_flags == 2

    def test_negative_no_missing_reasons(self):
        signals = extract_confidence_signals(_context(), [], [])
        assert signals.missing_context_flags == 0


class TestCommitMessageClarity:
    def test_positive_jira_key(self):
        signals = extract_confidence_signals(_context(message="CAMEL-99 fix bug"), [], [])
        assert signals.commit_message_clarity == 1.0

    def test_negative_empty_message(self):
        signals = extract_confidence_signals(_context(message=None), [], [])
        assert signals.commit_message_clarity == 0.0
