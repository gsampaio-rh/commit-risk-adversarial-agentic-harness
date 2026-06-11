"""Quality gate: deterministic follow-up evaluation for investigations.

iter-3b: Replaces the iter-3a passthrough stub with 4 canonical deterministic triggers.
The gate is driven entirely by Script-derived signals from the hypothesis artifact —
the LLM's follow_up_needed field is DEPRECATED and ignored.

Triggers (OR — any fires follow_up_needed=True):
  T1: supported_count == 0 AND production_defect_signals
  T2: localization == [] AND diff_truncated AND archetype_is_ambiguous
  T3: HypothesisArtifact Pydantic validation fails (validation_error is not None)
  T4: findings == ['Investigation completed'] (masked empty output)

Note: max_turns=1 is frozen everywhere (state.json). The gate fires correctly
but follow-up turns will not execute until iter-3f unlocks multi-turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any  # noqa: F401  (LocalizationClaim deferred to iter-3e)


# ---------------------------------------------------------------------------
# Trigger signal identifiers (stable constants for test assertions)
# ---------------------------------------------------------------------------

T1_NO_SUPPORTED_WITH_DEFECT = "no_supported_with_defect_signals"
T2_AMBIGUOUS_EMPTY_LOC_TRUNCATED = "ambiguous_empty_localization_truncated"
T3_SCHEMA_VALIDATION_FAILURE = "schema_validation_failure"
T4_MASKED_EMPTY_FINDINGS = "masked_empty_findings"

_MASKED_FINDINGS = ["Investigation completed"]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HypothesisArtifact:
    """Script-derived signals used by the quality gate to decide follow-up.

    Fields are computed deterministically from context and LLM output:
      supported_count: count of hypotheses verified SUPPORTED by evidence_tagger
      production_defect_signals: from archetype.has_production_defect_signals()
      localization: parsed from LLM JSON (may be empty)
      diff_truncated: len(context.diff) > max_diff_chars
      archetype_is_ambiguous: not detect_archetype(context)
      findings: from normalize_findings(parsed["findings"])
      validation_error: Pydantic validation error message or None
    """

    supported_count: int
    production_defect_signals: bool
    localization: list  # list[LocalizationClaim] — avoid runtime import cycle
    diff_truncated: bool
    archetype_is_ambiguous: bool
    findings: list[str]
    validation_error: str | None = None


@dataclass(frozen=True)
class GateResult:
    """Result of quality gate evaluation.

    follow_up_needed: whether an additional investigation turn is warranted.
    signals: list of trigger IDs that fired (T1-T4 constants above).
    reason: human-readable explanation of active triggers.
    """

    follow_up_needed: bool
    signals: list[str] = field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


def evaluate_gate(artifact: HypothesisArtifact) -> GateResult:
    """Evaluate whether a follow-up investigation turn is needed.

    Checks all 4 canonical triggers. Any trigger firing sets follow_up_needed=True.
    The LLM's follow_up_needed field is NOT consulted — this is a pure Script decision.
    """
    signals: list[str] = []

    # T1: supported_count == 0 AND production defect signals present
    if artifact.supported_count == 0 and artifact.production_defect_signals:
        signals.append(T1_NO_SUPPORTED_WITH_DEFECT)

    # T2: empty localization AND diff truncated AND archetype is ambiguous
    # Requires ALL THREE conjuncts — partial conditions must not fire
    if (
        not artifact.localization
        and artifact.diff_truncated
        and artifact.archetype_is_ambiguous
    ):
        signals.append(T2_AMBIGUOUS_EMPTY_LOC_TRUNCATED)

    # T3: Pydantic validation failure on hypothesis artifact
    if artifact.validation_error is not None:
        signals.append(T3_SCHEMA_VALIDATION_FAILURE)

    # T4: Masked empty output — findings == ['Investigation completed'] exactly
    if artifact.findings == _MASKED_FINDINGS:
        signals.append(T4_MASKED_EMPTY_FINDINGS)

    if not signals:
        return GateResult(follow_up_needed=False, signals=[], reason="")

    reason = "; ".join(signals)
    return GateResult(follow_up_needed=True, signals=signals, reason=reason)
