"""YAML-based governance rules: loader, dataclass, and hard-rule check registry."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger(__name__)

VALID_ENFORCEMENTS = frozenset({"hard", "soft"})
VALID_STAGES = frozenset({"planning", "examination", "attribution", "all"})

DEFAULT_RULES_DIR = Path(__file__).resolve().parents[3] / "data" / "governance" / "rules"


@dataclass(frozen=True)
class GovernanceRule:
    """One governance rule parsed from a YAML file."""

    id: str
    enforcement: str
    stage: str
    description: str
    check: str | None = None
    prompt_text: str = ""

    def __post_init__(self) -> None:
        if self.enforcement not in VALID_ENFORCEMENTS:
            msg = f"Invalid enforcement: {self.enforcement!r}"
            raise ValueError(msg)
        if self.stage not in VALID_STAGES:
            msg = f"Invalid stage: {self.stage!r}"
            raise ValueError(msg)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GovernanceRule:
        check_val = data.get("check")
        if check_val == "null" or check_val is None:
            check_val = None
        return cls(
            id=data["id"],
            enforcement=data["enforcement"],
            stage=data["stage"],
            description=data["description"],
            check=check_val,
            prompt_text=data.get("prompt_text", ""),
        )


def load_rules(
    stage: str, *, rules_dir: Path | None = None
) -> tuple[list[GovernanceRule], list[GovernanceRule]]:
    """Load governance rules for a stage and split into (hard, soft).

    Returns rules where rule.stage matches the requested stage or is 'all'.
    Malformed files are skipped with a warning.
    """
    directory = rules_dir if rules_dir is not None else DEFAULT_RULES_DIR
    if not directory.is_dir():
        return [], []

    matched: list[GovernanceRule] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                logger.warning("Skipping non-dict YAML: %s", path)
                continue
            rule = GovernanceRule.from_dict(raw)
        except Exception:
            logger.warning("Skipping malformed rule file: %s", path, exc_info=True)
            continue

        if rule.stage == stage or rule.stage == "all":
            matched.append(rule)

    hard = [r for r in matched if r.enforcement == "hard"]
    soft = [r for r in matched if r.enforcement == "soft"]
    return hard, soft


@dataclass
class RuleViolation:
    """Result of a failed hard-rule check."""

    rule_id: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)


CheckFn = Callable[[dict[str, Any]], RuleViolation | None]


class HardRuleRegistry:
    """Registry of executable hard-rule checks for the investigation harness.

    The harness registers check functions keyed by rule_id. At stage transitions,
    check_all() runs every registered check for the given stage and returns
    any violations that would block the transition.
    """

    def __init__(self) -> None:
        self._checks: dict[str, tuple[str, CheckFn]] = {}

    def register(self, rule_id: str, stage: str, check_fn: CheckFn) -> None:
        self._checks[rule_id] = (stage, check_fn)

    def check_all(self, stage: str, context: dict[str, Any]) -> list[RuleViolation]:
        violations: list[RuleViolation] = []
        for rule_id, (rule_stage, check_fn) in sorted(self._checks.items()):
            if rule_stage != stage and rule_stage != "all":
                continue
            result = check_fn(context)
            if result is not None:
                violations.append(result)
        return violations


def _check_min_suspects(context: dict[str, Any]) -> RuleViolation | None:
    suspects = context.get("suspects", [])
    candidate_set = context.get("candidate_set", [])
    if len(suspects) >= 3 or len(candidate_set) < 3:
        return None
    return RuleViolation(
        rule_id="min_suspects",
        message=f"Need >= 3 suspects, got {len(suspects)} (candidates: {len(candidate_set)})",
        context=dict(context),
    )


def _check_continue_below_confidence(context: dict[str, Any]) -> RuleViolation | None:
    top_confidence = context.get("top_confidence", 0.0)
    confidence_gate = context.get("confidence_gate", 0.60)
    budget_hard_stop = context.get("budget_hard_stop", False)
    if top_confidence >= confidence_gate or budget_hard_stop:
        return None
    return RuleViolation(
        rule_id="continue_below_confidence",
        message=f"Top confidence {top_confidence:.2f} < gate {confidence_gate:.2f}",
        context=dict(context),
    )


def create_default_registry() -> HardRuleRegistry:
    """Build a registry with the seed hard-rule checks."""
    registry = HardRuleRegistry()
    registry.register("min_suspects", "attribution", _check_min_suspects)
    registry.register("continue_below_confidence", "examination", _check_continue_below_confidence)
    return registry
