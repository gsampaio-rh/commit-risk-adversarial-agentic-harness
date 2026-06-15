"""Tests for V3 suspect evidence scoring."""

import pytest

from commit_investigator.analysis.evidence_tagger import (
    SuspectEvidenceScore,
    score_suspect_evidence,
)


SAMPLE_DIFF = """\
diff --git a/src/Main.java b/src/Main.java
--- a/src/Main.java
+++ b/src/Main.java
@@ -10,6 +10,8 @@
     public void configure() {
-        if (context != null) {
-            context.start();
-        }
+        context.start();
     }
"""


class TestScoreSuspectEvidence:
    def test_grounded_quote(self) -> None:
        score = score_suspect_evidence(
            commit_id="abc123",
            evidence_quotes=["context.start()"],
            diff=SAMPLE_DIFF,
        )
        assert score.grounded_quotes == 1
        assert score.grounding_rate == 1.0
        assert score.per_quote[0].tier == "SUPPORTED"

    def test_ungrounded_quote(self) -> None:
        score = score_suspect_evidence(
            commit_id="abc123",
            evidence_quotes=["this text does not appear in the diff at all anywhere"],
            diff=SAMPLE_DIFF,
        )
        assert score.grounded_quotes == 0
        assert score.grounding_rate == 0.0
        assert score.per_quote[0].tier == "SPECULATIVE"

    def test_mixed_quotes(self) -> None:
        score = score_suspect_evidence(
            commit_id="abc123",
            evidence_quotes=[
                "context.start()",
                "this is a hallucinated quote that does not exist in the diff",
            ],
            diff=SAMPLE_DIFF,
        )
        assert score.total_quotes == 2
        assert score.grounded_quotes == 1
        assert score.grounding_rate == 0.5

    def test_no_quotes(self) -> None:
        score = score_suspect_evidence(
            commit_id="abc123",
            evidence_quotes=[],
            diff=SAMPLE_DIFF,
        )
        assert score.total_quotes == 0
        assert score.grounding_rate == 0.0

    def test_no_diff_available(self) -> None:
        score = score_suspect_evidence(
            commit_id="abc123",
            evidence_quotes=["some quote here and more"],
            diff=None,
        )
        assert score.grounded_quotes == 0
        assert score.per_quote[0].tier == "UNVERIFIABLE"

    def test_normalized_score_matches_grounding_rate(self) -> None:
        score = score_suspect_evidence(
            commit_id="abc",
            evidence_quotes=["context.start()"],
            diff=SAMPLE_DIFF,
        )
        assert score.normalized_score == score.grounding_rate
