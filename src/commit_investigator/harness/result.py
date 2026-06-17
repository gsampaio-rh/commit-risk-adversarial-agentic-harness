"""Investigation result — data structure used by eval scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from commit_investigator.harness.trace_writer import InvestigationTrace


@dataclass
class InvestigationResult:
    """Complete result of an investigation for eval scoring."""

    issue_key: str
    suspects: list[dict[str, Any]] = field(default_factory=list)
    trace: InvestigationTrace | None = None
    retrieval_recall: bool = False
    error: str | None = None
