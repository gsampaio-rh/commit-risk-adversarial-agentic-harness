"""Tests for V4 governance loaders — rules and skills."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from commit_investigator.governance import (
    GovernanceRule,
    GovernanceSkill,
    HardRuleRegistry,
    RuleViolation,
    load_rules,
    retrieve_skills,
)
from commit_investigator.governance.rules import create_default_registry

SEED_RULES_DIR = Path(__file__).resolve().parents[1] / "data" / "governance" / "rules"
SEED_SKILLS_DIR = Path(__file__).resolve().parents[1] / "data" / "governance" / "skills"


@dataclass
class FakeProblemStatement:
    """Minimal stub matching ProblemStatement's relevant attributes."""

    project: str = ""
    extracted_keywords: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AC1: GovernanceRule dataclass
# ---------------------------------------------------------------------------


class TestGovernanceRule:
    def test_construct_from_dict(self) -> None:
        data = {
            "id": "test_rule",
            "enforcement": "hard",
            "stage": "attribution",
            "description": "A test rule",
            "check": "some_check",
            "prompt_text": "Do this thing",
        }
        rule = GovernanceRule.from_dict(data)
        assert rule.id == "test_rule"
        assert rule.enforcement == "hard"
        assert rule.stage == "attribution"
        assert rule.description == "A test rule"
        assert rule.check == "some_check"
        assert rule.prompt_text == "Do this thing"

    def test_null_check_parsed_as_none(self) -> None:
        data = {
            "id": "r",
            "enforcement": "soft",
            "stage": "all",
            "description": "d",
            "check": "null",
        }
        rule = GovernanceRule.from_dict(data)
        assert rule.check is None

    def test_invalid_enforcement_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid enforcement"):
            GovernanceRule(id="x", enforcement="invalid", stage="all", description="d")

    def test_invalid_stage_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid stage"):
            GovernanceRule(id="x", enforcement="hard", stage="invalid", description="d")


# ---------------------------------------------------------------------------
# AC2, AC3: load_rules — stage filtering and hard/soft split
# ---------------------------------------------------------------------------


class TestLoadRules:
    def test_load_attribution_rules_from_seed(self) -> None:
        hard, soft = load_rules("attribution", rules_dir=SEED_RULES_DIR)
        hard_ids = {r.id for r in hard}
        assert "min_suspects" in hard_ids
        assert len(soft) == 0

    def test_load_examination_rules_from_seed(self) -> None:
        hard, soft = load_rules("examination", rules_dir=SEED_RULES_DIR)
        hard_ids = {r.id for r in hard}
        soft_ids = {r.id for r in soft}
        assert "continue_below_confidence" in hard_ids
        assert "parent_chain_examine" in soft_ids

    def test_hard_soft_split_correct(self) -> None:
        hard, soft = load_rules("examination", rules_dir=SEED_RULES_DIR)
        for r in hard:
            assert r.enforcement == "hard"
        for r in soft:
            assert r.enforcement == "soft"

    def test_planning_stage_returns_empty(self) -> None:
        hard, soft = load_rules("planning", rules_dir=SEED_RULES_DIR)
        assert hard == []
        assert soft == []

    # --- E1: non-existent stage ---
    def test_nonexistent_stage_returns_empty(self) -> None:
        hard, soft = load_rules("nonexistent", rules_dir=SEED_RULES_DIR)
        assert hard == []
        assert soft == []

    # --- E4: empty directory ---
    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        hard, soft = load_rules("attribution", rules_dir=tmp_path)
        assert hard == []
        assert soft == []

    def test_nonexistent_directory_returns_empty(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        hard, soft = load_rules("attribution", rules_dir=missing)
        assert hard == []
        assert soft == []

    def test_stage_all_matches_any_stage(self, tmp_path: Path) -> None:
        rule_file = tmp_path / "global.yaml"
        rule_file.write_text(
            "id: global_rule\nenforcement: soft\nstage: all\n"
            "description: applies everywhere\ncheck: null\nprompt_text: do it\n",
            encoding="utf-8",
        )
        for stage in ("planning", "examination", "attribution"):
            hard, soft = load_rules(stage, rules_dir=tmp_path)
            assert len(soft) == 1, f"stage={stage} should match stage=all"
            assert soft[0].id == "global_rule"

    # --- E3: malformed YAML skipped ---
    def test_malformed_yaml_skipped(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("not: valid: yaml: {{{{", encoding="utf-8")
        good_file = tmp_path / "good.yaml"
        good_file.write_text(
            "id: ok\nenforcement: hard\nstage: all\ndescription: fine\n",
            encoding="utf-8",
        )
        hard, soft = load_rules("all", rules_dir=tmp_path)
        assert len(hard) == 1
        assert hard[0].id == "ok"

    def test_malformed_yaml_logs_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("- list\n- not\n- a dict", encoding="utf-8")
        import logging

        with caplog.at_level(logging.WARNING):
            load_rules("all", rules_dir=tmp_path)
        assert any("Skipping" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# AC4: GovernanceSkill dataclass
# ---------------------------------------------------------------------------


class TestGovernanceSkill:
    def test_parse_from_markdown(self) -> None:
        text = (
            "---\n"
            "id: test_skill\n"
            "scope: project\n"
            "project: SPARK\n"
            "triggers: [a, b]\n"
            "source: manual\n"
            "trace_ref: \"\"\n"
            "---\n\n"
            "# Title\n\nBody text here."
        )
        skill = GovernanceSkill.from_markdown(text)
        assert skill.id == "test_skill"
        assert skill.scope == "project"
        assert skill.project == "SPARK"
        assert skill.triggers == ["a", "b"]
        assert skill.source == "manual"
        assert skill.trace_ref == ""
        assert "Body text here." in skill.body

    def test_empty_project_becomes_none_for_general(self) -> None:
        text = (
            "---\nid: s\nscope: general\nproject: \"\"\n"
            "triggers: [x]\nsource: manual\ntrace_ref: \"\"\n---\n\nBody"
        )
        skill = GovernanceSkill.from_markdown(text)
        assert skill.project is None

    def test_scope_project_with_empty_project_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty project"):
            GovernanceSkill(
                id="bad", scope="project", project=None,
                triggers=["x"], source="manual", trace_ref="", body="b",
            )

    def test_scope_project_with_empty_string_project_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty project"):
            GovernanceSkill(
                id="bad", scope="project", project="",
                triggers=["x"], source="manual", trace_ref="", body="b",
            )

    def test_missing_frontmatter_raises(self) -> None:
        with pytest.raises(ValueError, match="frontmatter"):
            GovernanceSkill.from_markdown("No frontmatter here.")

    def test_parse_seed_spark_skill(self) -> None:
        path = SEED_SKILLS_DIR / "spark_serde_blame.md"
        skill = GovernanceSkill.from_markdown(path.read_text(encoding="utf-8"))
        assert skill.id == "spark_serde_blame"
        assert skill.scope == "project"
        assert skill.project == "SPARK"
        assert "serialization" in skill.triggers

    def test_parse_seed_npe_skill(self) -> None:
        path = SEED_SKILLS_DIR / "npe_null_check_removal.md"
        skill = GovernanceSkill.from_markdown(path.read_text(encoding="utf-8"))
        assert skill.id == "npe_null_check_removal"
        assert skill.scope == "general"
        assert skill.project is None
        assert "NullPointerException" in skill.triggers


# ---------------------------------------------------------------------------
# AC5, AC6, AC7: retrieve_skills — scoring, scope, tie-breaking
# ---------------------------------------------------------------------------


class TestRetrieveSkills:
    def test_keyword_overlap_scoring_with_score(self) -> None:
        ps = FakeProblemStatement(project="SPARK", extracted_keywords=["serialization", "kryo"])
        results = retrieve_skills(ps, skills_dir=SEED_SKILLS_DIR)
        assert len(results) >= 1
        skill, score = results[0]
        assert skill.id == "spark_serde_blame"
        assert score == 2

    def test_project_scope_filters_out_mismatch(self) -> None:
        ps = FakeProblemStatement(
            project="GROOVY", extracted_keywords=["serialization", "kryo"]
        )
        results = retrieve_skills(ps, skills_dir=SEED_SKILLS_DIR)
        ids = {s.id for s, _ in results}
        assert "spark_serde_blame" not in ids

    def test_general_scope_matches_any_project(self) -> None:
        ps = FakeProblemStatement(
            project="ANYTHING", extracted_keywords=["NullPointerException"]
        )
        results = retrieve_skills(ps, skills_dir=SEED_SKILLS_DIR)
        assert len(results) >= 1
        skill, score = results[0]
        assert skill.id == "npe_null_check_removal"
        assert score == 1

    def test_project_scoped_preferred_on_tie(self, tmp_path: Path) -> None:
        general_skill = (
            "---\nid: general_skill\nscope: general\nproject: \"\"\n"
            "triggers: [bug]\nsource: manual\ntrace_ref: \"\"\n---\n\nGeneral."
        )
        project_skill = (
            "---\nid: project_skill\nscope: project\nproject: MYPROJ\n"
            "triggers: [bug]\nsource: manual\ntrace_ref: \"\"\n---\n\nProject."
        )
        (tmp_path / "general.md").write_text(general_skill, encoding="utf-8")
        (tmp_path / "project.md").write_text(project_skill, encoding="utf-8")

        ps = FakeProblemStatement(project="MYPROJ", extracted_keywords=["bug"])
        results = retrieve_skills(ps, skills_dir=tmp_path)
        assert len(results) == 2
        assert results[0][0].id == "project_skill"
        assert results[1][0].id == "general_skill"
        assert results[0][1] == results[1][1] == 1

    def test_top_k_limits_results(self, tmp_path: Path) -> None:
        for i in range(5):
            text = (
                f"---\nid: skill_{i}\nscope: general\nproject: \"\"\n"
                f"triggers: [common]\nsource: manual\ntrace_ref: \"\"\n---\n\nBody {i}."
            )
            (tmp_path / f"skill_{i}.md").write_text(text, encoding="utf-8")

        ps = FakeProblemStatement(extracted_keywords=["common"])
        results = retrieve_skills(ps, top_k=3, skills_dir=tmp_path)
        assert len(results) == 3

    # --- E2: no keyword overlap ---
    def test_no_overlap_returns_empty(self) -> None:
        ps = FakeProblemStatement(
            project="SPARK", extracted_keywords=["unrelated_keyword"]
        )
        results = retrieve_skills(ps, skills_dir=SEED_SKILLS_DIR)
        assert results == []

    def test_empty_keywords_returns_empty(self) -> None:
        ps = FakeProblemStatement(project="SPARK", extracted_keywords=[])
        results = retrieve_skills(ps, skills_dir=SEED_SKILLS_DIR)
        assert results == []

    # --- E4: empty skills directory ---
    def test_empty_skills_dir_returns_empty(self, tmp_path: Path) -> None:
        ps = FakeProblemStatement(extracted_keywords=["anything"])
        results = retrieve_skills(ps, skills_dir=tmp_path)
        assert results == []

    def test_drafts_subdirectory_excluded(self, tmp_path: Path) -> None:
        """Skills in drafts/ subdirectory are not loaded by retrieve_skills."""
        drafts = tmp_path / "drafts"
        drafts.mkdir()
        draft_skill = (
            "---\nid: draft_skill\nscope: general\nproject: \"\"\n"
            "triggers: [match]\nsource: trace-derived\ntrace_ref: X-1\n---\n\nDraft."
        )
        (drafts / "draft.md").write_text(draft_skill, encoding="utf-8")
        ps = FakeProblemStatement(extracted_keywords=["match"])
        results = retrieve_skills(ps, skills_dir=tmp_path)
        assert results == []

    def test_malformed_skill_skipped(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        bad_skill = tmp_path / "bad.md"
        bad_skill.write_text("No frontmatter here at all.", encoding="utf-8")
        good_skill = (
            "---\nid: good\nscope: general\nproject: \"\"\n"
            "triggers: [hit]\nsource: manual\ntrace_ref: \"\"\n---\n\nBody."
        )
        (tmp_path / "good.md").write_text(good_skill, encoding="utf-8")
        import logging

        with caplog.at_level(logging.WARNING):
            ps = FakeProblemStatement(extracted_keywords=["hit"])
            results = retrieve_skills(ps, skills_dir=tmp_path)
        assert len(results) == 1
        assert results[0][0].id == "good"
        assert any("Skipping" in record.message for record in caplog.records)

    def test_case_insensitive_keyword_match(self) -> None:
        ps = FakeProblemStatement(
            project="SPARK", extracted_keywords=["SERIALIZATION", "KRYO"]
        )
        results = retrieve_skills(ps, skills_dir=SEED_SKILLS_DIR)
        assert len(results) >= 1
        assert results[0][0].id == "spark_serde_blame"

    def test_case_insensitive_project_match(self) -> None:
        ps = FakeProblemStatement(
            project="spark", extracted_keywords=["serialization"]
        )
        results = retrieve_skills(ps, skills_dir=SEED_SKILLS_DIR)
        assert len(results) >= 1
        assert results[0][0].id == "spark_serde_blame"


# ---------------------------------------------------------------------------
# AC9: HardRuleRegistry
# ---------------------------------------------------------------------------


class TestHardRuleRegistry:
    def test_register_and_check_violation(self) -> None:
        registry = HardRuleRegistry()
        registry.register(
            "test_rule",
            "attribution",
            lambda ctx: RuleViolation("test_rule", "failed") if ctx.get("fail") else None,
        )
        violations = registry.check_all("attribution", {"fail": True})
        assert len(violations) == 1
        assert violations[0].rule_id == "test_rule"

    def test_check_all_passes_when_satisfied(self) -> None:
        registry = HardRuleRegistry()
        registry.register(
            "test_rule",
            "attribution",
            lambda ctx: None,
        )
        violations = registry.check_all("attribution", {})
        assert violations == []

    def test_check_all_filters_by_stage(self) -> None:
        registry = HardRuleRegistry()
        registry.register(
            "attr_rule",
            "attribution",
            lambda ctx: RuleViolation("attr_rule", "fail"),
        )
        violations = registry.check_all("examination", {})
        assert violations == []

    def test_default_registry_min_suspects(self) -> None:
        registry = create_default_registry()
        violations = registry.check_all(
            "attribution", {"suspects": ["s1"], "candidate_set": list(range(10))}
        )
        assert len(violations) == 1
        assert violations[0].rule_id == "min_suspects"
        assert violations[0].context  # AC4: context dict populated

    def test_default_registry_min_suspects_passes(self) -> None:
        registry = create_default_registry()
        violations = registry.check_all(
            "attribution", {"suspects": ["s1", "s2", "s3"], "candidate_set": list(range(10))}
        )
        assert violations == []

    def test_default_registry_min_suspects_relaxed_for_small_candidate_set(self) -> None:
        registry = create_default_registry()
        violations = registry.check_all(
            "attribution", {"suspects": ["s1"], "candidate_set": ["c1", "c2"]}
        )
        assert violations == []

    def test_default_registry_continue_below_confidence(self) -> None:
        registry = create_default_registry()
        violations = registry.check_all(
            "examination", {"top_confidence": 0.3, "confidence_gate": 0.60}
        )
        assert len(violations) == 1
        assert violations[0].rule_id == "continue_below_confidence"

    def test_default_registry_continue_below_confidence_passes(self) -> None:
        registry = create_default_registry()
        violations = registry.check_all(
            "examination", {"top_confidence": 0.70, "confidence_gate": 0.60}
        )
        assert violations == []

    def test_default_registry_budget_overrides_confidence(self) -> None:
        registry = create_default_registry()
        violations = registry.check_all(
            "examination",
            {"top_confidence": 0.3, "confidence_gate": 0.60, "budget_hard_stop": True},
        )
        assert violations == []


# ---------------------------------------------------------------------------
# AC10: import test
# ---------------------------------------------------------------------------


class TestImports:
    def test_all_exports_importable(self) -> None:
        from commit_investigator.governance import (  # noqa: F401
            GovernanceRule,
            GovernanceSkill,
            HardRuleRegistry,
            RuleViolation,
            load_rules,
            retrieve_skills,
        )


# ---------------------------------------------------------------------------
# AC11: zero V3 imports
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_no_v3_imports(self) -> None:
        """Governance package must not import from agent, eval, or infra."""
        gov_dir = Path(__file__).resolve().parents[1] / "src" / "commit_investigator" / "governance"
        forbidden = {"commit_investigator.agent", "commit_investigator.eval", "commit_investigator.infra"}
        for py_file in gov_dir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for pkg in forbidden:
                assert pkg not in content, f"{py_file.name} imports {pkg}"
