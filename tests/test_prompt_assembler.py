"""Tests for V4 prompt assembler — ADR §Q3 section template and truncation."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from commit_investigator.extraction.problem_extractor import ProblemStatement
from commit_investigator.governance import PromptConfig, assemble_prompt
from commit_investigator.governance.rules import DEFAULT_RULES_DIR, GovernanceRule, load_rules
from commit_investigator.governance.skills import GovernanceSkill
from commit_investigator.models.candidates import CandidateCommit, CandidateSet
from commit_investigator.models.investigation import Hypothesis, InvestigationBrief, InvestigationState

SEED_RULES_DIR = Path(__file__).resolve().parents[1] / "data" / "governance" / "rules"


def _problem(**kwargs: object) -> ProblemStatement:
    defaults = {
        "title": "Test bug",
        "description": "Something broke",
        "project": "TEST",
    }
    defaults.update(kwargs)
    return ProblemStatement(**defaults)  # type: ignore[arg-type]


def _commit(rank: int, **kwargs: object) -> CandidateCommit:
    defaults = {
        "commit_id": f"{'a' * 40}{rank:02d}",
        "rank": rank,
        "retrieval_signal": "test",
        "summary": f"Summary {rank}",
        "files_changed": [f"file{rank}.py"],
    }
    defaults.update(kwargs)
    return CandidateCommit(**defaults)  # type: ignore[arg-type]


def _candidate_set(count: int) -> CandidateSet:
    return CandidateSet(commits=[_commit(i) for i in range(1, count + 1)])


def _headers(text: str) -> list[str]:
    return re.findall(r"^## .+", text, flags=re.MULTILINE)


def _hard_rule(description: str = "Hard rule text") -> GovernanceRule:
    return GovernanceRule(
        id="hard1",
        enforcement="hard",
        stage="examination",
        description=description,
    )


def _soft_rule(prompt_text: str = "Soft guidance here") -> GovernanceRule:
    return GovernanceRule(
        id="soft1",
        enforcement="soft",
        stage="examination",
        description="Soft desc",
        prompt_text=prompt_text,
    )


def _skill(skill_id: str = "skill_a", body: str = "Skill body text") -> GovernanceSkill:
    return GovernanceSkill(
        id=skill_id,
        scope="general",
        project=None,
        triggers=["bug"],
        source="manual",
        trace_ref="",
        body=body,
    )


class TestImportsAndConfig:
    def test_exports_importable(self) -> None:
        from commit_investigator.governance import PromptConfig, assemble_prompt

        assert callable(assemble_prompt)
        assert PromptConfig().token_budget == 100_000

    def test_prompt_config_defaults(self) -> None:
        cfg = PromptConfig()
        assert cfg.truncation_threshold == 0.80
        assert cfg.candidate_limit_stage2 == 20
        assert cfg.candidate_limit_stage3_unexamined == 5
        assert cfg.max_skills == 3

    def test_prompt_config_override(self) -> None:
        cfg = PromptConfig(token_budget=50_000)
        assert cfg.token_budget == 50_000

    def test_prompt_config_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="token_budget"):
            PromptConfig(token_budget=0)


class TestAssemblePromptBasics:
    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_planning_returns_non_empty_sections(self, _load: object, _skills: object) -> None:
        prompt = assemble_prompt("planning", _problem(), _candidate_set(1))
        assert prompt
        assert "## System Role" in prompt
        assert "\n\n" in prompt

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_invalid_stage_raises(self, _load: object, _skills: object) -> None:
        with pytest.raises(ValueError, match="invalid"):
            assemble_prompt("invalid", _problem(), _candidate_set(1))  # type: ignore[arg-type]


class TestSectionOrderAndOmission:
    @patch(
        "commit_investigator.governance.prompt_assembler.retrieve_skills",
        return_value=[(_skill("s1", "Body one"), 2), (_skill("s2", "Body two"), 1)],
    )
    @patch(
        "commit_investigator.governance.prompt_assembler.load_rules",
        return_value=([_hard_rule()], [_soft_rule("Soft prompt block")]),
    )
    def test_all_sections_present_in_order(self, _load: object, _skills: object) -> None:
        state = InvestigationState(
            current_stage=3,
            candidates_examined=2,
            candidates_total=5,
            hypotheses_tested=1,
            hypotheses_confirmed=0,
            evidence_quotes_collected=2,
        )
        brief = InvestigationBrief(hypotheses=[Hypothesis(id="h1", statement="s1")])
        prompt = assemble_prompt(
            "attribution",
            _problem(extracted_files=["a.py"], extracted_keywords=["kw"]),
            _candidate_set(5),
            investigation_state=state,
            brief=brief,
            evidence=["quote one", "quote two"],
        )
        headers = _headers(prompt)
        assert headers == [
            "## System Role",
            "## Hard Rules (Attribution)",
            "## Soft Rules (Attribution)",
            "## Relevant Skills",
            "## Stage Instructions",
            "## Problem Statement",
            "## Candidate Summary",
            "## Investigation Progress",
            "## Investigation Brief",
            "## Evidence Collected",
        ]

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_planning_omits_late_sections(self, _load: object, _skills: object) -> None:
        prompt = assemble_prompt("planning", _problem(), _candidate_set(1))
        assert "## Investigation Progress" not in prompt
        assert "## Investigation Brief" not in prompt
        assert "## Evidence Collected" not in prompt

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_examination_state_none_omits_progress(self, _load: object, _skills: object) -> None:
        prompt = assemble_prompt("examination", _problem(), _candidate_set(1), investigation_state=None)
        assert "## Investigation Progress" not in prompt

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_brief_none_omits_brief_section(self, _load: object, _skills: object) -> None:
        prompt = assemble_prompt(
            "examination",
            _problem(),
            _candidate_set(1),
            investigation_state=InvestigationState(),
            brief=None,
        )
        assert "## Investigation Brief" not in prompt

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_attribution_empty_evidence_omits_section(self, _load: object, _skills: object) -> None:
        prompt = assemble_prompt("attribution", _problem(), _candidate_set(1), evidence=[])
        assert "## Evidence Collected" not in prompt


class TestSystemRoleAndInstructions:
    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_system_role_per_stage(self, _load: object, _skills: object) -> None:
        prompts = {
            stage: assemble_prompt(stage, _problem(), CandidateSet())
            for stage in ("planning", "examination", "attribution")
        }
        assert "Planning stage" in prompts["planning"]
        assert "Examination stage" in prompts["examination"]
        assert "Attribution stage" in prompts["attribution"]
        for text in prompts.values():
            assert "bug attribution investigator" in text.lower()

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_stage_instruction_substrings(self, _load: object, _skills: object) -> None:
        planning = assemble_prompt("planning", _problem(), CandidateSet())
        examination = assemble_prompt("examination", _problem(), CandidateSet())
        attribution = assemble_prompt("attribution", _problem(), CandidateSet())
        assert "InvestigationBrief" in planning and "JSON" in planning
        assert "examine" in examination.lower() and "confidence" in examination.lower()
        assert "suspects" in attribution.lower() and "confidence" in attribution.lower()
        assert planning != examination != attribution


class TestRulesAndSkills:
    @patch(
        "commit_investigator.governance.prompt_assembler.retrieve_skills",
        return_value=[(_skill("alpha", "Alpha body"), 2), (_skill("beta", "Beta body"), 1)],
    )
    @patch(
        "commit_investigator.governance.prompt_assembler.load_rules",
        return_value=([_hard_rule("Must do X")], [_soft_rule("Keep this soft text")]),
    )
    def test_rules_and_skills_rendering(self, _load: object, _skills: object) -> None:
        prompt = assemble_prompt("examination", _problem(), CandidateSet())
        assert "- Must do X" in prompt
        assert "Keep this soft text" in prompt
        assert "### alpha" in prompt
        assert "Alpha body" in prompt
        assert "### beta" in prompt
        assert prompt.index("### alpha") < prompt.index("### beta")

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch(
        "commit_investigator.governance.prompt_assembler.load_rules",
        return_value=([], [_soft_rule()]),
    )
    def test_empty_hard_rules_omits_hard_section(self, _load: object, _skills: object) -> None:
        prompt = assemble_prompt("examination", _problem(), CandidateSet())
        assert "## Hard Rules" not in prompt
        assert "## Soft Rules (Examination)" in prompt


class TestCandidateSummary:
    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_planning_shows_top_20_of_30(self, _load: object, _skills: object) -> None:
        prompt = assemble_prompt("planning", _problem(), _candidate_set(30))
        assert "## Candidate Summary (top 20 of 30)" in prompt
        lines = [line for line in prompt.splitlines() if re.match(r"^\d+\. ", line)]
        assert len(lines) == 20

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_examination_examined_plus_unexamined(self, _load: object, _skills: object) -> None:
        state = InvestigationState(candidates_examined=5)
        prompt = assemble_prompt("examination", _problem(), _candidate_set(15), investigation_state=state)
        lines = [line for line in prompt.splitlines() if re.match(r"^\d+\. ", line)]
        assert len(lines) == 10

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_attribution_shows_all_top_candidates(self, _load: object, _skills: object) -> None:
        state = InvestigationState(candidates_examined=3)
        prompt = assemble_prompt("attribution", _problem(), _candidate_set(10), investigation_state=state)
        lines = [line for line in prompt.splitlines() if re.match(r"^\d+\. [0-9a-f]", line)]
        assert len(lines) == 10

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_empty_candidate_set_omits_summary(self, _load: object, _skills: object) -> None:
        prompt = assemble_prompt("planning", _problem(), CandidateSet(commits=[]))
        assert "## Candidate Summary" not in prompt

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_examined_count_capped_to_available(self, _load: object, _skills: object) -> None:
        state = InvestigationState(candidates_examined=10)
        prompt = assemble_prompt("examination", _problem(), _candidate_set(3), investigation_state=state)
        lines = [line for line in prompt.splitlines() if re.match(r"^\d+\. ", line)]
        assert len(lines) == 3

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_attribution_zero_examined_still_shows_candidates(self, _load: object, _skills: object) -> None:
        state = InvestigationState(candidates_examined=0)
        prompt = assemble_prompt("attribution", _problem(), _candidate_set(5), investigation_state=state)
        assert "## Candidate Summary" in prompt


class TestProblemStatementSection:
    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_problem_statement_format(self, _load: object, _skills: object) -> None:
        prompt = assemble_prompt(
            "planning",
            _problem(
                extracted_files=["a.py", "b.py"],
                extracted_keywords=["npe", "null"],
            ),
            CandidateSet(),
        )
        assert "Title: Test bug" in prompt
        assert "Description: Something broke" in prompt
        assert "Extracted files: a.py, b.py" in prompt
        assert "Extracted keywords: npe, null" in prompt
        assert "## Bug Report" not in prompt

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_empty_problem_omits_section(self, _load: object, _skills: object) -> None:
        prompt = assemble_prompt("planning", _problem(title="", description=""), CandidateSet())
        assert "## Problem Statement" not in prompt


class TestProgressBriefEvidence:
    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_investigation_progress_format(self, _load: object, _skills: object) -> None:
        state = InvestigationState(
            current_stage=3,
            candidates_examined=4,
            candidates_total=10,
            hypotheses_tested=2,
            hypotheses_confirmed=1,
            evidence_quotes_collected=3,
        )
        prompt = assemble_prompt("examination", _problem(), CandidateSet(), investigation_state=state)
        assert (
            "Stage: 3, Examined: 4/10, Hypotheses tested: 2, "
            "confirmed: 1, Evidence quotes: 3"
        ) in prompt

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_brief_json_section(self, _load: object, _skills: object) -> None:
        brief = InvestigationBrief(
            hypotheses=[
                Hypothesis(id="h1", statement="first"),
                Hypothesis(id="h2", statement="second"),
            ]
        )
        prompt = assemble_prompt("examination", _problem(), CandidateSet(), brief=brief)
        assert '"id": "h1"' in prompt
        assert '"statement": "second"' in prompt

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_evidence_numbered_list(self, _load: object, _skills: object) -> None:
        state = InvestigationState(candidates_examined=1)
        prompt = assemble_prompt(
            "attribution",
            _problem(),
            _candidate_set(1),
            investigation_state=state,
            evidence=["quote1", "quote2"],
        )
        assert "1. quote1" in prompt
        assert "2. quote2" in prompt


class TestTruncation:
    @patch(
        "commit_investigator.governance.prompt_assembler.retrieve_skills",
        return_value=[
            (_skill("s1", "x" * 200), 3),
            (_skill("s2", "y" * 200), 2),
            (_skill("s3", "z" * 200), 1),
        ],
    )
    @patch(
        "commit_investigator.governance.prompt_assembler.load_rules",
        return_value=([_hard_rule("protected hard")], [_soft_rule("protected soft")]),
    )
    def test_truncation_cascade(self, _load: object, _skills: object) -> None:
        state = InvestigationState(candidates_examined=30)
        evidence = [f"evidence line {index} " + ("word " * 20) for index in range(20)]
        cfg = PromptConfig(token_budget=400, truncation_threshold=0.80)
        prompt = assemble_prompt(
            "attribution",
            _problem(title="Keep me", description="Also keep"),
            _candidate_set(30),
            investigation_state=state,
            evidence=evidence,
            config=cfg,
        )
        assert "Title: Keep me" in prompt
        assert "protected hard" in prompt
        assert "InvestigationBrief" not in prompt or "Stage Instructions" in prompt
        assert "- protected hard" in prompt
        skill_headers = prompt.count("### ")
        assert skill_headers <= 1
        candidate_lines = [line for line in prompt.splitlines() if re.match(r"^\d+\. ", line)]
        assert len(candidate_lines) <= 10
        assert "## Evidence Collected" not in prompt or "1. evidence" not in prompt

    @patch(
        "commit_investigator.governance.prompt_assembler.retrieve_skills",
        return_value=[(_skill("only", "z" * 500), 1)],
    )
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_truncation_without_evidence(self, _load: object, _skills: object) -> None:
        cfg = PromptConfig(token_budget=200, truncation_threshold=0.80)
        prompt = assemble_prompt(
            "planning",
            _problem(),
            _candidate_set(25),
            evidence=None,
            config=cfg,
        )
        candidate_lines = [line for line in prompt.splitlines() if re.match(r"^\d+\. ", line)]
        assert len(candidate_lines) <= 10


class TestPlanningRulesAndSkillsEdgeCases:
    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_planning_seed_has_no_rule_sections(self, _load: object, _skills: object) -> None:
        hard, soft = load_rules("planning", rules_dir=SEED_RULES_DIR)
        assert hard == [] and soft == []
        prompt = assemble_prompt("planning", _problem(), CandidateSet())
        assert "## Hard Rules" not in prompt
        assert "## Soft Rules" not in prompt

    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    @patch("commit_investigator.governance.prompt_assembler.load_rules", return_value=([], []))
    def test_no_skill_match_omits_skills(self, _load: object, _skills: object) -> None:
        prompt = assemble_prompt(
            "planning",
            _problem(extracted_keywords=["unrelated_keyword_xyz"]),
            CandidateSet(),
        )
        assert "## Relevant Skills" not in prompt


@pytest.mark.skipif(not DEFAULT_RULES_DIR.is_dir(), reason="seed governance rules missing")
class TestSeedIntegration:
    @patch("commit_investigator.governance.prompt_assembler.retrieve_skills", return_value=[])
    def test_examination_uses_seed_rules(self, _skills: object) -> None:
        hard, soft = load_rules("examination", rules_dir=SEED_RULES_DIR)
        continue_rule = next(r for r in hard if r.id == "continue_below_confidence")
        parent_rule = next(r for r in soft if r.id == "parent_chain_examine")

        prompt = assemble_prompt("examination", _problem(), _candidate_set(1))
        assert f"## Hard Rules (Examination)" in prompt
        assert f"- {continue_rule.description}" in prompt
        assert "## Soft Rules (Examination)" in prompt
        assert parent_rule.prompt_text.strip()[:40] in prompt
