"""Problem extraction from JIRA tickets for bug attribution.

Two levels:
  Level 1 (default): Raw pass-through — title and description from JIRA.
  Level 2 (future): LLM-synthesized ProblemStatement with extracted
      file hints, error patterns, and component signals.

The ProblemStatement is the attribution agent's input. It contains
ONLY temporally valid information (JIRA ticket created before the fix).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from commit_investigator.infra.jira_client import JiraIssue


@dataclass(frozen=True)
class ProblemStatement:
    """Structured bug report derived from a JIRA ticket.

    This is the primary input to the Attribution Agent. It contains
    information available at bug-report time (before any fix exists).
    """

    title: str
    description: str
    project: str
    issue_key: str = ""
    extracted_files: list[str] = field(default_factory=list)
    extracted_symbols: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.title.strip() and not self.description.strip()

    def to_prompt_text(self) -> str:
        """Format for injection into the attribution agent's system prompt."""
        parts = [f"## Bug Report: {self.title}"]
        if self.description:
            parts.append("")
            parts.append(self.description)
        return "\n".join(parts)


class ProblemExtractor:
    """Creates ProblemStatements from JIRA tickets.

    Level 1: raw pass-through (title + description, no LLM).
    Level 2: LLM-synthesized with extracted file/symbol hints (future).
    """

    def from_jira_issue(
        self,
        issue: JiraIssue,
        project: str,
    ) -> ProblemStatement:
        """Level 1: raw JIRA pass-through.

        Takes the JIRA summary as title and description as-is.
        Empty descriptions are preserved (the agent must handle them).
        """
        return ProblemStatement(
            title=issue.summary,
            description=issue.description or "",
            project=project,
            issue_key=issue.key,
        )

    def from_raw(
        self,
        title: str,
        description: str,
        project: str,
        issue_key: str = "",
    ) -> ProblemStatement:
        """Create a ProblemStatement from raw strings (for testing or manual input)."""
        return ProblemStatement(
            title=title,
            description=description,
            project=project,
            issue_key=issue_key,
        )
