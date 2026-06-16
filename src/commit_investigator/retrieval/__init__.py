"""V4 deterministic retrieval — CandidateSet from git signals."""

from commit_investigator.retrieval.retriever import (
    RecallDiagnostic,
    RetrievalConfig,
    compute_recall_at_k,
    retrieve_candidates,
)

__all__ = [
    "RecallDiagnostic",
    "RetrievalConfig",
    "compute_recall_at_k",
    "retrieve_candidates",
]
