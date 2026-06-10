"""LLM response parsing utilities: JSON extraction and field normalization.

These functions handle the messy reality of LLM output: markdown fences,
nested JSON, non-string fields, and empty findings lists.
"""

from __future__ import annotations

import json
import re
from typing import Any


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from LLM output, tolerating markdown fences."""
    if not text:
        return {}

    text = text.strip()

    try:
        result: dict[str, Any] = json.loads(text)
        return result
    except (json.JSONDecodeError, TypeError):
        pass

    match = _JSON_FENCE_RE.search(text)
    if match:
        try:
            result = json.loads(match.group(1))
            return result
        except (json.JSONDecodeError, TypeError):
            pass

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            result = json.loads(text[brace_start : brace_end + 1])
            return result
        except (json.JSONDecodeError, TypeError):
            pass

    return {}


def coerce_text_field(value: Any, default: str) -> str:
    """Normalize LLM output to a string (some models return nested JSON objects)."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def normalize_findings(raw: Any) -> list[str]:
    """Ensure findings is a list of strings for schema validation."""
    if not raw:
        return ["Investigation completed"]
    if not isinstance(raw, list):
        return [coerce_text_field(raw, "Investigation completed")]
    findings = [coerce_text_field(item, "") for item in raw]
    findings = [f for f in findings if f.strip()]
    return findings or ["Investigation completed"]


def parse_lines(raw: Any) -> tuple[int, int] | None:
    """Parse a line range from various LLM output formats.

    Handles: [1, 10], "1-10", "370-377", [1], None.
    """
    if raw is None:
        return None

    if isinstance(raw, (list, tuple)):
        nums = [int(x) for x in raw if str(x).strip().isdigit()]
        if len(nums) >= 2:
            return (nums[0], nums[1])
        if len(nums) == 1:
            return (nums[0], nums[0])
        return None

    if isinstance(raw, str):
        raw = raw.strip()
        if "-" in raw:
            parts = raw.split("-", 1)
            try:
                return (int(parts[0].strip()), int(parts[1].strip()))
            except ValueError:
                return None
        try:
            n = int(raw)
            return (n, n)
        except ValueError:
            return None

    return None
