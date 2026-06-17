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

_NO_TOOLS_NOTICE = (
    "IMPORTANT: Do NOT use any tools (file read, grep, shell). "
    "Reason ONLY from the information provided below. "
    "Respond with the requested output format only — no file edits, no commands."
)

_SYSTEM_ROLES: dict[str, str] = {
    "planning": (
        "You are a bug attribution investigator in the Planning stage. "
        "Produce a structured InvestigationBrief with falsifiable hypotheses "
        "and an examination plan.\n\n" + _NO_TOOLS_NOTICE
    ),
    "examination": (
        "You are a bug attribution investigator in the Examination stage. "
        "Your goal is to identify WHICH specific commit from the candidate list "
        "introduced the bug described in the problem statement. Examine commits by "
        "analyzing their changes (files modified, code semantics) relative to the bug."
        "\n\n" + _NO_TOOLS_NOTICE
    ),
    "attribution": (
        "You are a bug attribution investigator in the Attribution stage. "
        "Based on evidence collected, rank commits by probability of having "
        "INTRODUCED the bug. Focus on commits that CHANGED the specific code "
        "or behavior described in the bug report.\n\n" + _NO_TOOLS_NOTICE
    ),
}

_STAGE_INSTRUCTIONS: dict[str, str] = {
    "planning": (
        "Analyze the bug report and candidate commits below. Your task is to identify "
        "which commit INTRODUCED the bug. Output JSON matching InvestigationBrief schema: "
        "hypotheses, examination_plan, success_criteria, strategy, max_effort.\n\n"
        "Good hypotheses for bug attribution:\n"
        "- 'Commit X introduced the bug because it modified [file/function] in a way "
        "that could cause [symptom]' — confirm by checking the diff semantics\n"
        "- 'The bug was introduced by a change to [component] between dates D1-D2' "
        "— confirm by examining commits in that range\n\n"
        "Each hypothesis must state what would CONFIRM it (evidence the commit's changes "
        "match the bug) and what would FALSIFY it (the commit doesn't touch relevant code)."
    ),
    "examination": (
        "Examine the listed candidate commits. For each one you analyze:\n"
        "- Check if its changed files/code overlap with the bug's affected area\n"
        "- Check if the change semantics could INTRODUCE the described bug\n"
        "- Note the commit date — the bug-introducing commit must predate the fix\n"
        "- State your confidence (0-1) that THIS commit introduced the bug\n\n"
        "Focus on commits with high retrieval signals (blame, file_log, pickaxe). "
        "A commit that modified the exact file/function mentioned in the bug report "
        "and changed relevant logic is a strong suspect."
    ),
    "attribution": (
        "Based on ALL evidence collected during examination, produce your final ranking.\n\n"
        "Output ONLY a JSON object with a 'suspects' array. Each element must have:\n"
        "- commit_id: full 40-char SHA from the candidate list\n"
        "- confidence: float 0.0 to 1.0\n"
        "- rationale: WHY this commit likely introduced the bug (cite evidence)\n\n"
        "Rank at least 3 suspects. The top suspect should be the commit whose changes "
        "most directly could have caused the described bug behavior.\n\n"
        "Example output:\n"
        "{\"suspects\": [{\"commit_id\": \"abc123def456...\", \"confidence\": 0.85, "
        "\"rationale\": \"Modified CqlPagingRecordReader.java with logic change that...\"}]}\n\n"
        "IMPORTANT: Output raw JSON only — no markdown fences, no explanation outside the JSON."
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
        limit = line_cap if line_cap is not None else max(examined_count, config.candidate_limit_stage2)
        selected = commits[:limit]

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
    if stage == "attribution":
        lines = []
        for c in selected:
            line = (
                f'{c.rank}. {c.commit_id} — "{c.summary}" '
                f'[signal: {c.retrieval_signal}] [{c.date}] '
                f'files: {", ".join(c.files_changed[:5])}'
            )
            if c.diff_summary:
                diff_lines = c.diff_summary[:300]
                line += f"\n   DIFF: {diff_lines}"
            lines.append(line)
    else:
        lines = []
        for c in selected:
            date_suffix = f" ({c.date})" if c.date else ""
            line = (
                f'{c.rank}. {c.commit_id[:12]} — "{c.summary}"{date_suffix} — '
                f'{", ".join(c.files_changed)}'
            )
            if c.diff_summary and stage == "examination":
                diff_lines = c.diff_summary[:300]
                line += f"\n   DIFF: {diff_lines}"
            lines.append(line)
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
