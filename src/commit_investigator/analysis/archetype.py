"""Commit archetype detection: classify commit patterns and production defect signals.

This module is script-only — no LLM calls. It classifies commits into known
clean-commit archetypes (version bumps, label renames, type migrations) and
detects production defect signals (guard removal, lifecycle changes, concurrency).

The risk_policy module consumes these to determine whether a clean-commit cap applies.
"""

from __future__ import annotations

import re

from commit_investigator.context.context_builder import InvestigationContext

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
_ITERATOR_SAFETY_RE = re.compile(
    r"^\+.*(?:\.iterator\(\)|itr\.remove\(\)|iterator\.remove\(\)|Iterator<)",
    re.MULTILINE,
)
_VERSION_PROPERTY_RE = re.compile(
    r"^[-+].*(?:-version>|<[\w-]*version>)",
    re.MULTILINE | re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Regex patterns — production defect signals
# ---------------------------------------------------------------------------

_LIFECYCLE_RE = re.compile(
    # @Order alone is excluded: it's a routine Spring bean ordering annotation
    # that does NOT indicate a lifecycle defect risk. LifecyclePhase, SmartLifecycle,
    # shutdown/startup hooks, and @EventListener are materially higher-risk.
    r"SmartLifecycle|LifecyclePhase|shutdown|startup|@EventListener|\blifecycle\b",
    re.IGNORECASE,
)
_GUARD_REMOVAL_RE = re.compile(
    r"^-\s+.*(?:"
    r"guard\b|nullcheck|checkNotNull|requireNonNull|Assert\.notNull"
    r"|null\s*==|==\s*null|!=\s*null|null\s*!="
    r")",
    re.MULTILINE | re.IGNORECASE,
)
_GUARD_ADDITION_RE = re.compile(
    r"^\+\s+.*(?:"
    r"guard\b|nullcheck|checkNotNull|requireNonNull|Assert\.notNull"
    r"|null\s*==|==\s*null|!=\s*null|null\s*!="
    r")",
    re.MULTILINE | re.IGNORECASE,
)
_TEST_FILE_RE = re.compile(
    r"/test/|Test\.(?:java|kt|scala)$|Tests\.(?:java|kt)$|TestCase\.java$|Spec\.java$",
    re.IGNORECASE,
)
_EXAMPLE_PATH_RE = re.compile(r"^examples/|/examples/|/example/", re.IGNORECASE)

# Minimum fraction of removed guards that must be replaced to classify as refactoring.
# Below this ratio, the removal is treated as a genuine net guard removal (defect signal).
_GUARD_REPLACEMENT_RATIO = 0.75

_CONCURRENCY_CHANGE_RE = re.compile(
    r"^[-+].*(?:synchronized|ReentrantLock|\bLock\.|volatile\s+\w+)",
    re.MULTILINE | re.IGNORECASE,
)
_THROW_OR_LOG_RE = re.compile(
    r"(?:throw\s+new\s+\w*(?:Exception|Error)|\.(?:info|warn|error|debug|trace)\s*\()",
    re.IGNORECASE,
)
_CONTROL_FLOW_CHANGE_RE = re.compile(
    r"^[-+]\s*(?:"
    r"(?:public|private|protected|static)\s+"
    r"|(?:if|for|while|switch|synchronized)\s*\("
    r"|(?:import|package|class|interface|enum)\b"
    r")",
    re.MULTILINE | re.IGNORECASE,
)
_STRING_FRAGMENT_LINE_RE = re.compile(
    r"^[-+]\s*(?:.*[\"'].*|.*\+\s*[\"']|.*throw\s+new\s+\w*(?:Exception|Error).*)",
    re.IGNORECASE,
)


def _is_test_only_commit(context: InvestigationContext) -> bool:
    """True when all changed files are in test source paths or example directories.

    Test code and example modules don't carry production defect risk — optimizing
    test performance, refactoring test helpers, or adding example/demo code should
    not be flagged as HIGH risk regardless of code patterns.
    """
    files = context.touched_files or []
    if not files:
        return False
    return all(
        _TEST_FILE_RE.search(f) or _EXAMPLE_PATH_RE.search(f)
        for f in files
    )


def _is_net_guard_removal(diff: str) -> bool:
    """True only when null-check guards are removed WITHOUT equivalent replacements.

    Refactoring that reorganizes null checks (equal remove/add counts) is not a
    genuine guard removal — the protection is preserved. A replacement ratio ≥ 0.75
    indicates refactoring; below that threshold we treat it as a net guard removal.
    """
    removed = len(_GUARD_REMOVAL_RE.findall(diff))
    if removed == 0:
        return False
    added = len(_GUARD_ADDITION_RE.findall(diff))
    return added < removed * _GUARD_REPLACEMENT_RATIO


def has_production_defect_signals(context: InvestigationContext) -> bool:
    """True when commit shows material production defect patterns.

    Detects guard removal, lifecycle ordering changes, and concurrency
    modifications — signals that opt-out from the clean-commit risk cap.
    Routine return-statement edits and JIRA-ticket references do NOT trigger.

    Uses raw_diff when available so assembled/truncated diffs don't suppress
    signal detection.
    """
    if _is_test_only_commit(context):
        return False

    diff = context.raw_diff or context.diff or ""

    if _is_net_guard_removal(diff):
        return True

    if _LIFECYCLE_RE.search(diff) and re.search(r"^[-+]", diff, re.MULTILINE):
        return True

    return _CONCURRENCY_CHANGE_RE.search(diff) is not None


def is_message_only_diff(diff: str) -> bool:
    """True when diff only changes exception/log message string literals.

    Used to cap false HIGH on clean commits where coverage forces hypotheses
    on cosmetic message edits (e.g. e0bb867c3fa6 ApplicationNotFoundException).
    """
    if not diff or not _THROW_OR_LOG_RE.search(diff):
        return False

    changed = [
        ln for ln in diff.splitlines()
        if (ln.startswith("+") or ln.startswith("-"))
        and not ln.startswith("+++")
        and not ln.startswith("---")
    ]
    if not changed:
        return False

    if (
        _CONTROL_FLOW_CHANGE_RE.search(diff)
        or _GUARD_REMOVAL_RE.search(diff)
        or _CONCURRENCY_CHANGE_RE.search(diff)
    ):
        return False

    if _LIFECYCLE_RE.search(diff) and re.search(r"^[-+]", diff, re.MULTILINE):
        return False

    for ln in changed:
        if not ln[1:].strip():
            continue
        if not _STRING_FRAGMENT_LINE_RE.match(ln):
            return False

    return True


def detect_archetype(context: InvestigationContext) -> bool:
    """True when commit matches a known clean-commit false-positive archetype.

    Archetypes: version bumps, compat-comment removals, label renames, pure
    type/import migrations, and API method renames without logic change.

    NOTE: This function checks commit patterns only. It does NOT check for
    production defect signals — that gate lives in risk_policy.evaluate_risk(),
    which calls has_production_defect_signals() before consulting this function.

    Uses raw_diff when available so assembled/truncated diffs don't drop
    archetype evidence (e.g. method signature changes in secondary files).
    """
    if _is_test_only_commit(context):
        return True

    diff = context.raw_diff or context.diff or ""
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

    # Iterator safety refactoring: replaces direct collection mutation with
    # iterator-based removal to prevent ConcurrentModificationException.
    # Only clean when no guards are net-removed (guard refactoring is still a risk signal).
    if _ITERATOR_SAFETY_RE.search(diff) and not _is_net_guard_removal(diff):
        minus_direct_remove = len(re.findall(r"^-.*\.(remove|put)\(", diff, re.MULTILINE))
        plus_itr_remove = len(re.findall(r"^\+.*itr(?:erator)?\.remove\(\)", diff, re.MULTILINE))
        if minus_direct_remove >= 1 and plus_itr_remove >= 1:
            return True

    if is_message_only_diff(diff):
        return True

    return False
