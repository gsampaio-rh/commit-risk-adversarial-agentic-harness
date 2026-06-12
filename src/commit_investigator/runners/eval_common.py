"""Shared helpers for eval runners (run_eval, run_multiturn_ab).

Canonical implementations for environment loading, git revision lookup,
and project-name normalization used by the eval pipeline.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _load_dotenv(path: str | Path = ".env") -> None:
    """Canonical eval-runner implementation: load KEY=VALUE pairs from .env (only unset keys)."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _git_rev() -> str:
    """Canonical eval-runner implementation: return short git HEAD revision, or 'unknown' on failure."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def _normalize_project(project: str) -> str:
    """Canonical eval-runner implementation: normalize project name to lowercase V1 repo slug.

    Input formats:
      - 'camel' → 'camel' (short name, already lowercase)
      - 'apache/camel' → 'camel' (ApacheJIT CSV prefix stripped)
      - 'CAMEL' → 'camel' (uppercase normalized)
    """
    p = project.strip().lower()
    if "/" in p:
        p = p.rsplit("/", 1)[-1]
    return p
