"""JIRA client for Apache public JIRA — fetches issue metadata with disk cache.

Used exclusively at eval time (D3–D5 dimensions). Never during agent investigation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


APACHE_JIRA_BASE = "https://issues.apache.org/jira/rest/api/2/issue"

DEFAULT_CACHE_DIR = Path("data/jira_cache")

FIELDS_TO_FETCH = "summary,description,priority,components,resolution,status,issuetype"


@dataclass
class JiraIssue:
    """Structured representation of a JIRA issue for eval purposes."""

    key: str
    summary: str
    description: str | None
    priority: str | None
    components: list[str]
    resolution: str | None
    status: str | None
    raw: dict[str, Any] | None = None


class JiraClientError(Exception):
    """Raised when JIRA API returns a non-recoverable error."""


class JiraClient:
    """Fetches Apache JIRA issues with local disk cache.

    Responses are cached as JSON files to avoid redundant network calls.
    Cache is keyed by issue_key (e.g., CAMEL-1234.json).
    """

    def __init__(
        self,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout
        self._max_retries = max_retries

    def get_issue(self, issue_key: str) -> JiraIssue:
        """Fetch issue metadata. Returns cached version if available."""
        cached = self._read_cache(issue_key)
        if cached is not None:
            return self._parse_issue(issue_key, cached)

        raw = self._fetch_from_api(issue_key)
        self._write_cache(issue_key, raw)
        return self._parse_issue(issue_key, raw)

    def is_cached(self, issue_key: str) -> bool:
        """Check if an issue is already cached on disk."""
        return self._cache_path(issue_key).exists()

    def _cache_path(self, issue_key: str) -> Path:
        return self._cache_dir / f"{issue_key}.json"

    def _read_cache(self, issue_key: str) -> dict[str, Any] | None:
        path = self._cache_path(issue_key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_cache(self, issue_key: str, data: dict[str, Any]) -> None:
        path = self._cache_path(issue_key)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _fetch_from_api(self, issue_key: str) -> dict[str, Any]:
        """Fetch from Apache JIRA REST API with retry logic."""
        url = f"{APACHE_JIRA_BASE}/{issue_key}"
        params = {"fields": FIELDS_TO_FETCH}

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(url, params=params)

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    raise JiraClientError(f"Issue {issue_key} not found (404)")
                elif response.status_code == 429:
                    import time

                    wait = min(2**attempt * 5, 60)
                    time.sleep(wait)
                    continue
                else:
                    last_error = JiraClientError(
                        f"JIRA API error {response.status_code} for {issue_key}: "
                        f"{response.text[:200]}"
                    )
            except httpx.HTTPError as e:
                last_error = e

        raise JiraClientError(
            f"Failed to fetch {issue_key} after {self._max_retries} attempts: {last_error}"
        )

    @staticmethod
    def _parse_issue(issue_key: str, raw: dict[str, Any]) -> JiraIssue:
        """Parse raw JIRA JSON into structured JiraIssue."""
        fields = raw.get("fields", {})
        priority = fields.get("priority")
        resolution = fields.get("resolution")
        status = fields.get("status")
        components = fields.get("components", [])

        return JiraIssue(
            key=issue_key,
            summary=fields.get("summary", ""),
            description=fields.get("description"),
            priority=priority["name"] if priority else None,
            components=[c["name"] for c in components if "name" in c],
            resolution=resolution["name"] if resolution else None,
            status=status["name"] if status else None,
            raw=raw,
        )
