"""
funlab.core.prewarm
===================

**Deferred-import registry** for Funlab/Finfun applications.

Purpose
-------
Move expensive one-time initialisations (heavy C-extension imports, calendar
registration, DB-engine warm-up, SDK connects ) **off** the first HTTP request
path by executing them in daemon background threads immediately after app startup.

Design principle  **Framework only, no task definitions here**
--------------------------------------------------------------
This module is a *pure infrastructure layer*.  It must never contain concrete
warm-up registrations.  Each plugin owns its own tasks and registers them by
overriding :meth:`~funlab.core.enhanced_plugin.EnhancedViewPlugin.register_prewarm_tasks`.

Architecture (simplified)
--------------------------
The entire mechanism is three module-level functions over a plain ``dict``::

    register(name, func, ...)    plugins call this (via register_prewarm_tasks)
    run(app)                     app bootstrap calls once (via _run_prewarm)
    status()                     observability / health-check

There is **no** scheduler class, **no** priority enum, **no** depends-on graph,
**no** ThreadPoolExecutor.  Each registered callable gets one daemon ``Thread``.
This is intentional: WSGI apps are I/O-bound and the overhead of a thread per
import is negligible compared to the import times involved (seconds to minutes).

Typical Usage (in a plugin''s ``register_prewarm_tasks``)
---------------------------------------------------------
::

    from funlab.core.prewarm import register_prewarm

    class MyPlugin(EnhancedViewPlugin):

        def register_prewarm_tasks(self) -> None:
            register_prewarm(
                "finfun_core.twse_calendar",
                self._warmup_calendar,
                skip_if_exists=True,   # shared resource: first registrant wins
            )

        @staticmethod
        def _warmup_calendar() -> None:
            from finfun.utils.fin_cale import _ensure_calendar_registered
            _ensure_calendar_registered()

Relationship to the Hook mechanism
-----------------------------------
``HookManager`` / ``plugin_after_start`` etc. are **event broadcast** channels
(observer pattern  "notify me when X happens").  Prewarm is **task execution**
("do Y in the background once").  They are complementary, not overlapping.
"""
from __future__ import annotations

import inspect
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from funlab.utils import log

_logger = log.get_logger(__name__)
_lock   = threading.Lock()

# ---------------------------------------------------------------------------
# Internal state  (module-level dict  no Registry class needed)
# ---------------------------------------------------------------------------

_entries: Dict[str, "_Entry"] = {}
_run_called: bool = False   # guard against double-run


@dataclass
class _Entry:
    """Internal record for a single deferred import.  Not part of public API."""
    name:     str
    func:     Callable
    blocking: bool  = False   # True  run synchronously before app serves first request
    delay:    float = 0.0     # seconds to sleep after run() before starting

    # Runtime (set by _execute)
    status:   str             = "pending"  # pending | running | done | failed
    elapsed:  Optional[float] = None
    error:    Optional[str]   = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register(
    name:           str,
    func:           Callable,
    *,
    blocking:       bool  = False,
    delay:          float = 0.0,
    skip_if_exists: bool  = False,
    replace:        bool  = False,
) -> None:
    """Register a deferred import callable.

    Parameters
    ----------
    name            : Globally unique identifier.  **Convention**: ``"{plugin}.{task}"``
                      (e.g. ``"finfun_core.twse_calendar"``).
    func            : Zero-argument callable, or single-argument callable that
                      accepts ``app`` (the Flask app instance).
    blocking        : If ``True``, this import **must** complete before the app
                      begins serving requests (run synchronously in ``run()``).
                      Default ``False``  runs in a daemon thread.
    delay           : Seconds to sleep after ``run()`` is invoked before this
                      task starts.  Use for low-urgency tasks that should not
                      compete with blocking tasks at t=0.
    skip_if_exists  : If the name is already registered, silently do nothing.
                      **Recommended for shared resources** (e.g. ``exchange_calendars``)
                      that multiple plugins may each try to register  only the
                      first registration wins.
    replace         : Silently overwrite an existing registration.  For tests /
                      hot-reload only; prefer ``skip_if_exists`` for shared resources.

    Raises
    ------
    ValueError  : If the name is already registered and neither ``skip_if_exists``
                  nor ``replace`` is set.
    """
    with _lock:
        if name in _entries:
            if skip_if_exists:
                _logger.debug("Deferred import %r already registered  skipped.", name)
                return
            if not replace:
                raise ValueError(
                    f"Deferred import {name!r} already registered. "
                    "Use skip_if_exists=True (shared resources) or replace=True (tests)."
                )
        _entries[name] = _Entry(
            name=name, func=func, blocking=blocking, delay=delay
        )
        _logger.debug("Registered deferred import %r (blocking=%s, delay=%.1fs)",
                      name, blocking, delay)


def unregister(name: str) -> None:
    """Remove a registration (idempotent).  Primarily for tests."""
    with _lock:
        _entries.pop(name, None)


def run(app: Any = None) -> None:
    """Trigger all registered deferred imports.  Called **once** by app bootstrap.

    - ``blocking=True`` entries run synchronously in this call (before return).
    - ``blocking=False`` entries each get a daemon ``threading.Thread``.

    Calling ``run()`` a second time is a no-op (guarded by ``_run_called``).
    """
    global _run_called
    with _lock:
        if _run_called:
            _logger.debug("prewarm.run() called more than once  ignoring.")
            return
        _run_called = True
        entries = list(_entries.values())

    if not entries:
        return

    blocking   = [e for e in entries if e.blocking]
    background = [e for e in entries if not e.blocking]

    for entry in blocking:
        _execute(entry, app)

    for entry in background:
        threading.Thread(
            target=_execute,
            args=(entry, app),
            daemon=True,
            name=f"prewarm-{entry.name}",
        ).start()


def status() -> Dict[str, Dict[str, Any]]:
    """Return a snapshot of all registered entries and their runtime status.

    Returns
    -------
    ``{name: {"status": str, "elapsed": float|None, "error": str|None}}``
    """
    with _lock:
        return {
            n: {"status": e.status, "elapsed": e.elapsed, "error": e.error}
            for n, e in _entries.items()
        }


def reset() -> None:
    """Clear all registrations and reset run-guard.  **Tests only.**"""
    global _run_called
    with _lock:
        _entries.clear()
        _run_called = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _execute(entry: _Entry, app: Any) -> None:
    """Run *entry.func* (with optional delay), recording status and elapsed."""
    if entry.delay > 0:
        _logger.debug("Deferred import %r  sleeping %.1fs", entry.name, entry.delay)
        time.sleep(entry.delay)

    entry.status = "running"
    t0 = time.perf_counter()
    try:
        _call(entry.func, app)
        entry.status = "done"
    except Exception as exc:
        entry.status = "failed"
        entry.error  = str(exc)
        _logger.warning("Deferred import %r failed: %s", entry.name, exc)
    finally:
        entry.elapsed = time.perf_counter() - t0
        lvl = logging.INFO if entry.status == "done" else logging.WARNING
        _logger.log(lvl, "Deferred import %-35r  %-7s  %.3fs",
                    entry.name, entry.status, entry.elapsed)


def _call(func: Callable, app: Any) -> None:
    """Call *func*, injecting *app* if the function declares a positional parameter."""
    try:
        sig        = inspect.signature(func)
        positional = [
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        if positional and app is not None:
            func(app)
        else:
            func()
    except TypeError:
        func()


# ---------------------------------------------------------------------------
# Convenience helpers (the primary API for plugin authors)
# ---------------------------------------------------------------------------

def register_prewarm(
    name:           str,
    func:           Callable,
    *,
    blocking:       bool  = False,
    delay:          float = 0.0,
    skip_if_exists: bool  = False,
    replace:        bool  = False,
    # Legacy keyword arguments  accepted but silently ignored so that
    # existing call-sites don''t break during migration.
    priority=None, timeout=None, background=None,
    tags=None, depends_on=None, description: str = "",
) -> None:
    """Convenience alias for :func:`register`.

    ``priority``, ``timeout``, ``tags``, ``depends_on``, ``description`` are
    accepted for backward compatibility but **have no effect**.
    Use ``blocking=True`` instead of ``priority=PrewarmPriority.CRITICAL``.
    """
    # Map legacy `background=False`  `blocking=True`
    if background is not None and not background:
        blocking = True
    register(name, func, blocking=blocking, delay=delay,
             skip_if_exists=skip_if_exists, replace=replace)


def deferred_import(
    name:     str,
    *,
    blocking: bool  = False,
    delay:    float = 0.0,
) -> Callable:
    """Decorator form of :func:`register`.

    ::

        @deferred_import("finfun_core.twse_calendar", blocking=True)
        def _warmup_calendar():
            from finfun.utils.fin_cale import _ensure_calendar_registered
            _ensure_calendar_registered()

        # Low-urgency: start 30 s after app boot
        @deferred_import("finfun_quantanlys.numpy_pandas", delay=30.0)
        def _warmup_quant():
            import numpy   # noqa: F401
            import pandas  # noqa: F401
    """
    def _deco(func: Callable) -> Callable:
        register(name, func, blocking=blocking, delay=delay)
        return func
    return _deco


# Legacy alias  keeps ``@prewarm_task(...)`` syntax working.
prewarm_task = deferred_import
