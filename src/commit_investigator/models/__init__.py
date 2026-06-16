"""V4 core data structures for the bug attribution agent pipelines."""

from commit_investigator.models.candidates import CandidateCommit, CandidateSet
from commit_investigator.models.investigation import (
    BudgetState,
    CompletionCriteria,
    ExaminationStep,
    Hypothesis,
    InvestigationBrief,
    InvestigationState,
)
from commit_investigator.models.trace import (
    CompletionCheck,
    EliminationRecord,
    EvidenceRecord,
    HypothesisRecord,
    HypothesisStatus,
    InvestigationTrace,
    OutcomeRecord,
    StrategyRecord,
    TraceToolCall,
    TurnRecord,
)

__all__ = [
    "BudgetState",
    "CandidateCommit",
    "CandidateSet",
    "CompletionCheck",
    "CompletionCriteria",
    "EliminationRecord",
    "EvidenceRecord",
    "ExaminationStep",
    "Hypothesis",
    "HypothesisRecord",
    "HypothesisStatus",
    "InvestigationBrief",
    "InvestigationState",
    "InvestigationTrace",
    "OutcomeRecord",
    "StrategyRecord",
    "TraceToolCall",
    "TurnRecord",
]
