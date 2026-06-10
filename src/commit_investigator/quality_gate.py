"""Quality gate: deterministic follow-up evaluation for investigations.

iter-3a stub: delegates to the LLM's own follow_up_needed field.
iter-3b will replace this with script-driven deterministic triggers based on
the evidence_tagger output (supported_count == 0 AND production_defect_signals,
localization == [] AND diff_truncated AND archetype == AMBIGUOUS, etc.).

The stub preserves the existing max_turns=1 frozen behavior — it is a pure
passthrough of the parsed LLM signal with no additional logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GateResult:
    """Result of quality gate evaluation.

    follow_up_needed: whether an additional investigation turn is warranted.
    signals: list of trigger reasons (empty in iter-3a stub; populated in iter-3b).
    reason: human-readable explanation (empty in iter-3a stub).
    """

    follow_up_needed: bool
    signals: list[str] = field(default_factory=list)
    reason: str = ""


def evaluate_gate(*, follow_up_needed: bool) -> GateResult:
    """Evaluate whether a follow-up investigation turn is needed.

    iter-3a stub: pure passthrough of the LLM-reported follow_up_needed flag.
    Behavior is identical to the previous inline JSON parse in _should_follow_up().
    """
    return GateResult(follow_up_needed=follow_up_needed, signals=[], reason="")
