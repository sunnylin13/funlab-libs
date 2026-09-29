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
overriding :meth:`~funlab.core.plugin.Plugin.register_prewarm_tasks`.

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

    class MyPlugin(Plugin):

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
    category: str   = "import"  # import | service_connect | cache_build
    resource_key: Optional[str] = None  # For resource-level dedup
    owner:    Optional[str] = None  # Plugin name that registered this task
    budget_sec: Optional[float] = None  # SLO budget in seconds

    # Runtime (set by _execute)
    status:   str             = "pending"  # pending | running | done | failed | skipped_shared
    elapsed:  Optional[float] = None
    error:    Optional[str]   = None
    start_ts: Optional[float] = None  # When execution started (wall clock)
    end_ts:   Optional[float] = None  # When execution ended
    queue_delay: Optional[float] = None  # Seconds between run() call and execution start
    budget_exceeded: bool = False  # True if elapsed > budget_sec
    # PW-4: True 表示此任務是在 run() 之後才註冊（late），永遠不會執行，
    # status 會停留在 "pending"。健康檢查據此把 late-pending 排除在 degraded
    # 之外，否則 lazily-loaded plugin 會讓 /health 永久亮紅燈。
    late: bool = False


# Global: when run() was called, used to compute queue_delay
_run_start_time: Optional[float] = None

def register(
    name:           str,
    func:           Callable,
    *,
    blocking:       bool  = False,
    delay:          float = 0.0,
    skip_if_exists: bool  = False,
    replace:        bool  = False,
    category:       str   = "import",
    resource_key:   str | None = None,
    owner:          str | None = None,
    budget_sec:     float | None = None,
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
                      **僅對背景任務生效**（PW-3）：blocking 任務在 ``run()`` 內
                      同步執行，delay 一睡就違反「服務前必完成」承諾，故被忽略。
    skip_if_exists  : If the name is already registered, silently do nothing.
                      **Recommended for shared resources** (e.g. ``exchange_calendars``)
                      that multiple plugins may each try to register  only the
                      first registration wins.
    replace         : Silently overwrite an existing registration.  For tests /
                      hot-reload only; prefer ``skip_if_exists`` for shared resources.
    category        : Task category (import | service_connect | cache_build). Default "import".
    resource_key    : Optional key for resource-level dedup. Tasks with same
                      resource_key will only execute one; others skipped_shared.
                      同資源多任務時 **blocking 註冊者優先執行**（blocking 先於
                      背景任務佔資源；同 blocking 級內維持註冊序，先到者赢）。
    owner           : Plugin name that registered this task (for observability).
    budget_sec      : SLO budget in seconds. Used to detect budget_exceeded.

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
        # PW-4：run() 之後才註冊的任務永不執行 → 標記 late（健康檢查據此
        # 豁免 late-pending）。寫入在鎖內完成；skip_if_exists 略過路徑在上面
        # 已 return，不會走到這裡，因此「重複註冊被略過」不會被誤標 late。
        entry = _Entry(
            name=name, func=func, blocking=blocking, delay=delay,
            category=category, resource_key=resource_key, owner=owner,
            budget_sec=budget_sec, late=_run_called,
        )
        _entries[name] = entry
        run_already = entry.late
        _logger.debug("Registered deferred import %r (blocking=%s, delay=%.1fs, category=%s, resource_key=%s)",
                      name, blocking, delay, category, resource_key)

    if run_already:
        _logger.warning(
            "Deferred import %r registered AFTER prewarm.run() was called; "
            "it will never execute (typical for lazily-loaded plugins). "
            "Move registration into a startup-mode plugin if it matters.",
            name,
        )


def unregister(name: str) -> None:
    """Remove a registration (idempotent).  Primarily for tests."""
    with _lock:
        _entries.pop(name, None)


def run(app: Any = None) -> None:
    """Trigger all registered deferred imports.  Called **once** by app bootstrap.

    - ``blocking=True`` entries run synchronously in this call (before return).
    - ``blocking=False`` entries each get a daemon ``threading.Thread``.
    - Resource-level dedup: entries with same resource_key only one runs; others
      skipped_shared.  同資源多任務時 **blocking 註冊者優先執行**：走訪順序以
      blocking 優先，背景任務先註冊也不再搶佔資源，否則「服務前必完成」的
      blocking 承諾會被無聲破壞（PW-2 缺陷，探針實證）。

    Calling ``run()`` a second time is a no-op (guarded by ``_run_called``).
    """
    global _run_called, _run_start_time
    with _lock:
        if _run_called:
            _logger.debug("prewarm.run() called more than once  ignoring.")
            return
        _run_called = True
        _run_start_time = time.perf_counter()
        entries = list(_entries.values())

    if not entries:
        return

    # Phase 0: Resource-level dedup  only first task per resource_key runs.
    # PW-2: 走訪順序以 blocking 優先（穩定排序 → 同 blocking 級內維持註冊序）。
    # 為什麼：依純註冊序走訪時，背景任務先註冊會搶走 resource_key，讓同 key 的
    # blocking 任務被標 skipped_shared  —  「服務前必完成」承諾遭無聲破壞。
    # 後段仍維持「blocking 同步跑完 → 背景起線程」的兩階段結構，不受此排序影響。
    owners: dict[str, "_Entry"] = {}          # resource_key -> winning entry
    blocking   = []
    background = []
    for e in sorted(entries, key=lambda e: not e.blocking):
        if e.resource_key and e.resource_key in owners:
            e.status = "skipped_shared"
            winner = owners[e.resource_key]
            _logger.debug("Deferred import %r skipped (resource %r already being warmed)",
                          e.name, e.resource_key)
            if e.blocking:
                # 只可能讓給另一 blocking（blocking 先走訪）；讓位是重大語意
                # 事件，必須可觀察，故 WARNING 並含兩者名稱。
                _logger.warning(
                    "Blocking task %r yields resource %r to blocking task %r "
                    "(same resource_key registered earlier; %r was skipped_shared)",
                    e.name, e.resource_key, winner.name, e.name,
                )
            continue
        if e.resource_key:
            owners[e.resource_key] = e
        if e.blocking:
            blocking.append(e)
        else:
            background.append(e)

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
    Dict with structure:
    {
        "task_name": {
            "status": "done"|"failed"|"pending"|"running"|"skipped_shared",
            "category": "import"|"service_connect"|"cache_build",
            "resource_key": str | None,
            "owner": str | None,
            "elapsed": float | None,
            "queue_delay": float | None,
            "budget_sec": float | None,
            "budget_exceeded": bool,
            "late": bool,
            "error": str | None,
        }
    }

    ``late``（PW-4）：True 代表該任務在 ``run()`` 之後才註冊、永不執行，
    status 停留在 ``pending``。健康檢查應以 ``status == 'pending' and not late``
    判定 degraded，否則 lazily-loaded plugin 會讓 /health 永久亮紅燈。
    """
    with _lock:
        return {
            n: {
                "status": e.status,
                "category": e.category,
                "resource_key": e.resource_key,
                "owner": e.owner,
                "elapsed": e.elapsed,
                "queue_delay": e.queue_delay,
                "budget_sec": e.budget_sec,
                "budget_exceeded": e.budget_exceeded,
                "late": e.late,
                "error": e.error,
            }
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
    """Run *entry.func* (with optional delay), recording status and elapsed.

    PW-3：delay 僅對背景任務生效。blocking 任務在 ``run()`` 內同步執行，
    延遲它們等於把「服務前必完成」的初始化推遲到首個請求之後，語意上互相
    矛盾，故 blocking+d>0 時直接忽略 delay（記 debug log 保留可觀察性）。
    """
    if entry.delay > 0:
        if entry.blocking:
            _logger.debug("Deferred import %r  delay %.1fs ignored for blocking task",
                          entry.name, entry.delay)
        else:
            _logger.debug("Deferred import %r  sleeping %.1fs", entry.name, entry.delay)
            time.sleep(entry.delay)

    entry.status = "running"
    entry.start_ts = time.perf_counter()

    # Compute queue_delay: time from run() call to execution start
    if _run_start_time is not None:
        entry.queue_delay = entry.start_ts - _run_start_time

    t0 = time.perf_counter()
    try:
        _call(entry.func, app)
        entry.status = "done"
    except Exception as exc:
        entry.status = "failed"
        entry.error  = str(exc)
        _logger.warning("Deferred import %r failed: %s", entry.name, exc)
    finally:
        entry.end_ts = time.perf_counter()
        entry.elapsed = entry.end_ts - t0

        # Check SLO budget
        if entry.budget_sec is not None and entry.elapsed > entry.budget_sec:
            entry.budget_exceeded = True
            # PW-5：service_connect 紅線——service_connect 只准短連線檢查，
            # 長外部 I/O 一律走排程/專用重試管線。此類任務逾時代表紅線被
            # 踩破（啟動路徑被外部服務拖住），必須以 ERROR 升級告警；其餘
            # 類別逾時僅為 SLO 偏移，維持 WARNING。
            budget_logger = (_logger.error if entry.category == "service_connect"
                             else _logger.warning)
            budget_logger("Deferred import %r exceeded budget: %.3fs > %.1fs",
                          entry.name, entry.elapsed, entry.budget_sec)

        lvl = logging.INFO if entry.status == "done" else logging.WARNING
        _logger.log(lvl, "Deferred import %-35r  %-15s  %.3fs (queue_delay=%.1fs, budget=%.1fs)",
                    entry.name, entry.status, entry.elapsed, entry.queue_delay or 0, entry.budget_sec or 0)


def _call(func: Callable, app: Any) -> None:
    """Call *func*, injecting *app* if the function declares a positional parameter.

    注意（PW-1）：**實際呼叫不包 try**——func 內部 TypeError 必須原樣向傳，
    否則副作用任務會被重複執行。舊實作把整個「簽名探測＋呼叫」包在同一個
    ``try: ... except TypeError: func()`` 裡，一旦 func 本身拋出 TypeError，
    會被誤判為簽名不匹配而觸發第二次 zero-arg 呼叫（副作用加倍，金融場景後果可觀）。
    因此 try 只保護 ``inspect.signature`` 探測階段（不可探測的 callable 退回
    zero-arg 呼叫）；實際呼叫放在 try 之外，func 拋出的任何例外原樣向傳，
    由 :func:`_execute` 記為 failed。
    """
    args = ()
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
            args = (app,)
    except (TypeError, ValueError):
        pass  # 不可探測的 callable：退回 zero-arg 呼叫
    func(*args)


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
    category:       str   = "import",
    resource_key:   str | None = None,
    owner:          str | None = None,
    budget_sec:     float | None = None,
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
             skip_if_exists=skip_if_exists, replace=replace,
             category=category, resource_key=resource_key, owner=owner, budget_sec=budget_sec)


def deferred_import(
    name:     str,
    *,
    blocking: bool  = False,
    delay:    float = 0.0,
    category: str   = "import",
    resource_key: str | None = None,
    owner:    str | None = None,
    budget_sec: float | None = None,
) -> Callable:
    """Decorator form of :func:`register`.

    ::

        @deferred_import("finfun_core.twse_calendar", blocking=True)
        def _warmup_calendar():
            from finfun.utils.fin_cale import _ensure_calendar_registered
            _ensure_calendar_registered()

        # Low-urgency: start 30 s after app boot
        # （delay 僅對背景任務生效；blocking 任務會忽略 delay，見 register()）
        @deferred_import("finfun_quantanlys.numpy_pandas", delay=30.0)
        def _warmup_quant():
            import numpy   # noqa: F401
            import pandas  # noqa: F401
    """
    def _deco(func: Callable) -> Callable:
        register(name, func, blocking=blocking, delay=delay,
                 category=category, resource_key=resource_key, owner=owner, budget_sec=budget_sec)
        return func
    return _deco


# Legacy alias  keeps ``@prewarm_task(...)`` syntax working.
prewarm_task = deferred_import
