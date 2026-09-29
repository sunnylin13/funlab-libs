"""
tests/test_prewarm.py
=====================

Unit tests for the simplified ``funlab.core.prewarm`` module.

Run with::

    cd funlab-libs
    python -m pytest tests/test_prewarm.py -v --tb=short
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List
from unittest.mock import MagicMock

import pytest

import funlab.core.prewarm as pw


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_registry():
    """Reset module state before every test to avoid cross-test pollution."""
    pw.reset()
    yield
    pw.reset()


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------

class TestRegister:

    def test_register_adds_entry(self):
        pw.register("test.entry", lambda: None)
        assert "test.entry" in pw._entries

    def test_register_stores_func(self):
        fn = lambda: None
        pw.register("test.fn", fn)
        assert pw._entries["test.fn"].func is fn

    def test_register_default_blocking_false(self):
        pw.register("test.bg", lambda: None)
        assert pw._entries["test.bg"].blocking is False

    def test_register_blocking_true(self):
        pw.register("test.blocking", lambda: None, blocking=True)
        assert pw._entries["test.blocking"].blocking is True

    def test_register_default_delay_zero(self):
        pw.register("test.nd", lambda: None)
        assert pw._entries["test.nd"].delay == 0.0

    def test_register_delay_stored(self):
        pw.register("test.delayed", lambda: None, delay=15.0)
        assert pw._entries["test.delayed"].delay == 15.0

    def test_duplicate_raises(self):
        pw.register("dup", lambda: None)
        with pytest.raises(ValueError, match="already registered"):
            pw.register("dup", lambda: None)

    def test_replace_overrides_existing(self):
        pw.register("rep", lambda: "first")
        pw.register("rep", lambda: "second", replace=True)
        assert pw._entries["rep"].func() == "second"

    def test_skip_if_exists_returns_quietly(self):
        pw.register("skip", lambda: "original")
        pw.register("skip", lambda: "intruder", skip_if_exists=True)
        assert pw._entries["skip"].func() == "original"

    def test_skip_if_exists_new_name_registers(self):
        pw.register("brand_new", lambda: None, skip_if_exists=True)
        assert "brand_new" in pw._entries

    def test_skip_if_exists_first_wins_priority(self):
        """Simulates two plugins registering the same shared resource."""
        pw.register("shared.exchange_cals", lambda: None, blocking=True)
        pw.register("shared.exchange_cals", lambda: None, blocking=False, skip_if_exists=True)
        # First registration (blocking=True) wins
        assert pw._entries["shared.exchange_cals"].blocking is True


# ---------------------------------------------------------------------------
# unregister()
# ---------------------------------------------------------------------------

class TestUnregister:

    def test_unregister_removes_entry(self):
        pw.register("x", lambda: None)
        pw.unregister("x")
        assert "x" not in pw._entries

    def test_unregister_nonexistent_is_noop(self):
        pw.unregister("nonexistent")  # must not raise


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

class TestRun:

    def test_run_executes_blocking_synchronously(self):
        out = []
        pw.register("sync", lambda: out.append(1), blocking=True)
        pw.run()
        assert out == [1]

    def test_run_executes_background_in_thread(self):
        out = []
        pw.register("bg", lambda: out.append(1))
        pw.run()
        deadline = time.monotonic() + 2.0
        while not out and time.monotonic() < deadline:
            time.sleep(0.01)
        assert out == [1]

    def test_run_called_twice_is_noop(self):
        """Second call to run() must not execute tasks again."""
        out = []
        pw.register("once", lambda: out.append(1), blocking=True)
        pw.run()
        pw.run()
        assert out == [1]   # exactly once

    def test_run_empty_registry_no_error(self):
        pw.run()  # must not raise

    def test_run_injects_app_when_func_accepts_arg(self):
        received = []

        def _warmup(app):
            received.append(app)

        pw.register("with_app", _warmup, blocking=True)
        fake_app = object()
        pw.run(app=fake_app)
        assert received == [fake_app]

    def test_run_no_injection_if_func_has_no_args(self):
        out = []
        pw.register("no_arg", lambda: out.append("ok"), blocking=True)
        pw.run(app=object())
        assert out == ["ok"]

    def test_run_blocking_then_background(self):
        """Blocking tasks finish before background tasks are spawned."""
        order = []
        pw.register("b", lambda: order.append("blocking"), blocking=True)
        pw.register("bg", lambda: time.sleep(0.01) or order.append("bg"))
        pw.run()
        # "blocking" must be present immediately after run() returns
        assert order[0] == "blocking"

    def test_delay_zero_runs_without_sleep(self):
        out = []
        pw.register("instant", lambda: out.append(1), blocking=True, delay=0.0)
        start = time.monotonic()
        pw.run()
        elapsed = time.monotonic() - start
        assert out == [1]
        assert elapsed < 1.0

    def test_delayed_background_sleeps_before_running(self):
        out = []
        pw.register("slow", lambda: out.append(1), delay=0.05)
        pw.run()
        deadline = time.monotonic() + 2.0
        while not out and time.monotonic() < deadline:
            time.sleep(0.01)
        assert out == [1]


# ---------------------------------------------------------------------------
# PW-2: resource_key dedup must prioritise blocking registrants
# ---------------------------------------------------------------------------

class TestResourceKeyBlockingPriority:
    """同 resource_key 多任務時 blocking 註冊者優先執行（PW-2 回歸）。"""

    def test_blocking_beats_earlier_background_registration(self):
        """背景先註冊搶 resource_key，blocking 後註冊（同 key）→ blocking 仍執行。"""
        pw.register("bg_first", lambda: None, resource_key="x")
        pw.register("blk_second", lambda: None, blocking=True, resource_key="x")
        pw.run()
        s = pw.status()
        assert s["blk_second"]["status"] in ("done", "running")
        assert s["bg_first"]["status"] == "skipped_shared"

    def test_two_blocking_same_key_first_registered_wins_with_warning(self, caplog):
        """兩個 blocking 同 key → 先註冊者執行、後者 skipped_shared + WARNING。"""
        out = []
        pw.register("blk_first", lambda: out.append("first"),
                    blocking=True, resource_key="x")
        pw.register("blk_second", lambda: out.append("second"),
                    blocking=True, resource_key="x")
        with caplog.at_level(logging.WARNING, logger=pw.__name__):
            pw.run()
        assert out == ["first"]  # 只有先註冊者實際執行
        s = pw.status()
        assert s["blk_first"]["status"] == "done"
        assert s["blk_second"]["status"] == "skipped_shared"
        # WARNING 必須含讓位者與佔資源者兩者名稱，供運維追溯
        warnings = [r for r in caplog.records
                    if r.levelno >= logging.WARNING and "blk_second" in r.getMessage()]
        assert warnings, "讓位的 blocking 任務應記 WARNING"
        assert "blk_first" in warnings[0].getMessage()

    def test_pure_background_same_key_first_registered_still_wins(self):
        """純背景同 key 行為不變：先到者赢。"""
        out = []
        ev = threading.Event()

        def _slow():
            out.append("bg_first")
            ev.set()
            time.sleep(0.05)

        pw.register("bg_first", _slow, resource_key="x")
        pw.register("bg_second", lambda: out.append("bg_second"), resource_key="x")
        pw.run()
        deadline = time.monotonic() + 2.0
        while not ev.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pw.status()["bg_first"]["status"] in ("running", "done")
        assert pw.status()["bg_second"]["status"] == "skipped_shared"
        assert "bg_second" not in out


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

class TestStatus:

    def test_status_pending_before_run(self):
        pw.register("s", lambda: None)
        s = pw.status()
        assert s["s"]["status"] == "pending"
        assert s["s"]["elapsed"] is None
        assert s["s"]["error"] is None

    def test_status_done_after_run(self):
        pw.register("s", lambda: None, blocking=True)
        pw.run()
        assert pw.status()["s"]["status"] == "done"

    def test_status_elapsed_recorded(self):
        pw.register("s", lambda: None, blocking=True)
        pw.run()
        assert pw.status()["s"]["elapsed"] is not None
        assert pw.status()["s"]["elapsed"] >= 0.0

    def test_status_failed_on_exception(self):
        pw.register("bad", lambda: 1 / 0, blocking=True)
        pw.run()
        s = pw.status()["bad"]
        assert s["status"] == "failed"
        assert s["error"] is not None

    def test_status_empty_dict_when_nothing_registered(self):
        assert pw.status() == {}


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------

class TestReset:

    def test_reset_clears_entries(self):
        pw.register("r", lambda: None)
        pw.reset()
        assert pw._entries == {}

    def test_reset_clears_run_guard(self):
        pw.register("r", lambda: None, blocking=True)
        pw.run()
        pw.reset()
        out = []
        pw.register("r2", lambda: out.append(1), blocking=True)
        pw.run()
        assert out == [1]


# ---------------------------------------------------------------------------
# register_prewarm() convenience helper
# ---------------------------------------------------------------------------

class TestRegisterPrewarmAlias:

    def test_register_prewarm_works(self):
        pw.register_prewarm("rp", lambda: None)
        assert "rp" in pw._entries

    def test_register_prewarm_skip_if_exists(self):
        pw.register_prewarm("rp2", lambda: "first")
        pw.register_prewarm("rp2", lambda: "second", skip_if_exists=True)
        assert pw._entries["rp2"].func() == "first"

    def test_register_prewarm_legacy_background_false_maps_to_blocking(self):
        """Legacy background=False should set blocking=True."""
        pw.register_prewarm("rp3", lambda: None, background=False)
        assert pw._entries["rp3"].blocking is True

    def test_register_prewarm_ignores_legacy_priority(self):
        """priority= is accepted without raising."""
        pw.register_prewarm("rp4", lambda: None, priority="HIGH")  # no error

    def test_register_prewarm_ignores_legacy_tags(self):
        pw.register_prewarm("rp5", lambda: None, tags=["a", "b"])

    def test_register_prewarm_ignores_depends_on(self):
        pw.register_prewarm("rp6", lambda: None, depends_on=["other"])


# ---------------------------------------------------------------------------
# deferred_import() / prewarm_task() decorator
# ---------------------------------------------------------------------------

class TestDeferredImportDecorator:

    def test_decorator_registers_function(self):
        @pw.deferred_import("deco.test")
        def _warmup():
            pass
        assert "deco.test" in pw._entries

    def test_decorator_returns_original_function(self):
        @pw.deferred_import("deco.ret")
        def _warmup():
            return 42
        assert _warmup() == 42

    def test_decorator_blocking(self):
        @pw.deferred_import("deco.blocking", blocking=True)
        def _warmup():
            pass
        assert pw._entries["deco.blocking"].blocking is True

    def test_decorator_delay(self):
        @pw.deferred_import("deco.delay", delay=20.0)
        def _warmup():
            pass
        assert pw._entries["deco.delay"].delay == 20.0

    def test_prewarm_task_alias_works(self):
        @pw.prewarm_task("alias.test")
        def _warmup():
            pass
        assert "alias.test" in pw._entries


# ---------------------------------------------------------------------------
# register_prewarm_tasks() Template Method
# ---------------------------------------------------------------------------

class TestRegisterPrewarmTasksTemplateMethod:

    def _get_base(self):
        try:
            from funlab.core.plugin import Plugin
            return Plugin
        except ImportError:
            pytest.skip("Plugin not importable in this env")

    def test_base_class_has_method(self):
        Base = self._get_base()
        assert callable(getattr(Base, "register_prewarm_tasks", None))

    def test_base_method_is_noop(self):
        Base = self._get_base()
        instance = object.__new__(Base)
        Base.register_prewarm_tasks(instance)  # must not raise

    def test_subclass_override_is_called(self):
        Base = self._get_base()
        called = []

        class My(Base):
            def register_prewarm_tasks(self):
                called.append(True)

        object.__new__(My).register_prewarm_tasks.__func__(object.__new__(My))
        # Call directly
        instance = object.__new__(My)
        My.register_prewarm_tasks(instance)
        assert called

    def test_subclass_can_call_register_from_hook(self):
        Base = self._get_base()

        class My(Base):
            def register_prewarm_tasks(self):
                pw.register("my.task", lambda: None)

        instance = object.__new__(My)
        My.register_prewarm_tasks(instance)
        assert "my.task" in pw._entries

    def test_shared_resource_with_skip_if_exists(self):
        """Two plugins each calling register_prewarm_tasks with same name."""
        Base = self._get_base()

        class PluginA(Base):
            def register_prewarm_tasks(self):
                pw.register("shared.cal", lambda: "owner", blocking=True,
                            skip_if_exists=True)

        class PluginB(Base):
            def register_prewarm_tasks(self):
                pw.register("shared.cal", lambda: "intruder", blocking=False,
                            skip_if_exists=True)

        object.__new__(PluginA).__class__.register_prewarm_tasks(object.__new__(PluginA))
        object.__new__(PluginB).__class__.register_prewarm_tasks(object.__new__(PluginB))

        assert pw._entries["shared.cal"].func() == "owner"
        assert pw._entries["shared.cal"].blocking is True


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:

    def test_concurrent_register_no_data_race(self):
        errors: List[Exception] = []

        def _reg(i):
            try:
                pw.register(f"t.{i}", lambda: None)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_reg, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(pw._entries) == 50


# ---------------------------------------------------------------------------
# _call() TypeError semantics (PW-1 regression)
# ---------------------------------------------------------------------------

class TestCallTypeSemantics:
    """PW-1: a TypeError raised *inside* func must NOT be misread as a
    signature mismatch — otherwise the func runs a second time and its
    side effects double (see ``_call`` docstring)."""

    def test_internal_typeerror_runs_func_exactly_once_and_marks_failed(self):
        side_effects: List[int] = []

        def _task():
            side_effects.append(1)
            raise TypeError("internal")

        pw.register("pw1.internal_typeerror", _task, blocking=True)
        pw.run()
        st = pw.status()["pw1.internal_typeerror"]
        assert len(side_effects) == 1
        assert st["status"] == "failed"
        assert "internal" in st["error"]

    def test_internal_typeerror_with_app_injection_runs_once(self):
        """func takes app and raises TypeError internally: still exactly one call."""
        side_effects: List[object] = []

        def _task(app):
            side_effects.append(app)
            raise TypeError("internal boom")

        pw.register("pw1.inject_typeerror", _task, blocking=True)
        fake_app = object()
        pw.run(app=fake_app)
        st = pw.status()["pw1.inject_typeerror"]
        assert len(side_effects) == 1
        assert side_effects[0] is fake_app
        assert st["status"] == "failed"
        assert "internal boom" in st["error"]

    def test_uninspectable_callable_falls_back_to_zero_arg(self):
        """Signature probing failure must still invoke the func zero-arg, once."""
        calls: List[tuple] = []

        class _NoSig:
            def __call__(self, *args):
                calls.append(args)

        def _raise_valueerror(func):  # emulate an undetectable signature
            raise ValueError("no signature")

        # Directly exercise the fallback branch via monkeypatched signature()
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(pw.inspect, "signature", _raise_valueerror)
        try:
            pw._call(_NoSig(), app=object())
        finally:
            monkeypatch.undo()
        assert calls == [()]


# ---------------------------------------------------------------------------
# PW-3: blocking tasks must ignore delay
# ---------------------------------------------------------------------------

class TestBlockingIgnoresDelay:
    """PW-3: ``delay`` only applies to background tasks.  A blocking task's
    contract is "complete before the app serves requests"; sleeping first
    would push initialisation past the first request (see ``_execute``)."""

    def test_blocking_with_delay_runs_immediately(self):
        pw.register("pw3.blocking_delayed", lambda: None,
                    blocking=True, delay=0.05)
        t0 = time.perf_counter()
        pw.run()
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.04, f"blocking task slept despite delay being ignored ({elapsed:.3f}s)"
        assert pw.status()["pw3.blocking_delayed"]["status"] == "done"

    def test_background_with_delay_still_sleeps(self):
        """Control: the delay sleep path must remain intact for background tasks."""
        started = threading.Event()

        def _task():
            started.set()

        pw.register("pw3.bg_delayed", _task, delay=0.3)
        pw.run()
        # func body must NOT be reached before the delay elapses (race-free:
        # 0.05 << 0.3 gives a wide margin)
        assert not started.wait(timeout=0.05), "background task skipped its delay sleep"
        assert started.wait(timeout=2.0), "background task never ran after delay"


# ---------------------------------------------------------------------------
# PW-4: late registrations (after run()) are flagged in status()
# ---------------------------------------------------------------------------

class TestLateFlag:
    """PW-4: a task registered *after* ``run()`` never executes; its status
    stays "pending" forever.  ``status()["late"]`` lets /health exclude these
    from degraded so lazily-loaded plugins don't pin the health light red."""

    def test_register_after_run_marks_late(self):
        pw.register("pw4.early", lambda: None, blocking=True)
        pw.run()
        pw.register("pw4.late", lambda: None)
        st = pw.status()["pw4.late"]
        assert st["late"] is True
        assert st["status"] == "pending"

    def test_register_before_run_not_late(self):
        pw.register("pw4.normal", lambda: None, blocking=True)
        pw.run()
        st = pw.status()["pw4.normal"]
        assert st["late"] is False
        assert st["status"] == "done"


# ---------------------------------------------------------------------------
# PW-5: service_connect budget breach logs at ERROR level
# ---------------------------------------------------------------------------

def _prewarm_log_records():
    """Return (records, handler) capturing everything ``pw._logger`` emits.

    ``funlab.utils.log.get_logger`` sets ``propagate = not has_real_handlers``,
    so records may never reach caplog's root handler — a direct handler
    capture is the robust way to assert levels.
    """
    records = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    return records, _Collect(level=logging.DEBUG)


class TestServiceConnectBudgetLog:
    """PW-5: service_connect tasks are a red line — they may only do short
    connectivity checks; a budget breach means long external I/O leaked onto
    the startup path and must be logged at ERROR, not WARNING."""

    def test_service_connect_budget_breach_logs_error(self):
        records, h = _prewarm_log_records()
        pw._logger.addHandler(h)
        try:
            pw.register("pw5.sc_slow", lambda: time.sleep(0.05),
                        blocking=True, category="service_connect", budget_sec=0)
            pw.run()
        finally:
            pw._logger.removeHandler(h)
        st = pw.status()["pw5.sc_slow"]
        assert st["budget_exceeded"] is True
        breach = [r for r in records if "exceeded budget" in r.getMessage()]
        assert breach, "no budget-exceeded log emitted"
        assert any(r.levelno == logging.ERROR for r in breach), \
            "service_connect budget breach must log at ERROR"

    def test_non_service_connect_budget_breach_stays_warning(self):
        records, h = _prewarm_log_records()
        pw._logger.addHandler(h)
        try:
            pw.register("pw5.import_slow", lambda: time.sleep(0.05),
                        blocking=True, category="import", budget_sec=0)
            pw.run()
        finally:
            pw._logger.removeHandler(h)
        assert pw.status()["pw5.import_slow"]["budget_exceeded"] is True
        breach = [r for r in records if "exceeded budget" in r.getMessage()]
        assert breach
        assert all(r.levelno == logging.WARNING for r in breach), \
            "non-service_connect breaches must stay WARNING"
