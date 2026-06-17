"""Investigation harness — scoped tools pipeline."""

from commit_investigator.harness.scoped_runner import run_scoped_investigation
from commit_investigator.harness.trace_writer import InvestigationTrace, TraceWriter
from commit_investigator.harness.result import InvestigationResult

__all__ = [
    "InvestigationTrace",
    "TraceWriter",
    "InvestigationResult",
    "run_scoped_investigation",
]
