"""Markdown-based governance skills: loader and keyword retriever."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[3] / "data" / "governance" / "skills"


VALID_SCOPES = frozenset({"project", "general"})


@dataclass(frozen=True)
class GovernanceSkill:
    """One governance skill parsed from a Markdown file with YAML frontmatter."""

    id: str
    scope: str
    project: str | None
    triggers: list[str]
    source: str
    trace_ref: str
    body: str

    def __post_init__(self) -> None:
        if self.scope == "project" and not self.project:
            msg = "scope='project' requires a non-empty project value"
            raise ValueError(msg)

    @classmethod
    def from_markdown(cls, text: str) -> GovernanceSkill:
        """Parse a Markdown file with YAML frontmatter delimited by '---'."""
        parts = text.split("---", 2)
        if len(parts) < 3:
            msg = "Skill file missing YAML frontmatter delimiters (---)"
            raise ValueError(msg)

        frontmatter: dict[str, Any] = yaml.safe_load(parts[1]) or {}
        body = parts[2].strip()

        project_val = frontmatter.get("project", None)
        if project_val == "" or project_val is None:
            project_val = None

        return cls(
            id=frontmatter["id"],
            scope=frontmatter.get("scope", "general"),
            project=project_val,
            triggers=[str(t) for t in frontmatter.get("triggers", [])],
            source=frontmatter.get("source", "manual"),
            trace_ref=frontmatter.get("trace_ref", ""),
            body=body,
        )


def _load_all_skills(skills_dir: Path) -> list[GovernanceSkill]:
    """Load all .md skill files from a directory (excludes subdirectories like drafts/)."""
    if not skills_dir.is_dir():
        return []

    skills: list[GovernanceSkill] = []
    for path in sorted(skills_dir.glob("*.md")):
        try:
            skill = GovernanceSkill.from_markdown(path.read_text(encoding="utf-8"))
            skills.append(skill)
        except Exception:
            logger.warning("Skipping malformed skill file: %s", path, exc_info=True)
    return skills


def _keyword_score(skill: GovernanceSkill, keywords: list[str]) -> int:
    """Count trigger-keyword overlaps (case-insensitive)."""
    keyword_lower = {kw.lower() for kw in keywords}
    return sum(1 for t in skill.triggers if t.lower() in keyword_lower)


def retrieve_skills(
    problem_statement: Any,
    top_k: int = 3,
    *,
    skills_dir: Path | None = None,
) -> list[tuple[GovernanceSkill, int]]:
    """Retrieve top-k skills by keyword overlap with a ProblemStatement.

    Returns list of (skill, score) tuples ranked by score descending.
    score = |triggers ∩ extracted_keywords| (case-insensitive).
    Only skills with score > 0 are returned.

    Scope filtering: project-scoped skills only match when
    problem_statement.project matches (case-insensitive).

    Tie-breaking: project-scoped skills rank above general on equal score.
    Secondary tie-break: alphabetical by id for determinism.
    """
    directory = skills_dir if skills_dir is not None else DEFAULT_SKILLS_DIR
    all_skills = _load_all_skills(directory)
    if not all_skills:
        return []

    keywords: list[str] = getattr(problem_statement, "extracted_keywords", [])
    project: str = getattr(problem_statement, "project", "")

    scored: list[tuple[int, int, str, GovernanceSkill]] = []
    for skill in all_skills:
        if skill.scope == "project" and skill.project is not None:
            if skill.project.lower() != project.lower():
                continue

        score = _keyword_score(skill, keywords)
        if score <= 0:
            continue

        scope_priority = 0 if skill.scope == "project" else 1
        scored.append((score, scope_priority, skill.id, skill))

    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [(item[3], item[0]) for item in scored[:top_k]]
