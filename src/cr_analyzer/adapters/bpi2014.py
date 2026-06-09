"""BPI Challenge 2014 adapter — CSV parser producing CRBundle objects."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from cr_analyzer.models.bundle import CRBundle, Incident, ItsmRecord, ScheduledWindow

BPI_DATE_FMT = "%d-%m-%Y %H:%M"
INCIDENT_DATE_FMT = "%d/%m/%Y %H:%M:%S"

RISK_MAP = {
    "Minor Change": "low",
    "Business Change": "medium",
    "Major Business Change": "high",
}

IMPACT_TO_SEVERITY = {
    "1": "P1",
    "2": "P2",
    "3": "P3",
    "4": "P4",
}


def _derive_change_type(row: dict) -> str:
    """Map BPI fields to standard/normal/emergency."""
    if row.get("Emergency Change", "N") == "Y":
        return "emergency"
    ct = row.get("Change Type", "")
    if ct.startswith("Standard"):
        return "standard"
    return "normal"


def _parse_bpi_datetime(s: str) -> datetime | None:
    if not s or not s.strip():
        return None
    try:
        return datetime.strptime(s.strip(), BPI_DATE_FMT)
    except ValueError:
        return None


def _parse_incident_datetime(s: str) -> str:
    """Parse incident datetime to ISO date string."""
    if not s or not s.strip():
        return ""
    try:
        dt = datetime.strptime(s.strip(), INCIDENT_DATE_FMT)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return s.strip()


def load_changes(change_csv: Path) -> dict[str, CRBundle]:
    """Parse Detail_Change.csv into CRBundle objects, grouped by Change ID.

    Multiple rows per change (one per CI) are grouped into a single bundle
    with aggregated affected_services.
    """
    groups: dict[str, list[dict]] = defaultdict(list)

    with open(change_csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            groups[row["Change ID"]].append(row)

    bundles: dict[str, CRBundle] = {}
    for change_id, rows in groups.items():
        first = rows[0]

        affected_services = sorted({r["CI Name (aff)"] for r in rows if r["CI Name (aff)"]})

        planned_start = _parse_bpi_datetime(first.get("Planned Start", ""))
        planned_end = _parse_bpi_datetime(first.get("Planned End", ""))

        if planned_start is None or planned_end is None:
            continue

        risk = RISK_MAP.get(first.get("Risk Assessment", ""), "low")
        change_type = _derive_change_type(first)

        record = ItsmRecord(
            change_id=change_id,
            type=change_type,
            risk_category=risk,
            title="",
            description="",
            affected_services=affected_services,
            requestor="bpi-system",
            approvers=["cab-board"],
            scheduled_window=ScheduledWindow(start=planned_start, end=planned_end),
        )

        bundles[change_id] = CRBundle(itsm_record=record)

    return bundles


def load_incident_index(
    incident_csv: Path,
) -> dict[str, list[Incident]]:
    """Parse Detail_Incident.csv into an index keyed by CI Name.

    Only includes incidents linked to changes (Related Change non-empty).
    """
    index: dict[str, list[Incident]] = defaultdict(list)

    with open(incident_csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            related_change = row.get("Related Change", "").strip()
            if not related_change:
                continue

            ci_name = row.get("CI Name (aff)", "").strip()
            if not ci_name:
                continue

            impact = row.get("Impact", "4")
            severity = IMPACT_TO_SEVERITY.get(impact, f"P{impact}")

            incident = Incident(
                incident_id=row.get("Incident ID", ""),
                service=ci_name,
                change_category=related_change,
                severity=severity,
                root_cause_summary=row.get("Category", ""),
                date=_parse_incident_datetime(row.get("Open Time", "")),
            )
            index[ci_name].append(incident)

    return index


def enrich_bundles_with_incidents(
    bundles: dict[str, CRBundle],
    incident_index: dict[str, list[Incident]],
) -> None:
    """Attach incident history to bundles based on affected services."""
    for bundle in bundles.values():
        incidents: list[Incident] = []
        for svc in bundle.itsm_record.affected_services:
            incidents.extend(incident_index.get(svc, []))
        if incidents:
            bundle.incident_history = incidents


def derive_cab_windows(
    bundles: dict[str, CRBundle],
) -> dict[str, list[CRBundle]]:
    """Group CAB-approval changes by ISO week of Planned Start.

    Returns dict[week_label, list[CRBundle]] for CAB changes only.
    """
    windows: dict[str, list[CRBundle]] = defaultdict(list)

    for bundle in bundles.values():
        window = bundle.itsm_record.scheduled_window
        iso = window.start.isocalendar()
        week_label = f"{iso.year}-W{iso.week:02d}"
        windows[week_label].append(bundle)

    return dict(sorted(windows.items()))


def load_bpi2014(
    data_dir: Path,
    *,
    cab_only: bool = False,
) -> tuple[dict[str, CRBundle], dict[str, list[CRBundle]]]:
    """Full BPI 2014 loading pipeline.

    Returns (all_bundles, cab_windows).
    """
    change_csv = data_dir / "Detail_Change.csv"
    incident_csv = data_dir / "Detail_Incident.csv"

    bundles = load_changes(change_csv)
    incident_index = load_incident_index(incident_csv)
    enrich_bundles_with_incidents(bundles, incident_index)

    # Filter CAB-approval changes for windows
    cab_bundles: dict[str, CRBundle] = {}
    with open(change_csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        cab_ids = {row["Change ID"] for row in reader if row.get("CAB-approval needed") == "Y"}

    cab_bundles = {cid: b for cid, b in bundles.items() if cid in cab_ids}
    cab_windows = derive_cab_windows(cab_bundles)

    if cab_only:
        return cab_bundles, cab_windows

    return bundles, cab_windows
