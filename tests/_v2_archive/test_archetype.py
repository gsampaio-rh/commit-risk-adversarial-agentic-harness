"""Tests for the archetype module: detect_archetype() and has_production_defect_signals()."""

import pytest

from commit_investigator.analysis.archetype import detect_archetype, has_production_defect_signals, is_message_only_diff
from commit_investigator.context.context_builder import InvestigationContext


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _version_bump_context() -> InvestigationContext:
    return InvestigationContext(
        commit_id="9530370f7642a79b67f7bc4b999cfcae6c193305",
        project="camel",
        message="CAMEL-11268 Upgrade to Infinispan 9.x",
        diff=(
            "diff --git a/pom.xml b/pom.xml\n"
            "-    <version>8.2.0</version>\n"
            "+    <version>9.4.0</version>\n"
            "diff --git a/InfinispanProducer.java b/InfinispanProducer.java\n"
            "-import org.infinispan.commons.util.concurrent.NotifyingFuture;\n"
            "+import java.util.concurrent.CompletableFuture;\n"
        ),
        touched_files=["pom.xml", "InfinispanProducer.java"],
        csv_features={"la": 5.0},
        file_histories={},
        author_stats=None,
    )


def _generic_diff_context() -> InvestigationContext:
    return InvestigationContext(
        commit_id="aabbccdd1234",
        project="camel",
        message="Refactor service layer",
        diff="diff --git a/ServiceImpl.java\n+    return process(value);\n",
        touched_files=["ServiceImpl.java"],
        csv_features={},
        file_histories={},
        author_stats=None,
    )


# ---------------------------------------------------------------------------
# has_production_defect_signals
# ---------------------------------------------------------------------------

class TestHasProductionDefectSignals:
    def test_returns_false_for_clean_version_bump(self):
        assert has_production_defect_signals(_version_bump_context()) is False

    def test_returns_true_for_guard_removal(self):
        ctx = _version_bump_context()
        ctx.diff += "\n-        if (value != null) {\n-            return value;\n-        }\n"
        assert has_production_defect_signals(ctx) is True

    def test_returns_true_for_lifecycle_change(self):
        ctx = InvestigationContext(
            commit_id="fbf0ffad627b",
            project="camel",
            message="CAMEL-10279 fix lifecycle ordering",
            diff="-        if (started) {\n-            SmartLifecycle.stop();\n+        // removed guard\n",
            touched_files=["RoutesCollector.java"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert has_production_defect_signals(ctx) is True

    def test_returns_true_for_concurrency_removal(self):
        ctx = InvestigationContext(
            commit_id="cc112233",
            project="camel",
            message="Remove lock",
            diff="-        synchronized (this) {\n-            counter++;\n-        }\n+        counter++;\n",
            touched_files=["Counter.java"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert has_production_defect_signals(ctx) is True

    def test_returns_false_for_plain_return_statement(self):
        ctx = _version_bump_context()
        ctx.diff += "\n+        return future;\n"
        assert has_production_defect_signals(ctx) is False

    def test_returns_false_for_null_diff(self):
        ctx = _generic_diff_context()
        ctx.diff = None  # type: ignore[assignment]
        assert has_production_defect_signals(ctx) is False

    def test_returns_false_for_empty_diff(self):
        ctx = _generic_diff_context()
        ctx.diff = ""
        assert has_production_defect_signals(ctx) is False

    def test_jira_ticket_with_return_does_not_trigger(self):
        ctx = _version_bump_context()
        ctx.diff += "\n+        return future;\n"
        assert has_production_defect_signals(ctx) is False


# ---------------------------------------------------------------------------
# detect_archetype
# ---------------------------------------------------------------------------

class TestDetectArchetype:
    def test_version_bump_with_type_migration(self):
        assert detect_archetype(_version_bump_context()) is True

    def test_jetty_version_bump_compat_comment_removal(self):
        ctx = InvestigationContext(
            commit_id="b9f1653151e2",
            project="camel",
            message="CAMEL jetty upgrade",
            diff=(
                "-    <jetty9-version>9.3.14</jetty9-version>\n"
                "+    <jetty9-version>9.3.21</jetty9-version>\n"
                "-    <!-- binary incompatible above 9.3.15 -->\n"
            ),
            touched_files=["parent/pom.xml"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert detect_archetype(ctx) is True

    def test_label_rename(self):
        ctx = InvestigationContext(
            commit_id="labelrename01",
            project="camel",
            message="Rename log type constants",
            diff='-  "LogType": "BATCH"\n+  "LogType": "STREAM"\n',
            touched_files=["LogConfig.java"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert detect_archetype(ctx) is True

    def test_method_rename_without_defect_signals(self):
        ctx = InvestigationContext(
            commit_id="meth001",
            project="camel",
            message="Rename public API method",
            diff=(
                "-    public void doWork() {\n"
                "+    public void executeWork() {\n"
            ),
            touched_files=["Worker.java"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert detect_archetype(ctx) is True

    def test_generic_diff_not_archetype(self):
        assert detect_archetype(_generic_diff_context()) is False

    def test_version_bump_matches_archetype_regardless_of_guard_removal(self):
        """detect_archetype checks commit pattern only, NOT defect signals.

        The defect-signal gate lives in evaluate_risk() — it calls
        has_production_defect_signals() BEFORE calling detect_archetype,
        and skips the cap when defect signals are present.
        """
        ctx = _version_bump_context()
        ctx.diff += "\n-        if (value != null) {\n-            return value;\n-        }\n"
        assert detect_archetype(ctx) is True


class TestMessageOnlyDiff:
    def test_e0bb867_exception_message_only(self):
        from pathlib import Path
        from commit_investigator.context.git_context import GitContextProvider

        repo = Path("data/repos/hadoop")
        if not repo.exists():
            pytest.skip("hadoop repo not cloned")
        diff = GitContextProvider(repo).get_diff("e0bb867c3fa638c9f689ee0b044b400481cf02b5") or ""
        assert is_message_only_diff(diff) is True
        ctx = InvestigationContext(
            commit_id="e0bb867c3fa6",
            project="hadoop",
            message="Improve ApplicationNotFoundException message",
            diff=diff,
            touched_files=["ClientRMService.java"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert detect_archetype(ctx) is True

    def test_scheduler_logic_not_message_only(self):
        ctx = _generic_diff_context()
        ctx.diff = (
            "-        if (trigger.exists()) {\n"
            "+        if (existingTrigger != null) {\n"
            "+            scheduler.rescheduleJob(existingTrigger.getTriggerBuilder()...\n"
        )
        assert is_message_only_diff(ctx.diff) is False


# ---------------------------------------------------------------------------
# New archetype rules: test-file-only and null-check refactoring
# ---------------------------------------------------------------------------

class TestTestFileOnlyArchetype:
    """Test-only and example-only commits are clean archetypes."""

    def _test_only_ctx(self, touched_files: list[str]) -> InvestigationContext:
        return InvestigationContext(
            commit_id="test001",
            project="hadoop",
            message="Reduce test runtime",
            diff="+        int rsSize = 3;\n-        int rsSize = 6;\n",
            touched_files=touched_files,
            csv_features={},
            file_histories={},
            author_stats=None,
        )

    def test_all_test_files_returns_false_for_defect_signals(self):
        ctx = self._test_only_ctx([
            "hdfs/src/test/java/org/apache/hadoop/hdfs/TestWriteReadStripedFile.java",
            "hdfs/src/test/java/org/apache/hadoop/hdfs/StripedFileTestUtil.java",
        ])
        assert has_production_defect_signals(ctx) is False

    def test_all_test_files_returns_true_for_archetype(self):
        ctx = self._test_only_ctx([
            "hdfs/src/test/java/org/apache/hadoop/hdfs/TestWriteReadStripedFile.java",
            "hdfs/src/test/java/org/apache/hadoop/hdfs/StripedFileTestUtil.java",
        ])
        assert detect_archetype(ctx) is True

    def test_mixed_test_and_prod_not_test_only_archetype(self):
        """Mixed commits are NOT test-only; detect_archetype must check other signals."""
        ctx = self._test_only_ctx([
            "hdfs/src/main/java/org/apache/hadoop/hdfs/Impl.java",
            "hdfs/src/test/java/org/apache/hadoop/hdfs/TestImpl.java",
        ])
        # Diff has no signals, so no defect signal — but this is incidental.
        # The key assertion: mixed file list → NOT a test-only archetype.
        assert detect_archetype(ctx) is False

    def test_mixed_test_and_prod_with_guard_removal_fires_signal(self):
        """Mixed commits with real production guard removals must still fire defect signals."""
        ctx = InvestigationContext(
            commit_id="mixed001",
            project="hadoop",
            message="Refactor null handling",
            diff="-        if (value != null) {\n-            process(value);\n-        }\n+        process(value);\n",
            touched_files=[
                "hdfs/src/main/java/org/apache/hadoop/hdfs/Impl.java",
                "hdfs/src/test/java/org/apache/hadoop/hdfs/TestImpl.java",
            ],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert has_production_defect_signals(ctx) is True
        assert detect_archetype(ctx) is False

    def test_example_only_returns_false_for_defect_signals(self):
        ctx = InvestigationContext(
            commit_id="example001",
            project="camel",
            message="Add pojo example",
            diff="+# automatic shutdown after 60 messages\n+camel.springboot.duration-max-messages = 60\n",
            touched_files=[
                "examples/camel-example-spring-boot-pojo/README.adoc",
                "examples/camel-example-spring-boot-pojo/pom.xml",
                "examples/camel-example-spring-boot-pojo/src/main/java/sample/camel/Application.java",
            ],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert has_production_defect_signals(ctx) is False

    def test_example_only_returns_true_for_archetype(self):
        ctx = InvestigationContext(
            commit_id="example001",
            project="camel",
            message="Add pojo example",
            diff="+# automatic shutdown after 60 messages\n",
            touched_files=[
                "examples/camel-example-spring-boot-pojo/README.adoc",
                "examples/camel-example-spring-boot-pojo/pom.xml",
            ],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert detect_archetype(ctx) is True


class TestNetGuardRemoval:
    """Guard-removal ratio: refactoring vs genuine removal."""

    def test_net_removal_is_defect_signal(self):
        ctx = InvestigationContext(
            commit_id="guard001",
            project="camel",
            message="Remove null guard",
            diff="-        if (value != null) {\n-            process(value);\n-        }\n+        process(value);\n",
            touched_files=["ServiceImpl.java"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert has_production_defect_signals(ctx) is True

    def test_balanced_null_refactoring_not_defect_signal(self):
        """Reorganizing null checks (equal remove/add) is a refactoring, not guard removal."""
        ctx = InvestigationContext(
            commit_id="guard002",
            project="camel",
            message="Refactor null checks",
            diff=(
                "-                if (parent != null && parent.getSpan() != null) {\n"
                "-                if (managedSpan != null) {\n"
                "-            if (managedSpan.getSpan() != null) {\n"
                "+                    if (parent != null && parent.getSpan() != null) {\n"
                "+                    if (managedSpan != null) {\n"
                "+                if (managedSpan.getSpan() != null) {\n"
            ),
            touched_files=["OpenTracingTracer.java"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert has_production_defect_signals(ctx) is False

    def test_partial_guard_removal_is_still_defect_signal(self):
        """Removing guards with <75% replacement rate is a real guard removal."""
        ctx = InvestigationContext(
            commit_id="guard003",
            project="camel",
            message="Remove most null checks",
            diff=(
                "-        if (a != null) { process(a); }\n"
                "-        if (b != null) { process(b); }\n"
                "-        if (c != null) { process(c); }\n"
                "-        if (d != null) { process(d); }\n"
                "+        if (a != null) { process(a); }\n"  # only 1 of 4 preserved
            ),
            touched_files=["ServiceImpl.java"],
            csv_features={},
            file_histories={},
            author_stats=None,
        )
        assert has_production_defect_signals(ctx) is True
