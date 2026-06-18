"""Problem extraction from JIRA tickets for bug attribution.

Two levels:
  Level 1 (default): Regex-based signal extraction — file paths, CamelCase
      symbols, and keywords from title + description.
  Level 2 (future): LLM-synthesized ProblemStatement with richer hints.

The ProblemStatement is the attribution agent's input. It contains
ONLY temporally valid information (JIRA ticket created before the fix).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from commit_investigator.extraction.jira_client import JiraIssue

_FILE_PATH_RE = re.compile(
    r"(?<![a-zA-Z0-9_/])"
    r"(?:[a-zA-Z0-9_]+/)?"
    r"(?:[a-zA-Z0-9_]+/)*"
    r"[A-Z][a-zA-Z0-9_]*"
    r"\."
    r"(?:java|scala|groovy|py|kt|xml|properties|yaml|yml|json|sql|sh|rb|go|rs|c|cpp|h|hpp|cs)"
    r"(?![a-zA-Z0-9_])"
)

_CAMEL_CASE_RE = re.compile(
    r"(?<![a-zA-Z0-9_.])"
    r"[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+"
    r"(?![a-zA-Z0-9_])"
)

_ACRONYM_CAMEL_RE = re.compile(
    r"(?<![a-zA-Z0-9_.])"
    r"[A-Z][a-z]+[A-Z]{2,}[a-zA-Z0-9]*"
    r"(?![a-zA-Z0-9_])"
)

_LOWER_CAMEL_RE = re.compile(
    r"(?<![a-zA-Z0-9_.])"
    r"[a-z]{2,}(?:[A-Z][a-z0-9]+|[A-Z]{2,}[a-z0-9]*)+"
    r"(?![a-zA-Z0-9_])"
)

_MIN_SYMBOL_LENGTH = 6

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must", "ought",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "and", "but", "or", "nor", "not", "so", "yet", "both",
    "either", "neither", "each", "every", "all", "any", "few", "more",
    "most", "other", "some", "such", "no", "only", "same", "than",
    "too", "very", "just", "because", "if", "when", "where", "how",
    "what", "which", "who", "whom", "this", "that", "these", "those",
    "it", "its", "i", "we", "you", "he", "she", "they", "me", "him",
    "her", "us", "them", "my", "our", "your", "his", "their",
    "about", "up", "down", "here", "there", "while", "also",
})


_INFERRED_EXTENSIONS = ("java", "scala", "groovy")


def _extract_file_paths(text: str) -> list[str]:
    """Extract file paths (e.g., Foo.java, path/to/Bar.scala) from text."""
    return list(dict.fromkeys(_FILE_PATH_RE.findall(text)))


def _infer_files_from_symbols(symbols: list[str], existing_files: list[str]) -> list[str]:
    """Derive potential filenames from PascalCase symbols (class → ClassName.java).

    In JVM projects, PascalCase class names map to same-named source files.
    Skips lowerCamelCase symbols since those are methods/variables, not classes.
    Only adds inferences not already present in extracted_files.
    """
    existing_lower = {f.lower() for f in existing_files}
    inferred: list[str] = []
    for symbol in symbols:
        if not symbol[0].isupper():
            continue
        for ext in _INFERRED_EXTENSIONS:
            candidate = f"{symbol}.{ext}"
            if candidate.lower() not in existing_lower:
                existing_lower.add(candidate.lower())
                inferred.append(candidate)
    return inferred


def _extract_symbols(text: str) -> list[str]:
    """Extract code identifiers from text: PascalCase, acronym CamelCase, lowerCamelCase."""
    hits: list[str] = []
    hits.extend(_CAMEL_CASE_RE.findall(text))
    hits.extend(_ACRONYM_CAMEL_RE.findall(text))
    hits.extend(_LOWER_CAMEL_RE.findall(text))
    return [s for s in dict.fromkeys(hits) if len(s) >= _MIN_SYMBOL_LENGTH]


def _extract_keywords(title: str) -> list[str]:
    """Extract keywords from title: split, filter stopwords, lowercase."""
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]*", title)
    seen: set[str] = set()
    keywords: list[str] = []
    for word in words:
        lower = word.lower()
        if lower in _STOPWORDS or len(lower) < 2:
            continue
        if lower not in seen:
            seen.add(lower)
            keywords.append(lower)
    return keywords


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
    extracted_keywords: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.title.strip() and not self.description.strip()

    def to_prompt_text(self) -> str:
        """Format for injection into the attribution agent's system prompt.

        .. deprecated::
            Legacy format ('## Bug Report: ...'). Current uses
            ``governance.prompt_assembler.assemble_prompt()`` which renders
            Problem Statement in its own ADR-compliant Title/Description format.
            Do not call this method when using PromptAssembler.
        """
        parts = [f"## Bug Report: {self.title}"]
        if self.description:
            parts.append("")
            parts.append(self.description)
        return "\n".join(parts)


class ProblemExtractor:
    """Creates ProblemStatements from JIRA tickets.

    Level 1: Regex-based signal extraction (file paths, CamelCase symbols,
        keywords from title). No LLM cost.
    Level 2: LLM-synthesized with richer extraction (future).
    """

    def from_jira_issue(
        self,
        issue: JiraIssue,
        project: str,
    ) -> ProblemStatement:
        """Level 1: regex-based extraction from JIRA issue.

        Extracts file paths and CamelCase symbols from title + description.
        Keywords come from title only (description is too noisy).
        """
        title = issue.summary
        description = issue.description or ""
        return self._build_with_extraction(title, description, project, issue.key)

    def from_raw(
        self,
        title: str,
        description: str,
        project: str,
        issue_key: str = "",
    ) -> ProblemStatement:
        """Create a ProblemStatement from raw strings with Level 1 extraction."""
        return self._build_with_extraction(title, description, project, issue_key)

    def _build_with_extraction(
        self,
        title: str,
        description: str,
        project: str,
        issue_key: str,
    ) -> ProblemStatement:
        combined_text = f"{title}\n{description}"
        explicit_files = _extract_file_paths(combined_text)
        symbols = _extract_symbols(combined_text)
        inferred = _infer_files_from_symbols(symbols, explicit_files)
        return ProblemStatement(
            title=title,
            description=description,
            project=project,
            issue_key=issue_key,
            extracted_files=explicit_files + inferred,
            extracted_symbols=symbols,
            extracted_keywords=_extract_keywords(title),
        )
