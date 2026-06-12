"""Tests for the ground truth graph loader."""

import pytest

from tests.conftest import skip_no_data

from commit_investigator.infra.ground_truth import GroundTruthGraph, CommitChain


@skip_no_data
class TestGroundTruthGraphReal:
    """Integration tests using real replication zip."""

    @pytest.fixture
    def graph(self) -> GroundTruthGraph:
        return GroundTruthGraph.from_replication_zip(
            "data/apachejit/apachejit_dataset_replication.zip"
        )

    def test_loads_projects(self, graph: GroundTruthGraph):
        assert len(graph.projects) >= 14
        assert "CAMEL" in graph.projects

    def test_total_bug_commits(self, graph: GroundTruthGraph):
        assert graph.total_bug_commits > 50000

    def test_get_fix_commits_known_bug(self, graph: GroundTruthGraph):
        # Known CAMEL bug commit from the data
        bug_hash = "a2fce2828a03e6c8bdad4c10e6363552a96c6206"
        fixes = graph.get_fix_commits(bug_hash)
        assert len(fixes) >= 1
        assert "e5c2985bf3814c05f2df5b86d710d981ed1bfafc" in fixes

    def test_get_bug_commits_known_fix(self, graph: GroundTruthGraph):
        fix_hash = "e5c2985bf3814c05f2df5b86d710d981ed1bfafc"
        bugs = graph.get_bug_commits(fix_hash)
        assert len(bugs) >= 1

    def test_get_issue_keys(self, graph: GroundTruthGraph):
        # Check that some commits have issue keys
        assert graph.total_issue_links > 40000

    def test_get_chain(self, graph: GroundTruthGraph):
        bug_hash = "a2fce2828a03e6c8bdad4c10e6363552a96c6206"
        chain = graph.get_chain(bug_hash)
        assert isinstance(chain, CommitChain)
        assert chain.bug_hash == bug_hash
        assert len(chain.fix_hashes) >= 1

    def test_unknown_commit_returns_empty(self, graph: GroundTruthGraph):
        fixes = graph.get_fix_commits("0000000000000000000000000000000000000000")
        assert fixes == []

    def test_has_bug(self, graph: GroundTruthGraph):
        assert graph.has_bug("a2fce2828a03e6c8bdad4c10e6363552a96c6206")
        assert not graph.has_bug("0000000000000000000000000000000000000000")
