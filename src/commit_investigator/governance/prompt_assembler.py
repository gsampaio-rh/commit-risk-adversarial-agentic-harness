"""Ordered prompt assembly for V4 investigation harness stages (ADR §Q3)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.governance.rules import GovernanceRule, load_rules
from commit_investigator.governance.skills import GovernanceSkill, retrieve_skills
from commit_investigator.models.candidates import CandidateCommit, CandidateSet
from commit_investigator.models.investigation import InvestigationBrief, InvestigationState

Stage = Literal["planning", "examination", "attribution"]

VALID_STAGES = frozenset({"planning", "examination", "attribution"})
STAGE_TITLES = {
    "planning": "Planning",
    "examination": "Examination",
    "attribution": "Attribution",
}
TRUNCATION_CANDIDATE_CAP = 10

_SYSTEM_ROLES: dict[str, str] = {
    "planning": (
        "You are a bug attribution investigator in the Planning stage. "
        "Produce a structured InvestigationBrief with falsifiable hypotheses "
        "and an examination plan."
    ),
    "examination": (
        "You are a bug attribution investigator in the Examination stage. "
        "Examine candidate commits and collect evidence quotes that test "
        "your hypotheses."
    ),
    "attribution": (
        "You are a bug attribution investigator in the Attribution stage. "
        "Rank suspect commits with confidence scores grounded in collected evidence."
    ),
}

_STAGE_INSTRUCTIONS: dict[str, str] = {
    "planning": (
        "Analyze the bug report and candidate commits below. Output JSON matching "
        "InvestigationBrief schema: hypotheses, examination_plan, success_criteria, "
        "strategy, max_effort. Each hypothesis must state what would confirm or falsify it."
    ),
    "examination": (
        "Examine the listed candidates using the investigation brief. Collect evidence "
        "quotes from commit diffs and messages that confirm or falsify each hypothesis."
    ),
    "attribution": (
        "Rank the examined suspects by likelihood of introducing the bug. Assign a "
        "confidence score between 0 and 1 to each suspect based on collected evidence."
    ),
}


@dataclass
class PromptConfig:
    token_budget: int = 100_000
    truncation_threshold: float = 0.80
    candidate_limit_stage2: int = 20
    candidate_limit_stage3_unexamined: int = 5
    max_skills: int = 3

    def __post_init__(self) -> None:
        for name in (
            "token_budget",
            "candidate_limit_stage2",
            "candidate_limit_stage3_unexamined",
            "max_skills",
            "truncation_threshold",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0, got {getattr(self, name)!r}")


def assemble_prompt(
    stage: Stage,
    problem_statement: ProblemStatement,
    candidate_set: CandidateSet,
    investigation_state: InvestigationState | None = None,
    brief: InvestigationBrief | None = None,
    evidence: list[str] | None = None,
    config: PromptConfig | None = None,
) -> str:
    if stage not in VALID_STAGES:
        raise ValueError(f"Invalid stage: {stage!r}")

    cfg = config or PromptConfig()
    hard_rules, soft_rules = load_rules(stage)
    all_skills = retrieve_skills(problem_statement, top_k=cfg.max_skills)

    evidence_items = list(evidence) if evidence else []
    candidate_line_cap: int | None = None
    skill_cap = cfg.max_skills
    candidates_truncated = False
    skills_truncated = False

    while True:
        prompt = _build_prompt(
            stage=stage,
            problem_statement=problem_statement,
            candidate_set=candidate_set,
            investigation_state=investigation_state,
            brief=brief,
            evidence_items=evidence_items,
            hard_rules=hard_rules,
            soft_rules=soft_rules,
            skills=all_skills[:skill_cap],
            config=cfg,
            candidate_line_cap=candidate_line_cap,
        )
        token_limit = int(cfg.token_budget * cfg.truncation_threshold)
        if _estimate_tokens(prompt) <= token_limit:
            return prompt

        if stage == "attribution" and evidence_items:
            evidence_items.pop(0)
            continue

        selected_count = len(
            _select_commits(stage, candidate_set, investigation_state, cfg, candidate_line_cap)
        )
        if not candidates_truncated and selected_count > TRUNCATION_CANDIDATE_CAP:
            candidate_line_cap = TRUNCATION_CANDIDATE_CAP
            candidates_truncated = True
            continue

        if not skills_truncated and len(all_skills) > 1:
            skill_cap = 1
            skills_truncated = True
            continue

        return prompt


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _build_prompt(
    *,
    stage: Stage,
    problem_statement: ProblemStatement,
    candidate_set: CandidateSet,
    investigation_state: InvestigationState | None,
    brief: InvestigationBrief | None,
    evidence_items: list[str],
    hard_rules: list[GovernanceRule],
    soft_rules: list[GovernanceRule],
    skills: list[tuple[GovernanceSkill, int]],
    config: PromptConfig,
    candidate_line_cap: int | None,
) -> str:
    stage_title = STAGE_TITLES[stage]
    sections: list[str] = []

    sections.append(f"## System Role\n{_SYSTEM_ROLES[stage]}")

    if hard_rules:
        bullets = "\n".join(f"- {rule.description}" for rule in hard_rules)
        sections.append(f"## Hard Rules ({stage_title})\n{bullets}")

    if soft_rules:
        blocks = "\n\n".join(rule.prompt_text for rule in soft_rules)
        sections.append(f"## Soft Rules ({stage_title})\n{blocks}")

    skills_body = _format_skills(skills)
    if skills_body:
        sections.append(f"## Relevant Skills\n{skills_body}")

    sections.append(f"## Stage Instructions\n{_STAGE_INSTRUCTIONS[stage]}")

    problem_body = _format_problem_statement(problem_statement)
    if problem_body:
        sections.append(f"## Problem Statement\n{problem_body}")

    candidate_section = _format_candidate_summary(
        stage, candidate_set, investigation_state, config, candidate_line_cap
    )
    if candidate_section:
        sections.append(candidate_section)

    if stage in {"examination", "attribution"} and investigation_state is not None:
        progress = _format_investigation_progress(investigation_state)
        sections.append(f"## Investigation Progress\n{progress}")

    if stage in {"examination", "attribution"} and brief is not None:
        brief_json = json.dumps(brief.to_dict(), indent=2)
        sections.append(f"## Investigation Brief\n{brief_json}")

    if stage == "attribution" and evidence_items:
        lines = "\n".join(f"{index}. {quote}" for index, quote in enumerate(evidence_items, 1))
        sections.append(f"## Evidence Collected\n{lines}")

    return "\n\n".join(sections)


def _format_problem_statement(problem_statement: ProblemStatement) -> str | None:
    if problem_statement.is_empty:
        return None
    lines = [
        f"Title: {problem_statement.title}",
        f"Description: {problem_statement.description}",
    ]
    if problem_statement.extracted_files:
        joined = ", ".join(problem_statement.extracted_files)
        lines.append(f"Extracted files: {joined}")
    if problem_statement.extracted_keywords:
        joined = ", ".join(problem_statement.extracted_keywords)
        lines.append(f"Extracted keywords: {joined}")
    return "\n".join(lines)


def _sorted_commits(candidate_set: CandidateSet) -> list[CandidateCommit]:
    return sorted(candidate_set.commits, key=lambda commit: commit.rank)


def _select_commits(
    stage: Stage,
    candidate_set: CandidateSet,
    investigation_state: InvestigationState | None,
    config: PromptConfig,
    line_cap: int | None,
) -> list[CandidateCommit]:
    commits = _sorted_commits(candidate_set)
    total = len(commits)
    if total == 0:
        return []

    examined_count = 0
    if investigation_state is not None:
        examined_count = min(investigation_state.candidates_examined, total)

    if stage == "planning":
        limit = line_cap if line_cap is not None else config.candidate_limit_stage2
        selected = commits[:limit]
    elif stage == "examination":
        examined = commits[:examined_count]
        unexamined_limit = config.candidate_limit_stage3_unexamined
        unexamined = commits[examined_count : examined_count + unexamined_limit]
        selected = examined + unexamined
        if line_cap is not None:
            selected = selected[:line_cap]
    else:
        selected = commits[:examined_count]
        if line_cap is not None:
            selected = selected[:line_cap]

    return selected


def _format_candidate_summary(
    stage: Stage,
    candidate_set: CandidateSet,
    investigation_state: InvestigationState | None,
    config: PromptConfig,
    line_cap: int | None,
) -> str | None:
    total = len(candidate_set.commits)
    if total == 0:
        return None

    selected = _select_commits(stage, candidate_set, investigation_state, config, line_cap)
    if not selected:
        return None

    shown = len(selected)
    header = "## Candidate Summary"
    if shown < total:
        header = f"## Candidate Summary (top {shown} of {total})"
    lines = [
        f'{c.rank}. {c.commit_id[:12]} — "{c.summary}" — {", ".join(c.files_changed)}'
        for c in selected
    ]
    return f"{header}\n" + "\n".join(lines)


def _format_skills(skills: list[tuple[GovernanceSkill, int]]) -> str | None:
    if not skills:
        return None
    blocks = [f"### {skill.id}\n{skill.body}" for skill, _score in skills]
    return "\n\n".join(blocks)


def _format_investigation_progress(state: InvestigationState) -> str:
    return (
        f"Stage: {state.current_stage}, Examined: {state.candidates_examined}/"
        f"{state.candidates_total}, Hypotheses tested: {state.hypotheses_tested}, "
        f"confirmed: {state.hypotheses_confirmed}, "
        f"Evidence quotes: {state.evidence_quotes_collected}"
    )
