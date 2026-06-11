"""Tests for the archetype module: detect_archetype() and has_production_defect_signals()."""

import pytest

from commit_investigator.archetype import detect_archetype, has_production_defect_signals, is_message_only_diff
from commit_investigator.context_builder import InvestigationContext


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
        from commit_investigator.git_context import GitContextProvider

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
