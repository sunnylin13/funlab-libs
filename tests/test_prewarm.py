"""
tests/test_prewarm.py
=====================

Comprehensive unit tests for ``funlab.core.prewarm``.

Run with::

    cd funlab-libs
    python -m pytest tests/test_prewarm.py -v --tb=short

Requirements: pytest (already in dev deps).  No Flask / DB / external network
access needed – these are pure-unit tests.
"""
from __future__ import annotations

import threading
import time
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from funlab.core.prewarm import (
    PrewarmManager,
    PrewarmPriority,
    PrewarmRegistry,
    PrewarmStatus,
    PrewarmTask,
    register_prewarm,
    prewarm_task,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def registry() -> PrewarmRegistry:
    """Fresh registry per test – avoids shared-state pollution."""
    return PrewarmRegistry()


@pytest.fixture()
def manager(registry: PrewarmRegistry) -> PrewarmManager:
    """Fresh manager backed by the per-test registry."""
    return PrewarmManager(registry=registry, max_workers=4)


# ---------------------------------------------------------------------------
# PrewarmTask tests
# ---------------------------------------------------------------------------

class TestPrewarmTask:

    def test_default_state(self):
        task = PrewarmTask(name="t", func=lambda: None)
        assert task.status == PrewarmStatus.PENDING
        assert task.elapsed is None
        assert task.error is None
        assert not task.is_done

    def test_is_done_on_success(self):
        task = PrewarmTask(name="t", func=lambda: None)
        task.status = PrewarmStatus.SUCCESS
        assert task.is_done

    def test_is_done_on_failed(self):
        task = PrewarmTask(name="t", func=lambda: None)
        task.status = PrewarmStatus.FAILED
        assert task.is_done

    def test_is_done_on_timeout(self):
        task = PrewarmTask(name="t", func=lambda: None)
        task.status = PrewarmStatus.TIMEOUT
        assert task.is_done

    def test_is_done_on_skipped(self):
        task = PrewarmTask(name="t", func=lambda: None)
        task.status = PrewarmStatus.SKIPPED
        assert task.is_done

    def test_reset_clears_runtime_fields(self):
        task = PrewarmTask(name="t", func=lambda: None)
        task.status  = PrewarmStatus.SUCCESS
        task.elapsed = 1.23
        task.error   = "some error"
        task.reset()
        assert task.status == PrewarmStatus.PENDING
        assert task.elapsed is None
        assert task.error is None
        assert not task.is_done

    def test_repr_contains_name(self):
        task = PrewarmTask(name="my_task", func=lambda: None)
        assert "my_task" in repr(task)


# ---------------------------------------------------------------------------
# PrewarmRegistry tests
# ---------------------------------------------------------------------------

class TestPrewarmRegistry:

    def test_register_and_get(self, registry):
        registry.register("foo", lambda: None)
        task = registry.get("foo")
        assert task is not None
        assert task.name == "foo"

    def test_register_returns_task(self, registry):
        task = registry.register("bar", lambda: None)
        assert isinstance(task, PrewarmTask)

    def test_duplicate_raises(self, registry):
        registry.register("dup", lambda: None)
        with pytest.raises(ValueError, match="already registered"):
            registry.register("dup", lambda: None)

    def test_duplicate_with_replace(self, registry):
        t1 = registry.register("dup", lambda: 1)
        t2 = registry.register("dup", lambda: 2, replace=True)
        assert registry.get("dup") is t2
        assert t1 is not t2

    def test_unregister(self, registry):
        registry.register("to_remove", lambda: None)
        registry.unregister("to_remove")
        assert registry.get("to_remove") is None

    def test_unregister_nonexistent_is_noop(self, registry):
        registry.unregister("ghost")  # should not raise

    def test_all_tasks_sorted_by_priority(self, registry):
        registry.register("low",    lambda: None, priority=PrewarmPriority.LOW)
        registry.register("high",   lambda: None, priority=PrewarmPriority.HIGH)
        registry.register("normal", lambda: None, priority=PrewarmPriority.NORMAL)
        tasks = registry.all_tasks()
        priorities = [t.priority.value for t in tasks]
        assert priorities == sorted(priorities, reverse=True)

    def test_all_tasks_filter_by_priority(self, registry):
        registry.register("h1", lambda: None, priority=PrewarmPriority.HIGH)
        registry.register("n1", lambda: None, priority=PrewarmPriority.NORMAL)
        high_tasks = registry.all_tasks(priority=PrewarmPriority.HIGH)
        assert all(t.priority == PrewarmPriority.HIGH for t in high_tasks)
        assert len(high_tasks) == 1

    def test_get_by_tags(self, registry):
        registry.register("t1", lambda: None, tags=["a", "b"])
        registry.register("t2", lambda: None, tags=["b", "c"])
        registry.register("t3", lambda: None, tags=["a"])
        result = registry.get_by_tags("a", "b")
        names = [t.name for t in result]
        assert "t1" in names
        assert "t2" not in names  # missing tag "a"
        assert "t3" not in names  # missing tag "b"

    def test_status_summary(self, registry):
        registry.register("s1", lambda: None, tags=["x"])
        summary = registry.status_summary()
        assert "s1" in summary
        assert summary["s1"]["status"] == PrewarmStatus.PENDING

    def test_len(self, registry):
        assert len(registry) == 0
        registry.register("a", lambda: None)
        assert len(registry) == 1
        registry.register("b", lambda: None)
        assert len(registry) == 2

    def test_critical_priority_forces_background_false(self, registry, caplog):
        """CRITICAL tasks with background=True should be promoted to blocking."""
        task = registry.register(
            "critical_bg", lambda: None,
            priority=PrewarmPriority.CRITICAL,
            background=True,   # intentionally wrong
        )
        assert task.background is False

    def test_thread_safety(self, registry):
        """Concurrent registrations should not raise and all tasks should be present."""
        errors: List[Exception] = []

        def _register(i):
            try:
                registry.register(f"task_{i}", lambda: None)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_register, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(registry) == 50


# ---------------------------------------------------------------------------
# PrewarmManager tests
# ---------------------------------------------------------------------------

class TestPrewarmManager:

    def test_run_sync_executes_all_tasks(self, manager, registry):
        results: List[str] = []
        registry.register("t1", lambda: results.append("t1"))
        registry.register("t2", lambda: results.append("t2"))
        manager.run(background=False)
        assert "t1" in results
        assert "t2" in results

    def test_task_status_success_after_run(self, manager, registry):
        registry.register("ok", lambda: None)
        manager.run(background=False)
        assert manager.get_task("ok").status == PrewarmStatus.SUCCESS

    def test_task_elapsed_recorded(self, manager, registry):
        registry.register("timed", lambda: time.sleep(0.01))
        manager.run(background=False)
        task = manager.get_task("timed")
        assert task.elapsed is not None
        assert task.elapsed >= 0.0

    def test_failing_task_status_failed(self, manager, registry):
        def _bad():
            raise RuntimeError("boom")

        registry.register("bad", _bad)
        manager.run(background=False)
        task = manager.get_task("bad")
        assert task.status == PrewarmStatus.FAILED
        assert "boom" in (task.error or "")

    def test_task_with_timeout_exceeded_status(self, manager, registry):
        def _slow():
            time.sleep(5.0)

        registry.register("slow", _slow, timeout=0.1)
        manager.run(background=False)
        task = manager.get_task("slow")
        assert task.status == PrewarmStatus.TIMEOUT

    def test_task_within_timeout_succeeds(self, manager, registry):
        registry.register("fast", lambda: time.sleep(0.01), timeout=5.0)
        manager.run(background=False)
        assert manager.get_task("fast").status == PrewarmStatus.SUCCESS

    def test_depends_on_success(self, manager, registry):
        order: List[str] = []
        registry.register("first",  lambda: order.append("first"),  priority=PrewarmPriority.HIGH)
        registry.register("second", lambda: order.append("second"), depends_on=["first"],
                          priority=PrewarmPriority.NORMAL)
        manager.run(background=False)
        assert order.index("first") < order.index("second")
        assert manager.get_task("second").status == PrewarmStatus.SUCCESS

    def test_depends_on_failed_skips_downstream(self, manager, registry):
        def _bad():
            raise RuntimeError("parent failed")

        registry.register("parent", _bad)
        registry.register("child",  lambda: None, depends_on=["parent"])
        manager.run(background=False)
        assert manager.get_task("parent").status == PrewarmStatus.FAILED
        assert manager.get_task("child").status == PrewarmStatus.SKIPPED

    def test_no_tasks_runs_cleanly(self, manager):
        # Should not raise; should log "No prewarm tasks registered"
        manager.run(background=False)  # no tasks in registry

    def test_run_called_twice_is_noop(self, manager, registry):
        counter = [0]

        def _inc():
            counter[0] += 1

        registry.register("once", _inc)
        manager.run(background=False)
        manager.run(background=False)  # second call should be no-op
        assert counter[0] == 1

    def test_task_receiving_app_arg(self, manager, registry):
        """Task with a single positional arg should receive the app object."""
        received = [None]

        def _needs_app(app):
            received[0] = app

        registry.register("needs_app", _needs_app)
        fake_app = MagicMock(name="FakeApp")
        manager.run(app=fake_app, background=False)
        assert received[0] is fake_app

    def test_task_no_arg(self, manager, registry):
        """Zero-arg task should not receive app."""
        received = [False]

        def _no_arg():
            received[0] = True

        registry.register("no_arg", _no_arg)
        fake_app = MagicMock(name="FakeApp")
        manager.run(app=fake_app, background=False)
        assert received[0] is True

    def test_background_mode_tasks_eventually_complete(self, manager, registry):
        """Background tasks should eventually gain SUCCESS status."""
        done = threading.Event()

        def _quick():
            time.sleep(0.05)

        registry.register("bg_task", _quick, priority=PrewarmPriority.HIGH, background=True)
        manager.run(background=True)
        completed = manager.wait(timeout=5.0, tasks=["bg_task"])
        assert completed
        assert manager.get_task("bg_task").status == PrewarmStatus.SUCCESS

    def test_status_returns_dict(self, manager, registry):
        registry.register("check", lambda: None)
        manager.run(background=False)
        summary = manager.status()
        assert isinstance(summary, dict)
        assert "check" in summary
        assert "status" in summary["check"]

    def test_critical_task_runs_sync_in_background_mode(self, manager, registry):
        """CRITICAL task must complete before run() returns even in background mode."""
        completed = [False]

        def _critical():
            completed[0] = True

        registry.register("crit", _critical, priority=PrewarmPriority.CRITICAL)
        manager.run(background=True)   # CRITICAL still runs synchronously
        assert completed[0] is True


# ---------------------------------------------------------------------------
# Convenience helpers tests
# ---------------------------------------------------------------------------

class TestConvenienceHelpers:

    def test_register_prewarm_adds_to_global_registry(self):
        """register_prewarm() touches the module-level singleton."""
        from funlab.core.prewarm import prewarm_registry as global_reg
        # Register with a unique name to avoid pollution between test runs
        name = f"__test_conv_{id(self)}__"
        try:
            register_prewarm(name=name, func=lambda: None, tags=["test"])
            assert global_reg.get(name) is not None
        finally:
            global_reg.unregister(name)

    def test_prewarm_task_decorator(self):
        """@prewarm_task decorator should register in the global registry."""
        from funlab.core.prewarm import prewarm_registry as global_reg
        name = f"__test_deco_{id(self)}__"
        try:
            @prewarm_task(name=name, tags=["test"])
            def _dummy():
                pass

            task = global_reg.get(name)
            assert task is not None
            assert task.func is _dummy
        finally:
            global_reg.unregister(name)

    def test_prewarm_task_decorator_preserves_func(self):
        """Decorator must return the original function unchanged."""
        from funlab.core.prewarm import prewarm_registry as global_reg
        name = f"__test_deco_func_{id(self)}__"
        try:
            @prewarm_task(name=name)
            def my_warmup():
                return 42

            assert my_warmup() == 42
        finally:
            global_reg.unregister(name)


# ---------------------------------------------------------------------------
# Module-level singleton import tests
# ---------------------------------------------------------------------------

class TestModuleSingletons:

    def test_prewarm_registry_importable(self):
        from funlab.core.prewarm import prewarm_registry
        assert isinstance(prewarm_registry, PrewarmRegistry)

    def test_prewarm_manager_importable(self):
        from funlab.core.prewarm import prewarm_manager
        assert isinstance(prewarm_manager, PrewarmManager)

    def test_lazy_import_via_core_init(self):
        """funlab.core.__getattr__ should lazily expose prewarm symbols."""
        import funlab.core as core
        pw = core.PrewarmPriority   # type: ignore[attr-defined]
        assert pw is PrewarmPriority

    def test_direct_import_prewarm_module(self):
        """Direct `from funlab.core.prewarm import X` must not error."""
        from funlab.core.prewarm import (  # noqa: F401
            PrewarmManager,
            PrewarmPriority,
            PrewarmRegistry,
            PrewarmStatus,
            PrewarmTask,
            prewarm_manager,
            prewarm_registry,
            prewarm_task,
            register_prewarm,
        )


# ---------------------------------------------------------------------------
# Edge-case / regression tests
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_depends_on(self, manager, registry):
        """Task with empty depends_on runs normally."""
        ran = [False]
        registry.register("edep", lambda: ran.__setitem__(0, True), depends_on=[])
        manager.run(background=False)
        assert ran[0]

    def test_unknown_dependency_logs_warning_but_runs(self, manager, registry, caplog):
        """Task depending on a non-existent task should still run (with warning)."""
        ran = [False]
        registry.register("ghost_dep", lambda: ran.__setitem__(0, True), depends_on=["non_existent"])
        with caplog.at_level("WARNING"):
            manager.run(background=False)
        assert ran[0]  # should still execute

    def test_task_with_none_timeout(self, manager, registry):
        """Tasks with timeout=None should run without timeout guard."""
        registry.register("no_timeout", lambda: time.sleep(0.01), timeout=None)
        manager.run(background=False)
        assert manager.get_task("no_timeout").status == PrewarmStatus.SUCCESS

    def test_multiple_tags(self, manager, registry):
        registry.register("multi", lambda: None, tags=["a", "b", "c"])
        task = registry.get("multi")
        assert set(task.tags) == {"a", "b", "c"}

    def test_description_stored(self, registry):
        registry.register("desc_test", lambda: None, description="My warm-up doc")
        t = registry.get("desc_test")
        assert t.description == "My warm-up doc"

    def test_priority_ordering_with_equal_values(self, registry):
        """Tasks of equal priority should all appear in sorted output, regardless of order."""
        for i in range(5):
            registry.register(f"norm_{i}", lambda: None, priority=PrewarmPriority.NORMAL)
        tasks = registry.all_tasks(priority=PrewarmPriority.NORMAL)
        assert len(tasks) == 5
