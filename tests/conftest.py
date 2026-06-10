"""Shared fixtures for the commit-investigator test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


DATA_DIR = Path("data/apachejit")
REPOS_DIR = Path("data/repos")


def has_apachejit_data() -> bool:
    return (DATA_DIR / "apachejit_train.csv").exists()


def has_repos() -> bool:
    return (REPOS_DIR / "camel" / ".git").exists()


skip_no_data = pytest.mark.skipif(
    not has_apachejit_data(),
    reason="data/apachejit/ not available (run scripts/download_apachejit.sh)"
)

skip_no_repos = pytest.mark.skipif(
    not has_repos(),
    reason="data/repos/ not available (run scripts/clone_apache_repos.sh)"
)
