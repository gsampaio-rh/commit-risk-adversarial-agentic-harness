"""Shared serialization helpers for V4 model dataclasses."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any


def value_to_dict(value: Any) -> Any:
    """Recursively convert dataclass instances to plain dicts for JSON."""
    if value is None:
        return None
    if is_dataclass(value) and not isinstance(value, type):
        return value.to_dict()
    if isinstance(value, list):
        return [value_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: value_to_dict(item) for key, item in value.items()}
    return value


def instance_to_dict(obj: object) -> dict[str, Any]:
    """Serialize a dataclass instance using its field names."""
    return {field.name: value_to_dict(getattr(obj, field.name)) for field in fields(obj)}
