"""Stage 1: Ingest — parse CR bundle artifacts into structured format."""

from __future__ import annotations

import re

from cr_analyzer.models.bundle import CRBundle
from cr_analyzer.models.outputs import IngestOutput, ParsedMarkdown

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_STEP_RE = re.compile(r"^\d+\.\s+\*{0,2}(.+?)\*{0,2}\s*$", re.MULTILINE)
_CODE_BLOCK_RE = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)
_BACKTICK_REF_RE = re.compile(r"`([a-zA-Z][\w./-]+)`")


def _parse_markdown(raw: str) -> ParsedMarkdown:
    """Extract structure from a Markdown artifact."""
    sections = [m.group(2).strip() for m in _HEADING_RE.finditer(raw)]
    steps = [m.group(1).strip() for m in _STEP_RE.finditer(raw)]
    commands = [m.group(1).strip() for m in _CODE_BLOCK_RE.finditer(raw)]
    service_refs = list(dict.fromkeys(_BACKTICK_REF_RE.findall(raw)))
    return ParsedMarkdown(
        sections=sections,
        steps=steps,
        service_refs=service_refs,
        commands=commands,
        raw=raw,
    )


def run_ingest(bundle: CRBundle) -> IngestOutput:
    """Parse a CRBundle into an IngestOutput with structured Markdown fields."""
    rec = bundle.itsm_record

    runbook = _parse_markdown(bundle.runbook) if bundle.runbook is not None else None
    rollback = _parse_markdown(bundle.rollback_plan) if bundle.rollback_plan is not None else None

    return IngestOutput(
        change_id=rec.change_id,
        itsm_record=rec.model_dump(),
        runbook=runbook,
        rollback=rollback,
        cmdb=bundle.cmdb_snapshot,
        sla=bundle.sla_definitions,
        schedule=bundle.maintenance_schedule,
        comms=bundle.communication_plan,
        incidents=bundle.incident_history,
        pr_flags=bundle.pr_scope_flags,
    )
