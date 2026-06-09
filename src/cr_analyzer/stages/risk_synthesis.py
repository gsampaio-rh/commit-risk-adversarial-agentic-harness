"""Stage 9: Risk Synthesis — deterministic severity rollup + template/LLM report.

L1: template-based fill-in-the-blank reports.
L2: LLM narrative for conditional/reject CRs (selective routing).
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from cr_analyzer.models.enums import Recommendation, Severity
from cr_analyzer.models.findings import Finding
from cr_analyzer.models.outputs import (
    AnalysisCoverage,
    CabReport,
    CabSummary,
    CrossCrConflict,
    DimensionSeverityCounts,
    DispositionBreakdown,
    ProcessingInfo,
    ScheduleSlaOutput,
)

logger = logging.getLogger(__name__)


def _rollup_recommendation(findings: list[Finding]) -> Recommendation:
    """R1-R4 deterministic rules."""
    has_blocker = any(f.severity == Severity.BLOCKER for f in findings)
    if has_blocker:
        return Recommendation.REJECT

    warning_dims = {f.dimension for f in findings if f.severity == Severity.WARNING}
    if len(warning_dims) >= 2:
        return Recommendation.CONDITIONAL

    return Recommendation.APPROVE


def _derive_risk_level(rec: Recommendation, findings: list[Finding]) -> Literal["low", "medium", "high", "critical"]:
    if rec == Recommendation.REJECT:
        return "critical"
    if rec == Recommendation.CONDITIONAL:
        return "high"
    has_warnings = any(f.severity == Severity.WARNING for f in findings)
    if has_warnings:
        return "medium"
    return "low"


def _build_dimension_summary(findings: list[Finding]) -> dict[str, DimensionSeverityCounts]:
    summary: dict[str, dict[str, int]] = defaultdict(lambda: {"blocker": 0, "warning": 0, "info": 0})
    for f in findings:
        summary[f.dimension.value][f.severity.value] += 1
    return {dim: DimensionSeverityCounts(**counts) for dim, counts in summary.items()}


def _derive_conditional_actions(findings: list[Finding], rec: Recommendation) -> list[str]:
    if rec != Recommendation.CONDITIONAL:
        return []
    actions = []
    for f in findings:
        if f.severity in (Severity.BLOCKER, Severity.WARNING) and f.remediation:
            actions.append(f.remediation)
    return actions


def synthesize_report(
    change_id: str,
    all_findings: list[Finding],
    stages_skipped: list[str],
    stages_executed: int,
    stages_degraded: int = 0,
) -> CabReport:
    """Produce a per-CR CabReport from aggregated findings."""
    recommendation = _rollup_recommendation(all_findings)
    risk_level = _derive_risk_level(recommendation, all_findings)

    return CabReport(
        change_id=change_id,
        risk_level=risk_level,
        recommendation=recommendation,
        findings=all_findings,
        conditional_actions=_derive_conditional_actions(all_findings, recommendation),
        dimension_summary=_build_dimension_summary(all_findings),
        stages_skipped=stages_skipped,
        analysis_coverage=AnalysisCoverage(
            executed=stages_executed,
            skipped=len(stages_skipped),
            degraded=stages_degraded,
        ),
    )


def synthesize_summary(
    window_id: str,
    reports: list[CabReport],
    schedule_output: ScheduleSlaOutput | None = None,
    wall_clock_seconds: float = 0,
    cost_usd: float = 0,
) -> CabSummary:
    """Produce a CAB window summary from per-CR reports."""
    breakdown = DispositionBreakdown(
        approve=sum(1 for r in reports if r.recommendation == Recommendation.APPROVE),
        conditional=sum(1 for r in reports if r.recommendation == Recommendation.CONDITIONAL),
        reject=sum(1 for r in reports if r.recommendation == Recommendation.REJECT),
    )

    conflicts: list[CrossCrConflict] = []
    if schedule_output:
        for sc in schedule_output.scheduling_conflicts:
            conflicts.append(CrossCrConflict(
                type="scheduling_overlap",
                cr_pair=sc.cr_pair,
                description=(
                    f"{sc.overlap_window.start.isoformat()}-{sc.overlap_window.end.isoformat()} "
                    f"on {', '.join(sc.shared_services)}"
                ),
            ))

    return CabSummary(
        window_id=window_id,
        total_crs=len(reports),
        disposition_breakdown=breakdown,
        cross_cr_conflicts=conflicts,
        processing=ProcessingInfo(
            wall_clock_seconds=wall_clock_seconds,
            cost_usd=cost_usd,
        ),
    )


# ---------------------------------------------------------------------------
# Template Markdown report
# ---------------------------------------------------------------------------

_SEVERITY_BADGE = {
    Severity.BLOCKER: "BLOCKER",
    Severity.WARNING: "WARNING",
    Severity.INFO: "INFO",
}


def render_cr_report_md(report: CabReport) -> str:
    """Render a per-CR risk assessment as Markdown."""
    lines = [
        f"# Change Risk Assessment: {report.change_id}",
        "",
        f"**Risk Level:** {report.risk_level.upper()}",
        f"**Recommendation:** {report.recommendation.value.upper()}",
        "",
    ]

    if report.stages_skipped:
        lines.append(f"**Stages skipped:** {', '.join(report.stages_skipped)}")
        cov = report.analysis_coverage
        lines.append(f"**Coverage:** {cov.executed} executed, {cov.skipped} skipped, {cov.degraded} degraded")
        lines.append("")

    if report.conditional_actions:
        lines.append("## Required Actions")
        lines.append("")
        for action in report.conditional_actions:
            lines.append(f"- {action}")
        lines.append("")

    if report.findings:
        lines.append("## Findings")
        lines.append("")
        for f in report.findings:
            badge = _SEVERITY_BADGE.get(f.severity, f.severity.value)
            lines.append(f"### [{badge}] {f.dimension.value}")
            lines.append("")
            lines.append(f"{f.finding}")
            lines.append("")
            if f.remediation:
                lines.append(f"**Remediation:** {f.remediation}")
                lines.append("")
    else:
        lines.append("No findings. Clean change request.")
        lines.append("")

    return "\n".join(lines)


def render_summary_md(summary: CabSummary) -> str:
    """Render a CAB window summary as Markdown."""
    bd = summary.disposition_breakdown
    lines = [
        f"# CAB Summary: {summary.window_id}",
        "",
        f"**Total CRs:** {summary.total_crs}",
        f"**Approve:** {bd.approve} | **Conditional:** {bd.conditional} | **Reject:** {bd.reject}",
        "",
    ]

    if summary.cross_cr_conflicts:
        lines.append("## Cross-CR Conflicts")
        lines.append("")
        for c in summary.cross_cr_conflicts:
            lines.append(f"- **{c.type}:** {', '.join(c.cr_pair)} — {c.description}")
        lines.append("")

    p = summary.processing
    lines.append(f"**Processing:** {p.wall_clock_seconds:.1f}s, ${p.cost_usd:.4f}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# L2: LLM narrative synthesis
# ---------------------------------------------------------------------------

COST_PER_1K_INPUT = 0.0015
COST_PER_1K_OUTPUT = 0.002
APPROX_CHARS_PER_TOKEN = 4


@dataclass
class RiskSynthesisConfig:
    """Configuration for risk synthesis L1/L2."""

    method: Literal["template", "llm_narrative"] = "template"
    api_base: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    cost_ceiling_usd: float = 2.0
    max_output_tokens: int = 512

    def __post_init__(self) -> None:
        if not self.api_base:
            self.api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
        if not self.api_key:
            self.api_key = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))


@dataclass
class CostTracker:
    """Track LLM token usage and estimated cost across a batch."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    cr_costs: dict[str, float] = field(default_factory=dict)

    @property
    def budget_remaining(self) -> float:
        return max(0.0, 2.0 - self.total_cost_usd)

    def record(self, change_id: str, input_tokens: int, output_tokens: int) -> None:
        cost = (input_tokens / 1000) * COST_PER_1K_INPUT + (output_tokens / 1000) * COST_PER_1K_OUTPUT
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost_usd += cost
        self.cr_costs[change_id] = cost


_NARRATIVE_SYSTEM_PROMPT = """You are a CAB (Change Advisory Board) risk analyst.
Given a structured list of findings from an automated change request analysis pipeline,
write a concise cross-dimension risk narrative for the CAB chair.

Requirements:
- Explain WHY the combination of findings matters (not just list them)
- Write specific conditional-approval requirements in natural language
- Focus on actionable insights the CAB chair needs to make a decision
- Be concise: 3-5 sentences maximum
- Do not repeat finding details verbatim; synthesize the overall risk picture"""


def _build_llm_prompt(change_id: str, findings: list[Finding], recommendation: Recommendation) -> str:
    findings_json = [
        {
            "dimension": f.dimension.value,
            "severity": f.severity.value,
            "finding": f.finding,
            "remediation": f.remediation,
        }
        for f in findings
    ]
    return json.dumps({
        "change_id": change_id,
        "recommendation": recommendation.value,
        "findings": findings_json,
    }, indent=2)


def _call_llm(prompt: str, config: RiskSynthesisConfig) -> tuple[str, int, int]:
    """Call OpenAI-compatible API. Returns (response_text, input_tokens, output_tokens).

    Raises RuntimeError on failure.
    """
    import httpx

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.api_key}",
    }
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": _NARRATIVE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": config.max_output_tokens,
        "temperature": 0.3,
    }

    url = f"{config.api_base.rstrip('/')}/chat/completions"

    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, json=body, headers=headers)
        response.raise_for_status()
        data = response.json()

    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def synthesize_report_l2(
    change_id: str,
    all_findings: list[Finding],
    stages_skipped: list[str],
    stages_executed: int,
    stages_degraded: int = 0,
    config: RiskSynthesisConfig | None = None,
    cost_tracker: CostTracker | None = None,
) -> CabReport:
    """L2 risk synthesis with selective LLM routing.

    - approve CRs → template (L1), no LLM call
    - conditional/reject CRs → LLM narrative if within budget
    """
    cfg = config or RiskSynthesisConfig()
    tracker = cost_tracker or CostTracker()
    recommendation = _rollup_recommendation(all_findings)
    risk_level = _derive_risk_level(recommendation, all_findings)

    report = CabReport(
        change_id=change_id,
        risk_level=risk_level,
        recommendation=recommendation,
        findings=all_findings,
        conditional_actions=_derive_conditional_actions(all_findings, recommendation),
        dimension_summary=_build_dimension_summary(all_findings),
        stages_skipped=stages_skipped,
        analysis_coverage=AnalysisCoverage(
            executed=stages_executed,
            skipped=len(stages_skipped),
            degraded=stages_degraded,
        ),
    )

    if cfg.method != "llm_narrative":
        return report

    # Selective routing: only call LLM for conditional/reject
    if recommendation == Recommendation.APPROVE:
        return report

    # Budget check
    if tracker.total_cost_usd >= cfg.cost_ceiling_usd:
        logger.warning(
            "Cost ceiling reached ($%.4f >= $%.2f); falling back to template for %s",
            tracker.total_cost_usd,
            cfg.cost_ceiling_usd,
            change_id,
        )
        return report

    # LLM narrative
    try:
        prompt = _build_llm_prompt(change_id, all_findings, recommendation)
        narrative, input_tokens, output_tokens = _call_llm(prompt, cfg)
        tracker.record(change_id, input_tokens, output_tokens)

        report.method_used = "llm_narrative"
        report.narrative = narrative
    except Exception as e:
        logger.warning("LLM call failed for %s: %s; falling back to template", change_id, e)

    return report


def render_cr_report_l2_md(report: CabReport) -> str:
    """Render a report with optional LLM narrative section."""
    md = render_cr_report_md(report)

    narrative = getattr(report, "narrative", None)
    if narrative and report.method_used == "llm_narrative":
        narrative_section = (
            "\n## Risk Narrative (AI-generated)\n\n"
            f"{narrative}\n"
        )
        # Insert before findings section
        if "## Findings" in md:
            md = md.replace("## Findings", f"{narrative_section}\n## Findings")
        else:
            md += narrative_section

    return md
