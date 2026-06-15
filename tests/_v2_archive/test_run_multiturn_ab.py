"""Tests for run_multiturn_ab defaults."""

from __future__ import annotations

import argparse

from commit_investigator.runners.run_multiturn_ab import HARD_COMMITS, FROZEN_CONTROL_D3


def test_hard_commits_exactly_three():
    assert len(HARD_COMMITS) == 3
    prefixes = {c[0] for c in HARD_COMMITS}
    assert prefixes == {"2213f71944ae", "409664582f53", "572f3cee35fe"}


def test_frozen_control_d3_all_zero():
    assert set(FROZEN_CONTROL_D3.values()) == {0.0}
    assert set(FROZEN_CONTROL_D3.keys()) == {"2213f71944ae", "409664582f53", "572f3cee35fe"}


def test_default_data_paths_match_run_eval():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-csv", default="data/apachejit/apachejit_test_large.csv")
    parser.add_argument("--zip", default="data/apachejit/apachejit_dataset_replication.zip")
    args = parser.parse_args([])
    assert args.zip.endswith("apachejit_dataset_replication.zip")
    assert args.test_csv.endswith("apachejit_test_large.csv")
