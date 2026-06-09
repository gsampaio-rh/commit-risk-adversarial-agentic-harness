"""Stage 8: Historical Pattern — match CR against past incident history.

L1: exact (change_category) grouping across affected services.
L2: sentence-transformer embedding similarity for fuzzy matching.
Dual-path: runs both, deduplicates by incident_id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from cr_analyzer.models.enums import FindingDimension, Severity
from cr_analyzer.models.findings import Finding, FindingEvidence
from cr_analyzer.models.outputs import HistoricalPatternOutput, NormalizeOutput

logger = logging.getLogger(__name__)

P1_SEVERITIES = {"P1"}
HIGH_SEVERITIES = {"P1", "P2"}
ALERT_THRESHOLD = 2
BLOCKER_THRESHOLD = 5
DEFAULT_SIMILARITY_THRESHOLD = 0.75

MethodKind = Literal["exact_match", "embedding_similarity", "dual"]


@dataclass
class HistoricalPatternConfig:
    """Configuration for historical pattern matching."""

    method: MethodKind = "exact_match"
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD
    model_name: str = "all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _incident_to_dict(inc: object) -> dict:
    return {
        "incident_id": inc.incident_id,  # type: ignore[attr-defined]
        "service": inc.service,  # type: ignore[attr-defined]
        "severity": inc.severity,  # type: ignore[attr-defined]
        "date": inc.date,  # type: ignore[attr-defined]
        "root_cause_summary": inc.root_cause_summary,  # type: ignore[attr-defined]
    }


def _assess_severity(incidents: list[dict]) -> Severity | None:
    """Determine finding severity from matched incidents.

    >=5 incidents OR any P1 → blocker
    >=2 high-severity (P1/P2) → warning
    Otherwise → None (no finding)
    """
    has_p1 = any(inc["severity"] in P1_SEVERITIES for inc in incidents)
    high_sev_count = sum(1 for inc in incidents if inc["severity"] in HIGH_SEVERITIES)

    if len(incidents) >= BLOCKER_THRESHOLD or has_p1:
        return Severity.BLOCKER
    if high_sev_count >= ALERT_THRESHOLD:
        return Severity.WARNING
    return None


def _make_finding(
    category: str,
    incidents: list[dict],
    severity: Severity,
    method: str,
    *,
    similarity_score: float | None = None,
) -> Finding:
    services_involved = sorted({inc["service"] for inc in incidents})
    service_label = ", ".join(services_involved)

    evidence_kwargs: dict = {
        "artifact": "incident_history",
        "service": services_involved[0],
        "change_category": category,
        "matching_incidents": incidents,
        "method": method,
    }
    if similarity_score is not None:
        evidence_kwargs["similarity_score"] = similarity_score

    return Finding(
        dimension=FindingDimension.HISTORICAL_PATTERN,
        severity=severity,
        finding=(
            f"Service(s) {service_label} + category {category} has "
            f"{len(incidents)} prior incident(s). "
            f"Pattern suggests elevated risk."
        ),
        evidence=FindingEvidence(**evidence_kwargs),
        remediation=(
            f"Review past incidents for {service_label}/{category} and add "
            f"mitigations to the runbook addressing root causes."
        ),
    )


# ---------------------------------------------------------------------------
# L1: Exact category match
# ---------------------------------------------------------------------------


def _build_category_index(
    norm: NormalizeOutput,
) -> dict[str, list[dict]]:
    """Group incidents by change_category for the CR's affected services."""
    affected = set(norm.affected_services)
    index: dict[str, list[dict]] = {}
    if not norm.incidents:
        return index
    for inc in norm.incidents:
        if inc.service not in affected:
            continue
        index.setdefault(inc.change_category, []).append(_incident_to_dict(inc))
    return index


def _run_l1(norm: NormalizeOutput) -> list[Finding]:
    findings: list[Finding] = []
    index = _build_category_index(norm)

    for category, incidents in index.items():
        severity = _assess_severity(incidents)
        if severity is None:
            continue
        findings.append(_make_finding(category, incidents, severity, "exact_match"))

    return findings


# ---------------------------------------------------------------------------
# L2: Embedding similarity
# ---------------------------------------------------------------------------

_encoder_cache: dict[str, object] = {}


def _get_encoder(model_name: str):
    """Lazy-load sentence transformer, cached per model name."""
    if model_name not in _encoder_cache:
        from sentence_transformers import SentenceTransformer

        _encoder_cache[model_name] = SentenceTransformer(model_name)
    return _encoder_cache[model_name]


def _cr_text(norm: NormalizeOutput) -> str:
    """Build a text representation of the CR for embedding."""
    parts = [
        f"change_type: {norm.change_type.value}",
        f"services: {', '.join(norm.affected_services)}",
    ]
    if norm.description:
        parts.append(f"description: {norm.description}")
    return " | ".join(parts)


def _incident_text(inc_dict: dict) -> str:
    parts = [f"severity: {inc_dict['severity']}"]
    if inc_dict.get("root_cause_summary"):
        parts.append(f"root_cause: {inc_dict['root_cause_summary']}")
    if inc_dict.get("change_category"):
        parts.append(f"category: {inc_dict['change_category']}")
    return " | ".join(parts)


@dataclass
class _EmbeddingCluster:
    """Incidents grouped by embedding similarity to the CR."""

    incidents: list[dict] = field(default_factory=list)
    max_similarity: float = 0.0
    category_label: str = ""


def _run_l2(
    norm: NormalizeOutput,
    config: HistoricalPatternConfig,
) -> list[Finding]:
    if not norm.incidents:
        return []

    affected = set(norm.affected_services)
    relevant_incidents = [
        _incident_to_dict(inc)
        for inc in norm.incidents
        if inc.service in affected
    ]
    if not relevant_incidents:
        return []

    encoder = _get_encoder(config.model_name)
    cr_embedding = encoder.encode(_cr_text(norm), normalize_embeddings=True)  # type: ignore[union-attr]

    inc_texts = [_incident_text(inc) for inc in relevant_incidents]
    inc_embeddings = encoder.encode(inc_texts, normalize_embeddings=True)  # type: ignore[union-attr]

    # Cosine similarity (embeddings are normalized, so dot product = cosine)
    similarities = inc_embeddings @ cr_embedding

    # Group matching incidents by change_category for coherent findings
    clusters: dict[str, _EmbeddingCluster] = {}
    for i, sim in enumerate(similarities):
        if sim < config.similarity_threshold:
            continue
        inc = relevant_incidents[i]
        cat = inc.get("change_category", "unknown")
        if cat not in clusters:
            clusters[cat] = _EmbeddingCluster(category_label=cat)
        clusters[cat].incidents.append(inc)
        clusters[cat].max_similarity = max(clusters[cat].max_similarity, float(sim))

    findings: list[Finding] = []
    for cat, cluster in clusters.items():
        severity = _assess_severity(cluster.incidents)
        if severity is None:
            continue
        findings.append(
            _make_finding(
                cat,
                cluster.incidents,
                severity,
                "embedding_similarity",
                similarity_score=cluster.max_similarity,
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Dual-path merge
# ---------------------------------------------------------------------------


def _merge_findings(l1: list[Finding], l2: list[Finding]) -> list[Finding]:
    """Merge L1 and L2 findings, deduplicating by incident_id."""
    seen_incidents: set[str] = set()
    merged: list[Finding] = []

    for f in l1:
        if f.evidence and hasattr(f.evidence, "matching_incidents"):
            ids = {inc["incident_id"] for inc in f.evidence.matching_incidents}
            seen_incidents.update(ids)
        merged.append(f)

    for f in l2:
        if f.evidence and hasattr(f.evidence, "matching_incidents"):
            new_incidents = [
                inc for inc in f.evidence.matching_incidents
                if inc["incident_id"] not in seen_incidents
            ]
            if not new_incidents:
                continue
            seen_incidents.update(inc["incident_id"] for inc in new_incidents)

            severity = _assess_severity(new_incidents)
            if severity is None:
                continue

            cat = f.evidence.change_category if hasattr(f.evidence, "change_category") else "unknown"
            sim = f.evidence.similarity_score if hasattr(f.evidence, "similarity_score") else None
            merged.append(
                _make_finding(cat, new_incidents, severity, "embedding_similarity", similarity_score=sim)
            )
        else:
            merged.append(f)

    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_historical_pattern(
    norm: NormalizeOutput,
    config: HistoricalPatternConfig | None = None,
) -> HistoricalPatternOutput:
    """Run historical pattern matching with configurable L1/L2/dual strategy."""
    cfg = config or HistoricalPatternConfig()

    if cfg.method == "exact_match":
        return HistoricalPatternOutput(
            change_id=norm.change_id,
            findings=_run_l1(norm),
            method_used="exact_match",
        )

    if cfg.method == "embedding_similarity":
        try:
            findings = _run_l2(norm, cfg)
        except ImportError:
            logger.warning(
                "sentence-transformers not installed; falling back to L1 exact match"
            )
            findings = _run_l1(norm)
            return HistoricalPatternOutput(
                change_id=norm.change_id,
                findings=findings,
                method_used="exact_match",
            )
        return HistoricalPatternOutput(
            change_id=norm.change_id,
            findings=findings,
            method_used="embedding_similarity",
        )

    # dual: run both, merge
    l1_findings = _run_l1(norm)
    try:
        l2_findings = _run_l2(norm, cfg)
        merged = _merge_findings(l1_findings, l2_findings)
        method: MethodKind = "dual"
    except ImportError:
        logger.warning(
            "sentence-transformers not installed; dual mode falling back to L1 only"
        )
        merged = l1_findings
        method = "exact_match"

    return HistoricalPatternOutput(
        change_id=norm.change_id,
        findings=merged,
        method_used=method,
    )
