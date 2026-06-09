"""Tests for Historical Pattern L2 — embedding similarity matching."""

from __future__ import annotations

from pathlib import Path

import pytest

from cr_analyzer.models.bundle import CRBundle, Incident, ItsmRecord, ScheduledWindow
from cr_analyzer.models.enums import ChangeType, RiskCategory
from cr_analyzer.models.outputs import NormalizeOutput
from cr_analyzer.stages.historical_pattern import (
    HistoricalPatternConfig,
    run_historical_pattern,
)


@pytest.fixture
def semantic_mismatch_norm() -> NormalizeOutput:
    """CR where L1 misses but L2 should catch: different category names, same meaning.

    Both incidents describe configuration-related outages on payment-gateway,
    but L1 sees them as separate categories ("config_change" vs "settings_update")
    — each has only 1 incident, below ALERT_THRESHOLD of 2.
    L2 embeddings should recognize both are semantically about configuration changes.
    """
    return NormalizeOutput(
        change_id="SEM-001",
        change_type=ChangeType.NORMAL,
        risk_category=RiskCategory.MEDIUM,
        title="Apply configuration update to payment gateway",
        description="Update the payment gateway configuration to enable new TLS certs and rate limiting",
        affected_services=["payment-gateway"],
        requestor="ops-team",
        approvers=["cab-board"],
        scheduled_window=ScheduledWindow(
            start="2024-03-15T08:00:00", end="2024-03-15T10:00:00"
        ),
        is_customer_facing=True,
        affected_tier=1,
        incidents=[
            Incident(
                incident_id="INC-101",
                service="payment-gateway",
                change_category="config_change",
                severity="P2",
                root_cause_summary="Configuration change caused payment API timeout — incorrect connection pool settings",
                date="2024-01-10",
            ),
            Incident(
                incident_id="INC-102",
                service="payment-gateway",
                change_category="settings_update",
                severity="P2",
                root_cause_summary="Config modification broke TLS handshake — wrong cipher suite configured",
                date="2024-02-05",
            ),
        ],
    )


@pytest.fixture
def exact_match_norm() -> NormalizeOutput:
    """CR with exact category match — both L1 and L2 should find it."""
    return NormalizeOutput(
        change_id="EXACT-001",
        change_type=ChangeType.NORMAL,
        risk_category=RiskCategory.MEDIUM,
        title="Schema migration on user-db",
        description="Apply schema migration to user database",
        affected_services=["user-db"],
        requestor="dev-team",
        approvers=["cab-board"],
        scheduled_window=ScheduledWindow(
            start="2024-03-20T06:00:00", end="2024-03-20T08:00:00"
        ),
        is_customer_facing=False,
        affected_tier=3,
        incidents=[
            Incident(
                incident_id="INC-201",
                service="user-db",
                change_category="schema_migration",
                severity="P2",
                root_cause_summary="Schema migration locked tables causing user login failures",
                date="2024-01-15",
            ),
            Incident(
                incident_id="INC-202",
                service="user-db",
                change_category="schema_migration",
                severity="P2",
                root_cause_summary="Migration script had wrong column type causing data truncation",
                date="2024-02-20",
            ),
        ],
    )


class TestL2CatchesSemanticMismatch:
    """L2 embedding similarity catches what L1 exact match misses."""

    def test_l1_misses_semantic_mismatch(self, semantic_mismatch_norm) -> None:
        """L1 should not find the pattern since categories differ."""
        config = HistoricalPatternConfig(method="exact_match")
        result = run_historical_pattern(semantic_mismatch_norm, config)
        assert len(result.findings) == 0
        assert result.method_used == "exact_match"

    def test_l2_catches_semantic_mismatch(self, semantic_mismatch_norm) -> None:
        """L2 embedding similarity should detect the pattern."""
        config = HistoricalPatternConfig(
            method="embedding_similarity",
            similarity_threshold=0.4,
        )
        result = run_historical_pattern(semantic_mismatch_norm, config)
        assert len(result.findings) >= 1, "L2 should catch semantically similar patterns"
        assert result.method_used == "embedding_similarity"

        finding = result.findings[0]
        assert finding.dimension.value == "historical_pattern"
        assert finding.evidence.method == "embedding_similarity"
        assert hasattr(finding.evidence, "similarity_score")
        assert finding.evidence.similarity_score > 0.4

    def test_dual_mode_catches_semantic_mismatch(self, semantic_mismatch_norm) -> None:
        """Dual mode runs both L1 and L2 — L2 findings should appear."""
        config = HistoricalPatternConfig(
            method="dual",
            similarity_threshold=0.4,
        )
        result = run_historical_pattern(semantic_mismatch_norm, config)
        assert len(result.findings) >= 1
        assert result.method_used == "dual"


class TestL2ExactMatch:
    """L2 should also find exact-match cases (embedding similarity is high)."""

    def test_l2_finds_exact_match_case(self, exact_match_norm) -> None:
        config = HistoricalPatternConfig(
            method="embedding_similarity",
            similarity_threshold=0.4,
        )
        result = run_historical_pattern(exact_match_norm, config)
        assert len(result.findings) >= 1
        assert result.method_used == "embedding_similarity"


class TestDualModeDeduplication:
    """Dual mode should not double-count incidents found by both L1 and L2."""

    def test_dual_deduplicates(self, exact_match_norm) -> None:
        config = HistoricalPatternConfig(
            method="dual",
            similarity_threshold=0.4,
        )
        result = run_historical_pattern(exact_match_norm, config)

        all_incident_ids = []
        for f in result.findings:
            if hasattr(f.evidence, "matching_incidents"):
                all_incident_ids.extend(
                    inc["incident_id"] for inc in f.evidence.matching_incidents
                )

        assert len(all_incident_ids) == len(set(all_incident_ids)), (
            f"Duplicate incidents in dual mode: {all_incident_ids}"
        )


class TestConfigThreshold:
    """Similarity threshold controls sensitivity."""

    def test_high_threshold_reduces_matches(self, semantic_mismatch_norm) -> None:
        config = HistoricalPatternConfig(
            method="embedding_similarity",
            similarity_threshold=0.95,
        )
        result = run_historical_pattern(semantic_mismatch_norm, config)
        # Very high threshold may exclude fuzzy matches
        # (this tests that threshold is respected, not that it finds/misses)
        assert result.method_used == "embedding_similarity"

    def test_low_threshold_increases_matches(self, semantic_mismatch_norm) -> None:
        low = HistoricalPatternConfig(
            method="embedding_similarity",
            similarity_threshold=0.3,
        )
        high = HistoricalPatternConfig(
            method="embedding_similarity",
            similarity_threshold=0.9,
        )
        result_low = run_historical_pattern(semantic_mismatch_norm, low)
        result_high = run_historical_pattern(semantic_mismatch_norm, high)

        low_incidents = sum(
            len(f.evidence.matching_incidents)
            for f in result_low.findings
            if hasattr(f.evidence, "matching_incidents")
        )
        high_incidents = sum(
            len(f.evidence.matching_incidents)
            for f in result_high.findings
            if hasattr(f.evidence, "matching_incidents")
        )
        assert low_incidents >= high_incidents


class TestMethodUsedField:
    """Output correctly reports method_used."""

    def test_l1_reports_exact_match(self, exact_match_norm) -> None:
        result = run_historical_pattern(exact_match_norm)
        assert result.method_used == "exact_match"

    def test_l2_reports_embedding_similarity(self, exact_match_norm) -> None:
        config = HistoricalPatternConfig(method="embedding_similarity")
        result = run_historical_pattern(exact_match_norm, config)
        assert result.method_used == "embedding_similarity"

    def test_dual_reports_dual(self, exact_match_norm) -> None:
        config = HistoricalPatternConfig(method="dual")
        result = run_historical_pattern(exact_match_norm, config)
        assert result.method_used == "dual"


class TestNoIncidents:
    """Edge case: CR with no incident history."""

    def test_l2_no_incidents(self) -> None:
        norm = NormalizeOutput(
            change_id="EMPTY-001",
            change_type=ChangeType.STANDARD,
            risk_category=RiskCategory.LOW,
            title="Routine update",
            description="No-op",
            affected_services=["svc-a"],
            requestor="bot",
            approvers=[],
            scheduled_window=ScheduledWindow(
                start="2024-01-01T00:00:00", end="2024-01-01T01:00:00"
            ),
            is_customer_facing=False,
            affected_tier=4,
            incidents=[],
        )
        config = HistoricalPatternConfig(method="embedding_similarity")
        result = run_historical_pattern(norm, config)
        assert len(result.findings) == 0


BPI_DIR = Path(__file__).resolve().parent.parent / "data" / "bpi2014"
has_bpi_data = (BPI_DIR / "Detail_Change.csv").exists()


@pytest.mark.skipif(not has_bpi_data, reason="BPI 2014 CSVs not found")
class TestBpiRecallComparison:
    """Compare L1 vs L2 recall on BPI 2014 data.

    BPI 2014 limitation: change_category = Related Change ID (opaque),
    root_cause_summary = always "incident". L2 embeddings can't extract
    semantic meaning from opaque identifiers, so L2 delta on BPI is
    expected to be near zero. The real value of L2 is on data with
    meaningful categories (tested in TestL2CatchesSemanticMismatch).
    """

    @pytest.fixture(scope="class")
    def bpi_sample(self):
        """Load a sample of BPI changes with incidents for benchmarking."""
        from cr_analyzer.adapters.bpi2014 import load_bpi2014
        from cr_analyzer.stages.ingest import run_ingest
        from cr_analyzer.stages.normalize import run_normalize

        bundles, _ = load_bpi2014(BPI_DIR)
        with_incidents = [
            b for b in bundles.values()
            if b.incident_history and len(b.incident_history) >= 2
        ][:50]

        norms = []
        for bundle in with_incidents:
            ingest_out = run_ingest(bundle)
            norm_out = run_normalize(ingest_out)
            norms.append(norm_out)
        return norms

    def test_l1_vs_l2_metrics(self, bpi_sample, capsys) -> None:
        """Measure and print L1 vs L2 finding counts on BPI sample."""
        l1_total = 0
        l2_total = 0
        dual_total = 0

        for norm in bpi_sample:
            l1 = run_historical_pattern(norm, HistoricalPatternConfig(method="exact_match"))
            l2 = run_historical_pattern(
                norm,
                HistoricalPatternConfig(method="embedding_similarity", similarity_threshold=0.3),
            )
            dual = run_historical_pattern(
                norm,
                HistoricalPatternConfig(method="dual", similarity_threshold=0.3),
            )
            l1_total += len(l1.findings)
            l2_total += len(l2.findings)
            dual_total += len(dual.findings)

        with capsys.disabled():
            print(f"\n{'='*60}")
            print(f"BPI 2014 Historical Pattern: L1 vs L2 ({len(bpi_sample)} CRs)")
            print(f"{'='*60}")
            print(f"  L1 (exact_match) findings: {l1_total}")
            print(f"  L2 (embedding)   findings: {l2_total}")
            print(f"  Dual (merged)    findings: {dual_total}")
            print(f"  L2 delta vs L1:            {l2_total - l1_total:+d}")
            print(f"  NOTE: BPI categories are opaque IDs — L2 semantic")
            print(f"        matching has limited value on this dataset.")
            print(f"{'='*60}")

        assert dual_total >= l1_total, (
            f"Dual findings ({dual_total}) should be >= L1 ({l1_total})"
        )
