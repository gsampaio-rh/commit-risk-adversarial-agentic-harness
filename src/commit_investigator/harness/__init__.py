"""V4 investigation harness — state machine governing the agent pipeline."""

from commit_investigator.harness.brief_validator import BriefValidator, ValidationResult
from commit_investigator.harness.completion import CompletionEvaluator, CompletionStatus
from commit_investigator.harness.harness import InvestigationHarness, InvestigationOutcome
from commit_investigator.harness.llm_protocol import LLMProvider, LLMResponse

__all__ = [
    "BriefValidator",
    "CompletionEvaluator",
    "CompletionStatus",
    "InvestigationHarness",
    "InvestigationOutcome",
    "LLMProvider",
    "LLMResponse",
    "ValidationResult",
]
