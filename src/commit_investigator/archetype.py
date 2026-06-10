"""Commit archetype detection: classify commit patterns and production defect signals.

This module is script-only — no LLM calls. It classifies commits into known
clean-commit archetypes (version bumps, label renames, type migrations) and
detects production defect signals (guard removal, lifecycle changes, concurrency).

The risk_policy module consumes these to determine whether a clean-commit cap applies.
"""

from __future__ import annotations

import re

from commit_investigator.context_builder import InvestigationContext

# ---------------------------------------------------------------------------
# Regex patterns — archetype detection
# ---------------------------------------------------------------------------

_VERSION_DIFF_RE = re.compile(
    r"^[-+].*(?:<version>|version\s*=)", re.MULTILINE | re.IGNORECASE
)
_IMPORT_CHANGE_RE = re.compile(r"^[-+]\s*import\s+", re.MULTILINE)
_TYPE_MIGRATION_RE = re.compile(
    r"NotifyingFuture|CompletableFuture|QueryFactory|raw type",
    re.IGNORECASE,
)
_LABEL_RENAME_RE = re.compile(
    r'^[-+].*"(?:FileName|LogType|logType|logAggregationType)"',
    re.MULTILINE,
)
_COMPAT_COMMENT_RE = re.compile(
    r"^-.*(?:incompatible|compatibility|breaking threshold|binary incompatible)",
    re.MULTILINE | re.IGNORECASE,
)
_VERSION_PROPERTY_RE = re.compile(
    r"^[-+].*(?:-version>|<[\w-]*version>)",
    re.MULTILINE | re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Regex patterns — production defect signals
# ---------------------------------------------------------------------------

_LIFECYCLE_RE = re.compile(
    r"SmartLifecycle|@Order|shutdown|startup|@EventListener|lifecycle",
    re.IGNORECASE,
)
_GUARD_REMOVAL_RE = re.compile(
    r"^-\s+.*(?:if\s*\(|guard|null\s*==|!=\s*null|nullcheck)",
    re.MULTILINE | re.IGNORECASE,
)
_CONCURRENCY_CHANGE_RE = re.compile(
    r"^[-+].*(?:synchronized|ReentrantLock|\bLock\.|volatile\s+\w+)",
    re.MULTILINE | re.IGNORECASE,
)


def has_production_defect_signals(context: InvestigationContext) -> bool:
    """True when commit shows material production defect patterns.

    Detects guard removal, lifecycle ordering changes, and concurrency
    modifications — signals that opt-out from the clean-commit risk cap.
    Routine return-statement edits and JIRA-ticket references do NOT trigger.
    """
    diff = context.diff or ""

    if _GUARD_REMOVAL_RE.search(diff):
        return True

    if _LIFECYCLE_RE.search(diff) and re.search(r"^[-+]", diff, re.MULTILINE):
        return True

    return _CONCURRENCY_CHANGE_RE.search(diff) is not None


def detect_archetype(context: InvestigationContext) -> bool:
    """True when commit matches a known clean-commit false-positive archetype.

    Archetypes: version bumps, compat-comment removals, label renames, pure
    type/import migrations, and API method renames without logic change.

    NOTE: This function checks commit patterns only. It does NOT check for
    production defect signals — that gate lives in risk_policy.evaluate_risk(),
    which calls has_production_defect_signals() before consulting this function.
    """
    diff = context.diff or ""
    touched = " ".join(context.touched_files or [])

    version_touched = any(name in touched for name in ("pom.xml", "build.gradle", ".gradle"))
    if version_touched and (
        _VERSION_DIFF_RE.search(diff)
        or _VERSION_PROPERTY_RE.search(diff)
        or _COMPAT_COMMENT_RE.search(diff)
    ):
        return True

    if _LABEL_RENAME_RE.search(diff):
        return True

    import_changes = len(_IMPORT_CHANGE_RE.findall(diff))
    if import_changes >= 2:
        return True

    if _TYPE_MIGRATION_RE.search(diff) and (import_changes >= 1 or version_touched):
        return True

    minus_methods = len(re.findall(r"^-\s*(?:public|protected)[^\n]*\(", diff, re.MULTILINE))
    plus_methods = len(re.findall(r"^\+\s*(?:public|protected)[^\n]*\(", diff, re.MULTILINE))
    if minus_methods >= 1 and plus_methods >= 1 and import_changes <= 1:
        if not has_production_defect_signals(context):
            return True

    return False
