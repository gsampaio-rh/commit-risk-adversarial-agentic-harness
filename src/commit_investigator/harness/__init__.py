"""V4 investigation harness — state machine governing the agent pipeline."""

from commit_investigator.harness.brief_validator import BriefValidator, ValidationResult
from commit_investigator.harness.completion import CompletionEvaluator, CompletionStatus
from commit_investigator.harness.harness import InvestigationHarness, InvestigationOutcome
from commit_investigator.harness.llm_protocol import LLMProvider, LLMResponse
from commit_investigator.harness.trace_writer import InvestigationTrace, TraceWriter
from commit_investigator.harness.v4_runner import V4InvestigationResult, run_v4_investigation

__all__ = [
    "BriefValidator",
    "CompletionEvaluator",
    "CompletionStatus",
    "InvestigationHarness",
    "InvestigationOutcome",
    "InvestigationTrace",
    "LLMProvider",
    "LLMResponse",
    "TraceWriter",
    "V4InvestigationResult",
    "ValidationResult",
    "run_v4_investigation",
]
