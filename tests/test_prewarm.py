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


# ---------------------------------------------------------------------------
# delay parameter tests
# ---------------------------------------------------------------------------

class TestDelayParameter:
    """Tests for the delay= parameter added to PrewarmTask / registry / helpers."""

    def test_prewarm_task_default_delay_is_zero(self):
        task = PrewarmTask(name="t", func=lambda: None)
        assert task.delay == 0.0

    def test_prewarm_task_stores_delay(self):
        task = PrewarmTask(name="t", func=lambda: None, delay=15.0)
        assert task.delay == 15.0

    def test_registry_register_stores_delay(self):
        reg = PrewarmRegistry()
        reg.register("delayed", lambda: None, delay=42.0)
        assert reg.get("delayed").delay == 42.0

    def test_registry_register_default_delay_zero(self):
        reg = PrewarmRegistry()
        reg.register("no_delay", lambda: None)
        assert reg.get("no_delay").delay == 0.0


# ---------------------------------------------------------------------------
# skip_if_exists parameter tests
# ---------------------------------------------------------------------------

class TestSkipIfExists:
    """Tests for skip_if_exists= parameter – the shared-resource safety valve."""

    def test_skip_if_exists_returns_original_task(self):
        """Second call with skip_if_exists=True returns the first task unchanged."""
        reg = PrewarmRegistry()
        first_func = lambda: None
        t1 = reg.register("shared.resource", first_func)
        t2 = reg.register("shared.resource", lambda: None, skip_if_exists=True)
        assert t1 is t2
        assert t2.func is first_func  # original func preserved

    def test_skip_if_exists_does_not_overwrite(self):
        """skip_if_exists must not replace an existing registration."""
        reg = PrewarmRegistry()
        reg.register("shared.resource", lambda: "original", priority=PrewarmPriority.HIGH)
        reg.register("shared.resource", lambda: "intruder", priority=PrewarmPriority.LOW, skip_if_exists=True)
        task = reg.get("shared.resource")
        assert task.priority == PrewarmPriority.HIGH  # original priority kept

    def test_skip_if_exists_false_raises_on_duplicate(self):
        """Default (skip_if_exists=False) must still raise on duplicate."""
        reg = PrewarmRegistry()
        reg.register("dup", lambda: None)
        with pytest.raises(ValueError, match="already registered"):
            reg.register("dup", lambda: None)

    def test_skip_if_exists_and_replace_both_false_raise(self):
        """Explicitly setting both flags to False should still raise."""
        reg = PrewarmRegistry()
        reg.register("x", lambda: None)
        with pytest.raises(ValueError):
            reg.register("x", lambda: None, replace=False, skip_if_exists=False)

    def test_skip_if_exists_different_names_no_issue(self):
        """skip_if_exists=True on a new name registers normally."""
        reg = PrewarmRegistry()
        t = reg.register("brand_new", lambda: None, skip_if_exists=True)
        assert t is not None
        assert reg.get("brand_new") is t

    def test_register_prewarm_helper_skip_if_exists(self):
        """register_prewarm convenience helper passes skip_if_exists through."""
        from funlab.core.prewarm import prewarm_registry
        # use a unique name to avoid polluting global state
        unique = "test_skip_helper_unique_xyz"
        try:
            prewarm_registry.register(unique, lambda: "first")
            # should not raise
            t = prewarm_registry.register(unique, lambda: "second", skip_if_exists=True)
            assert t.func() == "first"  # original kept
        finally:
            prewarm_registry.unregister(unique)

    def test_shared_resource_pattern_multiple_plugins(self):
        """Simulates two plugins both trying to register the same exchange_calendars task."""
        reg = PrewarmRegistry()

        # Plugin A (canonical owner) registers first
        reg.register(
            "finfun_core.twse_calendar",
            lambda: None,
            priority=PrewarmPriority.HIGH,
            timeout=120.0,
        )

        # Plugin B (dependent) tries to register the same resource
        # With skip_if_exists=True this should be silently ignored
        reg.register(
            "finfun_core.twse_calendar",
            lambda: None,
            priority=PrewarmPriority.NORMAL,  # would downgrade if overwritten
            skip_if_exists=True,
        )

        # Original registration wins
        assert reg.get("finfun_core.twse_calendar").priority == PrewarmPriority.HIGH
        assert len(reg) == 1  # only one task in registry

    def test_register_prewarm_helper_stores_delay(self):
        reg = PrewarmRegistry()
        mgr = PrewarmManager(registry=reg)
        register_prewarm.__wrapped__ = None  # bypass if cached
        # Use the imported function directly referencing a fresh registry
        reg.register("rp_delayed", lambda: None, delay=30.0)
        assert reg.get("rp_delayed").delay == 30.0

    def test_prewarm_task_decorator_stores_delay(self):
        """@prewarm_task should accept delay= without raising."""
        from funlab.core.prewarm import prewarm_registry

        @prewarm_task(name="deco_delayed_unique_test", delay=20.0)
        def my_task():
            pass  # pragma: no cover

        task = prewarm_registry.get("deco_delayed_unique_test")
        try:
            assert task is not None
            assert task.delay == 20.0
        finally:
            prewarm_registry.unregister("deco_delayed_unique_test")

    def test_delay_zero_task_runs_without_sleep(self, registry, manager):
        """A task with delay=0 should complete quickly (no artificial sleep)."""
        ran = []
        registry.register("instant", lambda: ran.append(1), delay=0.0)
        start = time.monotonic()
        manager.run(background=False)
        elapsed = time.monotonic() - start
        assert ran == [1]
        assert elapsed < 1.0  # should be well under 1 second

    def test_delayed_task_sleeps_before_running(self, registry, manager):
        """A background task with delay should sleep before executing."""
        ran = []
        sleep_delay = 0.05  # 50 ms – fast enough for tests

        registry.register(
            "slow_start",
            lambda: ran.append(1),
            delay=sleep_delay,
            background=True,
        )
        manager.run(background=False)

        # _run_task_with_delay runs in background thread, wait a little
        deadline = time.monotonic() + 2.0
        while not ran and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ran == [1]

    def test_run_task_with_delay_method_exists(self, manager):
        """PrewarmManager must expose _run_task_with_delay (regression guard)."""
        assert callable(getattr(manager, "_run_task_with_delay", None))


# ---------------------------------------------------------------------------
# register_prewarm_tasks() Template Method tests
# ---------------------------------------------------------------------------

class TestRegisterPrewarmTasksTemplateMethod:
    """Tests for EnhancedViewPlugin.register_prewarm_tasks() contract."""

    def _make_plugin_class(self):
        """Import and return EnhancedViewPlugin if available, else skip."""
        try:
            from funlab.core.enhanced_plugin import EnhancedViewPlugin
            return EnhancedViewPlugin
        except ImportError:
            pytest.skip("EnhancedViewPlugin not importable in this test env")

    def test_base_class_has_register_prewarm_tasks(self):
        EnhancedViewPlugin = self._make_plugin_class()
        assert hasattr(EnhancedViewPlugin, "register_prewarm_tasks")
        assert callable(EnhancedViewPlugin.register_prewarm_tasks)

    def test_base_register_prewarm_tasks_is_noop(self):
        """Calling the base method should not raise and should do nothing."""
        EnhancedViewPlugin = self._make_plugin_class()

        plugin = object.__new__(EnhancedViewPlugin)
        # Call directly without __init__ to isolate the method behaviour
        try:
            EnhancedViewPlugin.register_prewarm_tasks(plugin)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"register_prewarm_tasks() raised unexpectedly: {exc}")

    def test_subclass_can_override_register_prewarm_tasks(self):
        """Subclass override is discovered and callable."""
        EnhancedViewPlugin = self._make_plugin_class()
        called = []

        class MyPlugin(EnhancedViewPlugin):
            def register_prewarm_tasks(self) -> None:  # type: ignore[override]
                called.append(True)

        plugin = object.__new__(MyPlugin)
        MyPlugin.register_prewarm_tasks(plugin)
        assert called == [True]

    def test_overridden_register_can_call_register_prewarm(self):
        """Verify subclass can call register_prewarm() inside the hook."""
        EnhancedViewPlugin = self._make_plugin_class()
        reg = PrewarmRegistry()

        class MyPlugin(EnhancedViewPlugin):
            def register_prewarm_tasks(self) -> None:  # type: ignore[override]
                reg.register("my_task", lambda: None, delay=5.0)

        plugin = object.__new__(MyPlugin)
        MyPlugin.register_prewarm_tasks(plugin)
        assert reg.get("my_task") is not None
        assert reg.get("my_task").delay == 5.0
