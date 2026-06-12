"""Historical defect context — defect-category priors from ApacheJIT closed pairs.

Finds K nearest buggy commits in the ApacheJIT training set (by code-metric
similarity), fetches their commit messages from local git repos, and derives
the top defect category labels. These labels are injected into the hypothesis
prompt to ground the LLM in historically plausible failure modes.

Design constraints:
- Temporal guard: never injects fix-commit content — only the buggy commit
  commit message is used (not the associated fix diff or message).
- Zero LLM calls. Pure deterministic lookup + regex classification.
- Graceful degradation: returns None on any data error, missing repo, etc.
  Callers must treat None as "context unavailable" and skip injection.
- Fallback: when K-nearest yields <3 classified messages (sparse local repo),
  falls back to a precomputed project-level defect distribution.
"""

from __future__ import annotations

import csv
import logging
import math
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

from commit_investigator.context.context_builder import InvestigationContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defect-category taxonomy (keyword → category)
# ---------------------------------------------------------------------------

_DEFECT_CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    ("null-dereference", re.compile(
        r"\bnull\b|NPE|NullPointer|nullcheck|null.pointer",
        re.IGNORECASE,
    )),
    ("concurrency", re.compile(
        r"\brace\b|concurrent|synchroni|deadlock|thread|volatile|atomic|lock\b|"
        r"\bblocking\b|\breentrant\b",
        re.IGNORECASE,
    )),
    ("resource-leak", re.compile(
        r"\bleak\b|close\b|unclosed|socket|stream|connection\b|finally|"
        r"\bfile.handle\b|\bopen\b.*not.*close",
        re.IGNORECASE,
    )),
    ("error-handling", re.compile(
        r"\bexception\b|timeout|retry|backoff|rollback|recover|"
        r"\bswallow|error.handler|\bfail\b|\bfailure\b",
        re.IGNORECASE,
    )),
    ("api-contract", re.compile(
        r"\bAPI\b|deprecat|interface\b|contract\b|signature\b|backwards?\s+compat|"
        r"migration\b|break.*compat|endpoint\b.*param|parameter.*rename",
        re.IGNORECASE,
    )),
    ("lifecycle-ordering", re.compile(
        r"\blifecycle\b|startup\b|shutdown\b|start\b|stop\b|"
        r"\binitialization\b|\bordering\b|\bphase\b|\bstart.*order|stop.*order",
        re.IGNORECASE,
    )),
    ("logic-error", re.compile(
        r"\boff.by.one\b|wrong\s+result|incorrect|miscalcul|invalid\s+state|"
        r"\bwrong\s+value\b|\bdefault\b.*wrong|\bwrong.*default\b|"
        r"\bmissing\s+dot\b|\bmissing\s+separator\b",
        re.IGNORECASE,
    )),
    ("input-validation", re.compile(
        r"\bvalidat|sanitize|parse\s+error|illegal\s+argument|boundary|edge\s+case|"
        r"\bformat\b|\bencod\b|\bcharset\b",
        re.IGNORECASE,
    )),
    ("configuration", re.compile(
        r"\bconfig\b|propert|setting\b|default\s+value|autowir|"
        r"\bspring.boot\b|\bpom\b|\bdependency\b|\bversion\b",
        re.IGNORECASE,
    )),
]


def _classify_message(message: str) -> str | None:
    """Return the first matching defect category for a commit message."""
    for category, pattern in _DEFECT_CATEGORIES:
        if pattern.search(message):
            return category
    return None


# ---------------------------------------------------------------------------
# Metric normalization & similarity
# ---------------------------------------------------------------------------

_METRIC_KEYS = ("la", "ld", "nf", "ent", "ns")
_LOG_KEYS = {"la", "ld", "nf"}  # right-skewed, log-transform before comparison


def _extract_metrics(row: dict[str, str]) -> tuple[float, ...] | None:
    """Extract and transform metrics from a CSV row. Returns None on error."""
    try:
        return tuple(
            math.log1p(float(row[k])) if k in _LOG_KEYS else float(row.get(k, 0) or 0)
            for k in _METRIC_KEYS
        )
    except (KeyError, ValueError):
        return None


def _euclidean(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ---------------------------------------------------------------------------
# Training data loader (lazy, process-level singleton)
# ---------------------------------------------------------------------------

class _TrainingRow(NamedTuple):
    commit_id: str
    project: str
    metrics: tuple[float, ...]


_TRAINING_CACHE: list[_TrainingRow] | None = None
_TRAINING_LOAD_ATTEMPTED = False

_JIT_CSV = Path(__file__).parent.parent.parent.parent / "data/apachejit/apachejit_train.csv"


def _load_training_data() -> list[_TrainingRow]:
    global _TRAINING_CACHE, _TRAINING_LOAD_ATTEMPTED
    if _TRAINING_LOAD_ATTEMPTED:
        return _TRAINING_CACHE or []
    _TRAINING_LOAD_ATTEMPTED = True
    try:
        rows = []
        with _JIT_CSV.open(newline="") as f:
            for row in csv.DictReader(f):
                if row.get("buggy") != "True":
                    continue
                metrics = _extract_metrics(row)
                if metrics is None:
                    continue
                rows.append(_TrainingRow(
                    commit_id=row["commit_id"],
                    project=row["project"],
                    metrics=metrics,
                ))
        _TRAINING_CACHE = rows
        logger.info("historical defect context: loaded %d buggy training rows from ApacheJIT", len(rows))
    except Exception as exc:
        logger.warning("historical defect context: failed to load training data: %s", exc)
        _TRAINING_CACHE = []
    return _TRAINING_CACHE or []


# ---------------------------------------------------------------------------
# Project-level fallback distribution (precomputed from fetched messages)
# ---------------------------------------------------------------------------

_PROJECT_DIST_CACHE: dict[str, dict[str, int]] = {}
_PROJECT_DIST_ATTEMPTED: set[str] = set()

# Minimum classifiable neighbors before skipping K-nearest and using fallback
_MIN_CLASSIFIED_FOR_KNN = 3


def _get_project_distribution(
    project_key: str,
    training: list[_TrainingRow],
    repos_base: Path,
    *,
    sample_size: int = 100,
) -> dict[str, int]:
    """Return a project-level defect category count, sampling up to sample_size commits."""
    if project_key in _PROJECT_DIST_CACHE:
        return _PROJECT_DIST_CACHE[project_key]
    if project_key in _PROJECT_DIST_ATTEMPTED:
        return {}
    _PROJECT_DIST_ATTEMPTED.add(project_key)

    project_rows = [r for r in training if r.project == project_key]
    # Sample evenly to bound startup cost
    step = max(1, len(project_rows) // sample_size)
    sample = project_rows[::step][:sample_size]

    counts: dict[str, int] = {}
    fetched = 0
    for row in sample:
        msg = _get_commit_message(row.commit_id, project_key, repos_base)
        if msg is None:
            continue
        fetched += 1
        cat = _classify_message(msg)
        if cat:
            counts[cat] = counts.get(cat, 0) + 1

    if counts:
        logger.info(
            "historical defect context: project-level distribution for %s: %d classified from %d fetched",
            project_key, sum(counts.values()), fetched,
        )
    _PROJECT_DIST_CACHE[project_key] = counts
    return counts


# ---------------------------------------------------------------------------
# Git commit message retrieval
# ---------------------------------------------------------------------------

_PROJECT_TO_REPO_NAMES: dict[str, str] = {
    "apache/camel": "camel",
    "apache/hadoop-hdfs": "hadoop",
    "apache/hadoop-mapreduce": "hadoop",
    "apache/hadoop": "hadoop",
}


def _get_commit_message(
    commit_id: str,
    project_key: str,
    repos_base: Path,
) -> str | None:
    repo_name = _PROJECT_TO_REPO_NAMES.get(project_key)
    if not repo_name:
        return None
    repo_path = repos_base / repo_name
    if not repo_path.is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%s", commit_id],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            msg = result.stdout.strip()
            if msg:
                return msg
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_historical_defect_context(
    context: InvestigationContext,
    *,
    k: int = 10,
    repos_base: Path | None = None,
) -> str | None:
    """Return a formatted historical context block, or None if unavailable.

    Finds the K nearest buggy commits in the ApacheJIT training set by
    code-metric similarity, fetches their commit messages from local git
    repos, classifies each message into a defect category, and returns
    the top-3 categories as a concise prior.

    When K-nearest hit rate is too sparse (<3 classified), falls back to
    a precomputed project-level defect distribution.

    Args:
        context: The investigation context including csv_features metrics.
        k: Number of nearest neighbors to query.
        repos_base: Base directory for git repositories (autodetected if None).

    Returns:
        A markdown block for injection into the hypothesis prompt, or None
        if the data or repos are unavailable.
    """
    if not context.csv_features:
        return None

    query_metrics = _extract_metrics(dict(context.csv_features))
    if query_metrics is None:
        return None

    training = _load_training_data()
    if not training:
        return None

    if repos_base is None:
        repos_base = _JIT_CSV.parent.parent / "repos"

    # Prefer same-project neighbors; fall back to all projects
    same_project_key = f"apache/{context.project}"
    same_project = [r for r in training if r.project == same_project_key]
    corpus = same_project if len(same_project) >= k else training

    # Find K nearest neighbors by metric distance
    scored = sorted(corpus, key=lambda r: _euclidean(r.metrics, query_metrics))
    neighbors = scored[:k]

    # Fetch commit messages and classify
    category_counts: dict[str, int] = {}
    fetched = 0
    for neighbor in neighbors:
        msg = _get_commit_message(neighbor.commit_id, neighbor.project, repos_base)
        if msg is None:
            continue
        fetched += 1
        cat = _classify_message(msg)
        if cat:
            category_counts[cat] = category_counts.get(cat, 0) + 1

    total_classified = sum(category_counts.values())
    using_fallback = total_classified < _MIN_CLASSIFIED_FOR_KNN

    # Fallback: use project-level distribution when K-nearest is too sparse
    if using_fallback and same_project:
        fallback_dist = _get_project_distribution(
            same_project_key, training, repos_base,
        )
        if fallback_dist:
            category_counts = fallback_dist
            total_classified = sum(fallback_dist.values())
            fetched = total_classified  # approximate for display

    if not category_counts:
        return None

    top = sorted(category_counts.items(), key=lambda x: -x[1])[:3]
    total_classified = sum(category_counts.values())

    source_label = (
        f"project-wide {context.project} base rate"
        if using_fallback
        else f"{fetched} similar {context.project} commits"
    )
    lines = [f"Historical defect pattern ({source_label}):"]
    for cat, count in top:
        pct = round(100 * count / total_classified)
        lines.append(f"  - {cat}: {pct}% of historical matches")

    return "\n".join(lines)
