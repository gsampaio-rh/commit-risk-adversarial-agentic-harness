"""Hypothesis generation and historical defect context."""

from commit_investigator.hypothesis.historical_rag import (
    get_historical_defect_context,
    reset_training_cache,
)

__all__ = ["get_historical_defect_context", "reset_training_cache"]
