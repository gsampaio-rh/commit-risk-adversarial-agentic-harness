"""Tests for evaluation harness."""

from __future__ import annotations

from pathlib import Path

import pytest

BPI_DIR = Path(__file__).resolve().parent.parent / "data" / "bpi2014"
has_bpi_data = (BPI_DIR / "Detail_Change.csv").exists()


@pytest.mark.skipif(not has_bpi_data, reason="BPI 2014 CSVs not found")
class TestEvaluation:
    """Run evaluation on a small subset to verify no crashes."""

    def test_eval_single_window_no_embeddings(self, tmp_path) -> None:
        from cr_analyzer.eval.evaluate import run_evaluation

        report = run_evaluation(
            BPI_DIR,
            output_dir=tmp_path / "eval-output",
            max_windows=1,
            use_embeddings=False,
        )
        assert report.total_crs_processed > 0
        assert report.task_completion_rate == 1.0
        assert report.total_windows_processed == 1
        assert (tmp_path / "eval-output" / "eval-report.json").exists()
        assert (tmp_path / "eval-output" / "eval-report.md").exists()

    def test_eval_single_window_with_embeddings(self, tmp_path) -> None:
        from cr_analyzer.eval.evaluate import run_evaluation

        report = run_evaluation(
            BPI_DIR,
            output_dir=tmp_path / "eval-output",
            max_windows=1,
            use_embeddings=True,
        )
        assert report.total_crs_processed > 0
        assert report.task_completion_rate == 1.0
        assert report.historical_pattern.dual_findings_total >= report.historical_pattern.l1_findings_total

    def test_eval_report_has_banner(self, tmp_path) -> None:
        from cr_analyzer.eval.evaluate import run_evaluation

        run_evaluation(
            BPI_DIR,
            output_dir=tmp_path / "eval-output",
            max_windows=1,
            use_embeddings=False,
        )
        md = (tmp_path / "eval-output" / "eval-report.md").read_text()
        assert "Evaluated on BPI 2014 real data" in md
        assert "L1 vs L2" in md

    def test_eval_completeness_metrics(self, tmp_path) -> None:
        from cr_analyzer.eval.evaluate import run_evaluation

        report = run_evaluation(
            BPI_DIR,
            output_dir=tmp_path / "eval-output",
            max_windows=1,
            use_embeddings=False,
        )
        assert report.completeness.pct_incomplete > 0.9, (
            f"Expected ~100% incomplete, got {report.completeness.pct_incomplete:.1%}"
        )

    def test_eval_disposition_counts(self, tmp_path) -> None:
        from cr_analyzer.eval.evaluate import run_evaluation

        report = run_evaluation(
            BPI_DIR,
            output_dir=tmp_path / "eval-output",
            max_windows=1,
            use_embeddings=False,
        )
        disp = report.disposition
        assert disp.total == report.total_crs_processed
        assert disp.approve + disp.conditional + disp.reject == disp.total
