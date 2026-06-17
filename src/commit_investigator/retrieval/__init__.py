"""Deterministic retrieval — CandidateSet from git signals."""

from commit_investigator.retrieval.pipeline import RetrievalResult, prepare_investigation
from commit_investigator.retrieval.retriever import (
    RecallDiagnostic,
    RetrievalConfig,
    compute_recall_at_k,
    retrieve_candidates,
)

__all__ = [
    "RecallDiagnostic",
    "RetrievalConfig",
    "RetrievalResult",
    "compute_recall_at_k",
    "prepare_investigation",
    "retrieve_candidates",
]
