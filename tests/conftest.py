"""Shared pytest fixtures for CR Analyzer tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "cab-window-01"


def load_cr_bundle_data(cr_dir: str) -> dict[str, Any]:
    """Load a CR bundle from a fixture directory into a dict ready for model_validate.

    Reads each artifact file and assembles the CRBundle-shaped dict.
    Markdown files are read as strings; JSON files are parsed.
    """
    cr_path = FIXTURES_DIR / cr_dir
    if not cr_path.exists():
        raise FileNotFoundError(f"Fixture directory not found: {cr_path}")

    bundle: dict[str, Any] = {}

    json_artifacts = {
        "itsm_record": "itsm_record.json",
        "cmdb_snapshot": "cmdb_snapshot.json",
        "sla_definitions": "sla_definitions.json",
        "maintenance_schedule": "maintenance_schedule.json",
        "incident_history": "incident_history.json",
        "pr_scope_flags": "pr_scope_flags.json",
    }
    for field, filename in json_artifacts.items():
        filepath = cr_path / filename
        if filepath.exists():
            bundle[field] = json.loads(filepath.read_text())

    md_artifacts = {
        "runbook": "runbook.md",
        "rollback_plan": "rollback_plan.md",
        "communication_plan": "communication_plan.md",
    }
    for field, filename in md_artifacts.items():
        filepath = cr_path / filename
        if filepath.exists():
            bundle[field] = filepath.read_text()

    return bundle


@pytest.fixture()
def cr_001_data() -> dict[str, Any]:
    """Full CR bundle data from cr-001 fixture."""
    return load_cr_bundle_data("cr-001")


@pytest.fixture()
def cr_002_data() -> dict[str, Any]:
    """Full CR bundle data from cr-002 fixture (has injected failures)."""
    return load_cr_bundle_data("cr-002")


@pytest.fixture()
def cr_003_data() -> dict[str, Any]:
    """Full CR bundle data from cr-003 fixture (missing comms plan)."""
    return load_cr_bundle_data("cr-003")


@pytest.fixture()
def minimal_bundle_data() -> dict[str, Any]:
    """Minimal valid CRBundle — only itsm_record, everything else None."""
    return {
        "itsm_record": {
            "change_id": "CR-MINIMAL-001",
            "type": "standard",
            "risk_category": "low",
            "title": "Minimal change",
            "description": "A minimal change for testing",
            "affected_services": ["test-service"],
            "requestor": "test.user",
            "approvers": ["approver.one"],
            "scheduled_window": {
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-01T02:00:00Z",
            },
        }
    }


@pytest.fixture()
def bpi_shaped_bundle_data() -> dict[str, Any]:
    """BPI 2014-shaped bundle — itsm_record only, no prose artifacts, no CMDB.

    Simulates what the BPI 2014 adapter produces: structured fields present
    but runbook, rollback, cmdb, sla, comms all absent.
    """
    return {
        "itsm_record": {
            "change_id": "CHG0012345",
            "type": "standard",
            "risk_category": "low",
            "title": "",
            "description": "",
            "affected_services": ["SUB000479", "WBA000082"],
            "requestor": "bpi-system",
            "approvers": ["cab-board"],
            "scheduled_window": {
                "start": "2013-03-15T08:00:00Z",
                "end": "2013-03-15T10:00:00Z",
            },
            "expected_duration_min": 120,
        },
        "incident_history": [
            {
                "incident_id": "INC0001",
                "service": "SUB000479",
                "change_category": "Standard Change Type 08",
                "severity": "P2",
                "root_cause_summary": "Config drift after change",
                "date": "2013-01-10",
            },
            {
                "incident_id": "INC0002",
                "service": "SUB000479",
                "change_category": "Standard Change Type 08",
                "severity": "P2",
                "root_cause_summary": "Service degradation post-change",
                "date": "2013-02-20",
            },
        ],
    }
