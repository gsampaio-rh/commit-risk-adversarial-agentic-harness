"""Early-stopping monitor for scaled multi-model evaluation runs.

Tracks per-model running metrics and checks 5 hard-stop rules after each case.
Also tracks global spend across all models for the budget cap.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class Verdict(Enum):
    CONTINUE = "continue"
    ABORT_MODEL = "abort_model"
    ABORT_ALL = "abort_all"


@dataclass
class StopDecision:
    verdict: Verdict
    reason: str = ""


@dataclass
class _ModelTracker:
    """Per-model running statistics."""

    n_completed: int = 0
    n_hits: int = 0
    total_cost: float = 0.0
    total_elapsed_s: float = 0.0
    latency_window: deque[float] = field(default_factory=lambda: deque(maxlen=10))
    recent_errors: deque[bool] = field(default_factory=lambda: deque(maxlen=10))


class EarlyStopMonitor:
    """Checks hard-stop rules after each eval case.

    Rules:
      1. Performance floor: Hit@5 < threshold after min_cases → abort model
      2. Latency ceiling: rolling avg > limit → abort model
      3. Cost ceiling: per-model total > limit → abort model
      4. Error rate: > error_pct failures in last window_size → abort model
      5. Global budget: total across all models > limit → abort all
    """

    def __init__(
        self,
        *,
        perf_floor: float = 0.20,
        perf_min_cases: int = 10,
        latency_ceiling_s: float = 600.0,
        cost_ceiling_per_model: float = 15.0,
        error_rate_pct: float = 0.30,
        error_window: int = 10,
        global_budget: float = 50.0,
    ) -> None:
        self._perf_floor = perf_floor
        self._perf_min_cases = perf_min_cases
        self._latency_ceiling_s = latency_ceiling_s
        self._cost_ceiling_per_model = cost_ceiling_per_model
        self._error_rate_pct = error_rate_pct
        self._error_window = error_window
        self._global_budget = global_budget

        self._trackers: dict[str, _ModelTracker] = {}
        self._global_cost: float = 0.0

    @property
    def global_cost(self) -> float:
        return self._global_cost

    def replay_checkpoint(self, completed_models: dict[str, dict]) -> None:
        """Restore monitor state from checkpoint data.

        Replays prior case results through tracker accumulators so that
        hard-stop rules have correct history after a --resume. Does NOT
        check rules during replay — only rebuilds counters and windows.
        """
        for model_id, model_data in completed_models.items():
            for result in model_data.get("case_results", []):
                tracker = self._get_tracker(model_id)
                status = result.get("status", "error")
                is_error = status != "completed"

                tracker.recent_errors.append(is_error)

                if not is_error:
                    tracker.n_completed += 1
                    if result.get("hit_at_5"):
                        tracker.n_hits += 1
                    elapsed_s = result.get("elapsed_ms", 0) / 1000.0
                    tracker.total_elapsed_s += elapsed_s
                    tracker.latency_window.append(elapsed_s)

                cost = result.get("estimated_cost", 0.0) or 0.0
                tracker.total_cost += cost
                self._global_cost += cost

    def _get_tracker(self, model: str) -> _ModelTracker:
        if model not in self._trackers:
            self._trackers[model] = _ModelTracker()
        return self._trackers[model]

    def update(self, model: str, case_result: dict) -> StopDecision:
        """Record a case result and check all hard-stop rules.

        Args:
            model: Model identifier.
            case_result: Dict with keys: status, hit_at_5, elapsed_ms,
                         estimated_cost (optional).

        Returns:
            StopDecision with verdict and reason.
        """
        tracker = self._get_tracker(model)
        status = case_result.get("status", "error")
        is_error = status != "completed"

        tracker.recent_errors.append(is_error)

        if not is_error:
            tracker.n_completed += 1
            if case_result.get("hit_at_5"):
                tracker.n_hits += 1

            elapsed_s = case_result.get("elapsed_ms", 0) / 1000.0
            tracker.total_elapsed_s += elapsed_s
            tracker.latency_window.append(elapsed_s)

        cost = case_result.get("estimated_cost", 0.0) or 0.0
        tracker.total_cost += cost
        self._global_cost += cost

        return self._check_rules(model, tracker)

    def _check_rules(self, model: str, t: _ModelTracker) -> StopDecision:
        if self._global_cost >= self._global_budget:
            return StopDecision(
                Verdict.ABORT_ALL,
                f"Global budget ${self._global_budget:.0f} exceeded "
                f"(${self._global_cost:.2f} spent)",
            )

        if t.total_cost >= self._cost_ceiling_per_model:
            return StopDecision(
                Verdict.ABORT_MODEL,
                f"Model cost ${t.total_cost:.2f} exceeds "
                f"${self._cost_ceiling_per_model:.0f} ceiling",
            )

        if (
            len(t.recent_errors) >= self._error_window
            and sum(t.recent_errors) / len(t.recent_errors) > self._error_rate_pct
        ):
            error_count = sum(t.recent_errors)
            return StopDecision(
                Verdict.ABORT_MODEL,
                f"{error_count}/{len(t.recent_errors)} recent cases failed "
                f"(>{self._error_rate_pct:.0%} threshold)",
            )

        if (
            len(t.latency_window) >= t.latency_window.maxlen  # type: ignore[operator]
            and sum(t.latency_window) / len(t.latency_window) > self._latency_ceiling_s
        ):
            avg = sum(t.latency_window) / len(t.latency_window)
            return StopDecision(
                Verdict.ABORT_MODEL,
                f"Rolling latency avg {avg:.0f}s exceeds "
                f"{self._latency_ceiling_s:.0f}s ceiling",
            )

        if t.n_completed >= self._perf_min_cases:
            hit_rate = t.n_hits / t.n_completed
            if hit_rate < self._perf_floor:
                return StopDecision(
                    Verdict.ABORT_MODEL,
                    f"Hit@5={hit_rate:.3f} after {t.n_completed} cases "
                    f"(below {self._perf_floor:.2f} floor)",
                )

        return StopDecision(Verdict.CONTINUE)

    def model_summary(self, model: str) -> dict:
        """Return current aggregate stats for a model."""
        t = self._get_tracker(model)
        hit_rate = t.n_hits / t.n_completed if t.n_completed else 0.0
        avg_latency = t.total_elapsed_s / t.n_completed if t.n_completed else 0.0
        return {
            "model": model,
            "n_completed": t.n_completed,
            "n_hits": t.n_hits,
            "hit_at_5": round(hit_rate, 4),
            "avg_latency_s": round(avg_latency, 1),
            "total_cost": round(t.total_cost, 4),
        }
