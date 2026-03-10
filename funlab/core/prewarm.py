"""
funlab.core.prewarm
===================

Centralised **pre-warm framework** for Funlab/Finfun applications.

Goals
-----
- Move expensive one-time initialisation (heavy imports, calendar registration,
  DB-engine warm-up, quote-agent connects …) **off** the first HTTP request path.
- Provide each plugin with a **standard interface** to register its warm-up tasks
  without owning the scheduling logic.
- Support *blocking* (app-ready gate), *background* (fire-and-forget at startup),
  *delayed* (start after N seconds), and *deferred* (on-demand) execution strategies.
- Centralise metrics / observability so every task reports elapsed time & status.

Design principle – **Framework only, no task definitions here**
--------------------------------------------------------------
This module is a **pure infrastructure layer**.  It must never contain concrete
warm-up task registrations.  Each plugin owns its own tasks and registers them
via ``register_prewarm()`` / ``@prewarm_task`` in its own ``__init__.py`` or
by overriding :meth:`EnhancedViewPlugin.register_prewarm_tasks`.

Typical Usage (in a plugin ``__init__``)
-----------------------------------------
::

    from funlab.core.prewarm import prewarm_registry, PrewarmPriority

    def _warmup_calendar():
        from finfun.utils.fin_cale import _ensure_calendar_registered
        _ensure_calendar_registered()

    prewarm_registry.register(
        name="twse_calendar",
        func=_warmup_calendar,
        priority=PrewarmPriority.HIGH,
        timeout=120.0,
        tags=["calendar", "finfun-core"],
    )

Typical Usage (in app bootstrap)
---------------------------------
::

    from funlab.core.prewarm import prewarm_manager
    prewarm_manager.run(app, background=True)   # non-blocking; most common
    # or
    prewarm_manager.run(app, background=False)  # blocking; wait until all HIGH+ are done
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from funlab.utils import log


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

class PrewarmPriority(IntEnum):
    """Task priority – higher value = executed earlier (and possibly blocking).

    CRITICAL  Run before app starts accepting requests (**blocking**).
    HIGH      Background, started immediately at app startup.
    NORMAL    Background, started shortly after HIGH tasks complete.
    LOW       Background, deferred – nice-to-have; started last.
    """
    CRITICAL = 40
    HIGH = 30
    NORMAL = 20
    LOW = 10


class PrewarmStatus(str):
    """Status string constants – no Enum overhead; usable as dict key."""
    PENDING   = "pending"
    RUNNING   = "running"
    SUCCESS   = "success"
    FAILED    = "failed"
    SKIPPED   = "skipped"
    TIMEOUT   = "timeout"


# ---------------------------------------------------------------------------
# PrewarmTask dataclass
# ---------------------------------------------------------------------------

@dataclass
class PrewarmTask:
    """Describes a single pre-warm task.

    Attributes
    ----------
    name:       Unique task identifier (must be globally unique).
    func:       Zero-argument callable that performs the warm-up.
    priority:   Execution order / blocking behaviour.
    timeout:    Max seconds to wait before marking the task as *timeout*.
                ``None`` means no limit (not recommended for CRITICAL tasks).
    background: Override – force background even if priority == CRITICAL.
    tags:       Free-form labels for grouping / filtering.
    depends_on: Names of tasks that must finish (SUCCESS) before this one starts.
    description: Human-readable description; used in docs/logs.

    Runtime fields (set by PrewarmManager)
    ---------------------------------------
    status, elapsed, error, started_at, finished_at
    """
    name:        str
    func:        Callable[[], Any]
    priority:    PrewarmPriority = PrewarmPriority.NORMAL
    timeout:     Optional[float] = 60.0
    background:  bool = True
    tags:        List[str] = field(default_factory=list)
    depends_on:  List[str] = field(default_factory=list)
    description: str = ""
    delay:       float = 0.0
    """Seconds to wait *after* app startup before starting this task.

    Useful for non-urgent tasks (e.g. pre-importing heavy analytics modules)
    that should not compete with critical warm-up tasks at t=0.
    Set ``delay=30`` to start 30 s after :meth:`PrewarmManager.run` is called.
    """

    # ---- runtime state (mutated by PrewarmManager) ----
    status:      str = field(default=PrewarmStatus.PENDING, init=False)
    elapsed:     Optional[float] = field(default=None, init=False)
    error:       Optional[str] = field(default=None, init=False)
    started_at:  Optional[float] = field(default=None, init=False)
    finished_at: Optional[float] = field(default=None, init=False)

    def reset(self) -> None:
        """Reset runtime state so the task can be re-executed."""
        self.status     = PrewarmStatus.PENDING
        self.elapsed    = None
        self.error      = None
        self.started_at = None
        self.finished_at = None

    @property
    def is_done(self) -> bool:
        return self.status in (PrewarmStatus.SUCCESS, PrewarmStatus.FAILED,
                               PrewarmStatus.SKIPPED, PrewarmStatus.TIMEOUT)

    def __repr__(self) -> str:
        return (f"<PrewarmTask name={self.name!r} priority={self.priority.name} "
                f"status={self.status!r} elapsed={self.elapsed}>")


# ---------------------------------------------------------------------------
# PrewarmRegistry – global task store
# ---------------------------------------------------------------------------

class PrewarmRegistry:
    """Thread-safe global registry of :class:`PrewarmTask` objects.

    Plugins **register** tasks here; :class:`PrewarmManager` **retrieves** them.
    Has a module-level singleton ``prewarm_registry`` for convenience.
    """

    def __init__(self) -> None:
        self._lock  = threading.RLock()
        self._tasks: Dict[str, PrewarmTask] = {}
        self._logger = log.get_logger(__name__, level=logging.INFO)

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def register(
        self,
        name:        str,
        func:        Callable[[], Any],
        priority:    PrewarmPriority = PrewarmPriority.NORMAL,
        timeout:     Optional[float] = 60.0,
        background:  bool = True,
        tags:        Optional[List[str]] = None,
        depends_on:  Optional[List[str]] = None,
        description: str = "",
        replace:     bool = False,
        delay:       float = 0.0,
    ) -> "PrewarmTask":
        """Register a warm-up task.

        Parameters
        ----------
        name      : Unique identifier (duplicate raises ``ValueError`` unless *replace=True*).
        func      : Zero-argument callable (or single-arg accepting *app*).
        priority  : :class:`PrewarmPriority`.
        timeout   : Seconds before the task is considered *timed out*.
        background: If *True* the task runs in a daemon thread.
                    CRITICAL tasks default to blocking (``background=False``).
        tags      : Optional list of string labels.
        depends_on: List of task names that must succeed first.
        description: Human-readable summary.
        replace   : If *True* an existing registration with the same *name*
                    is silently replaced (useful in tests / hot-reload).
        delay     : Seconds to sleep *after* ``PrewarmManager.run()`` is called
                    before this task starts.  Useful for low-urgency modules
                    that should not compete with critical tasks at t=0.

        Returns
        -------
        The registered :class:`PrewarmTask` instance.
        """
        with self._lock:
            if name in self._tasks and not replace:
                raise ValueError(
                    f"PrewarmTask {name!r} already registered. "
                    "Use replace=True to overwrite."
                )
            # CRITICAL tasks are blocking by default unless caller explicitly opts out
            if priority == PrewarmPriority.CRITICAL and background:
                self._logger.warning(
                    "Task %r has CRITICAL priority but background=True – "
                    "it will be promoted to blocking.", name
                )
                background = False

            task = PrewarmTask(
                name=name,
                func=func,
                priority=priority,
                timeout=timeout,
                background=background,
                tags=list(tags or []),
                depends_on=list(depends_on or []),
                description=description,
                delay=delay,
            )
            self._tasks[name] = task
            self._logger.debug("Registered prewarm task %r (priority=%s, delay=%.1fs)", name, priority.name, delay)
            return task

    def unregister(self, name: str) -> None:
        """Remove a task from the registry (idempotent)."""
        with self._lock:
            self._tasks.pop(name, None)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[PrewarmTask]:
        with self._lock:
            return self._tasks.get(name)

    def get_by_tags(self, *tags: str) -> List[PrewarmTask]:
        """Return tasks that have ALL of the supplied tags."""
        tag_set = set(tags)
        with self._lock:
            return [t for t in self._tasks.values() if tag_set.issubset(set(t.tags))]

    def all_tasks(self, priority: Optional[PrewarmPriority] = None) -> List[PrewarmTask]:
        """Return tasks sorted by priority (descending).

        Parameters
        ----------
        priority : If given, return only tasks whose priority == *priority*.
        """
        with self._lock:
            tasks = list(self._tasks.values())
        if priority is not None:
            tasks = [t for t in tasks if t.priority == priority]
        return sorted(tasks, key=lambda t: t.priority.value, reverse=True)

    def status_summary(self) -> Dict[str, Any]:
        """Return a JSON-serialisable status snapshot for all tasks."""
        with self._lock:
            return {
                name: {
                    "status":    task.status,
                    "priority":  task.priority.name,
                    "elapsed":   round(task.elapsed, 3) if task.elapsed else None,
                    "error":     task.error,
                    "tags":      task.tags,
                }
                for name, task in self._tasks.items()
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)

    def __repr__(self) -> str:
        return f"<PrewarmRegistry tasks={len(self)}>"


# ---------------------------------------------------------------------------
# PrewarmManager – orchestrates execution
# ---------------------------------------------------------------------------

class PrewarmManager:
    """Orchestrates execution of tasks registered in a :class:`PrewarmRegistry`.

    Lifecycle
    ---------
    1. ``run(app)``  is called once by the app bootstrap (after ``register_plugins``).
    2. CRITICAL tasks are run **synchronously** (blocking); app waits.
    3. HIGH / NORMAL / LOW tasks are dispatched to a ``ThreadPoolExecutor`` and
       run in daemon threads (non-blocking by default).
    4. ``wait(timeout)`` can be used by tests / healthchecks to confirm all tasks
       have finished.

    Thread safety
    -------------
    The manager is designed to be called from a single thread (bootstrap) and
    then queried from multiple threads.  Internal ``_futures`` dict is protected
    by a lock.
    """

    def __init__(
        self,
        registry:  Optional[PrewarmRegistry] = None,
        max_workers: int = 4,
    ) -> None:
        # NOTE: use `is not None` – an empty PrewarmRegistry has __len__==0
        # and would be falsy in a bare `registry or prewarm_registry` expression.
        self._registry   = registry if registry is not None else prewarm_registry
        self._max_workers = max_workers
        self._lock       = threading.RLock()
        self._futures:   Dict[str, Future] = {}
        self._executor:  Optional[ThreadPoolExecutor] = None
        self._logger     = log.get_logger(__name__, level=logging.INFO)
        self._run_called = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, app: Any = None, background: bool = True) -> None:
        """Execute all registered tasks.

        Parameters
        ----------
        app:        The Flask/Funlab application (passed to task func if it
                    accepts a single argument).
        background: Master switch.
                    - ``True``  → CRITICAL tasks block; others are threaded.
                    - ``False`` → ALL tasks run synchronously, blocking the caller.
                    Useful for ``background=False`` in unit tests.
        """
        if self._run_called:
            self._logger.warning("PrewarmManager.run() called more than once – ignoring.")
            return
        self._run_called = True

        tasks = self._registry.all_tasks()
        if not tasks:
            self._logger.info("No prewarm tasks registered.")
            return

        self._logger.info(
            "Starting prewarm: %d task(s) registered – background=%s", len(tasks), background
        )

        if not background:
            # Fully synchronous – useful in tests and CLI tools
            for task in tasks:
                if task.delay:
                    time.sleep(task.delay)
                self._run_task_sync(task, app)
            return

        # Separate blocking CRITICAL from threaded rest
        critical = [t for t in tasks if t.priority == PrewarmPriority.CRITICAL and not t.background]
        others   = [t for t in tasks if not (t.priority == PrewarmPriority.CRITICAL and not t.background)]

        # 1. Run CRITICAL tasks synchronously (blocks until done)
        for task in critical:
            self._run_task_sync(task, app)

        # 2. Run remaining tasks in thread pool
        if others:
            self._executor = ThreadPoolExecutor(
                max_workers=min(self._max_workers, len(others)),
                thread_name_prefix="prewarm",
            )
            for task in others:
                future = self._executor.submit(self._run_task_with_delay, task, app)
                with self._lock:
                    self._futures[task.name] = future

            # Shutdown pool in background so we don't block Flask startup
            def _shutdown():
                self._executor.shutdown(wait=True)
                self._logger.info("All background prewarm tasks complete.")
            threading.Thread(target=_shutdown, name="prewarm-shutdown", daemon=True).start()

    def wait(self, timeout: Optional[float] = None, tasks: Optional[Sequence[str]] = None) -> bool:
        """Wait until all (or selected) tasks finish.

        Returns *True* if all finished within *timeout*, *False* on timeout.
        """
        deadline = (time.monotonic() + timeout) if timeout else None
        names = tasks or list(self._futures.keys())
        for name in names:
            with self._lock:
                future = self._futures.get(name)
            if future is None:
                continue
            remaining = max(0.0, deadline - time.monotonic()) if deadline else None
            try:
                future.result(timeout=remaining)
            except Exception:
                pass  # errors already recorded on the task
            if deadline and time.monotonic() >= deadline:
                return False
        return True

    def status(self) -> Dict[str, Any]:
        """Proxy to registry's :meth:`~PrewarmRegistry.status_summary`."""
        return self._registry.status_summary()

    def get_task(self, name: str) -> Optional[PrewarmTask]:
        return self._registry.get(name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_task_with_delay(self, task: PrewarmTask, app: Any) -> None:
        """Honour ``task.delay`` then delegate to :meth:`_run_task_sync`."""
        if task.delay and task.delay > 0:
            self._logger.debug(
                "Prewarm [DELAY  ] %r – sleeping %.1fs before start", task.name, task.delay
            )
            time.sleep(task.delay)
        self._run_task_sync(task, app)

    def _resolve_func_call(self, func: Callable, app: Any) -> Any:
        """Call *func*.  If it takes one positional argument, inject *app*."""
        import inspect as _inspect
        try:
            sig = _inspect.signature(func)
            positional = [
                p for p in sig.parameters.values()
                if p.default is _inspect.Parameter.empty
                and p.kind in (
                    _inspect.Parameter.POSITIONAL_ONLY,
                    _inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            if positional:
                return func(app)
            return func()
        except TypeError:
            return func()

    def _run_task_sync(self, task: PrewarmTask, app: Any) -> None:
        """Execute *task* synchronously, recording all metrics.  Thread-safe."""
        # Dependency check
        for dep_name in task.depends_on:
            dep = self._registry.get(dep_name)
            if dep is None:
                self._logger.warning(
                    "Task %r depends on unknown task %r – skipping dependency wait.",
                    task.name, dep_name
                )
                continue
            # busy-wait with short sleep
            wait_start = time.monotonic()
            while not dep.is_done:
                time.sleep(0.05)
                if task.timeout and (time.monotonic() - wait_start) > task.timeout:
                    self._logger.error(
                        "Task %r timed out waiting for dependency %r.", task.name, dep_name
                    )
                    task.status  = PrewarmStatus.FAILED
                    task.error   = f"Timed out waiting for dependency {dep_name!r}"
                    return
            if dep.status != PrewarmStatus.SUCCESS:
                self._logger.warning(
                    "Task %r: dependency %r did not succeed (status=%s) – skipping.",
                    task.name, dep_name, dep.status
                )
                task.status = PrewarmStatus.SKIPPED
                task.error  = f"Dependency {dep_name!r} not successful"
                return

        task.status     = PrewarmStatus.RUNNING
        task.started_at = time.monotonic()
        t0              = time.perf_counter()
        self._logger.info("Prewarm [START ] %r", task.name)

        try:
            if task.timeout:
                # Run in a temporary thread to enforce timeout
                exc_holder: List[Optional[BaseException]] = [None]
                def _target():
                    try:
                        self._resolve_func_call(task.func, app)
                    except BaseException as exc:
                        exc_holder[0] = exc

                t = threading.Thread(target=_target, daemon=True)
                t.start()
                t.join(timeout=task.timeout)
                if t.is_alive():
                    task.status  = PrewarmStatus.TIMEOUT
                    task.error   = f"Exceeded timeout of {task.timeout}s"
                    self._logger.error(
                        "Prewarm [TIMEOUT] %r after %.1fs", task.name, task.timeout
                    )
                elif exc_holder[0] is not None:
                    raise exc_holder[0]
                else:
                    task.status = PrewarmStatus.SUCCESS
            else:
                self._resolve_func_call(task.func, app)
                task.status = PrewarmStatus.SUCCESS

        except BaseException as exc:  # catch SystemExit, KeyboardInterrupt too
            task.status = PrewarmStatus.FAILED
            task.error  = repr(exc)
            self._logger.exception("Prewarm [FAILED ] %r: %s", task.name, exc)

        finally:
            task.elapsed     = time.perf_counter() - t0
            task.finished_at = time.monotonic()
            level = logging.INFO if task.status == PrewarmStatus.SUCCESS else logging.WARNING
            self._logger.log(
                level,
                "Prewarm [%-7s] %r  elapsed=%.3fs",
                task.status.upper(),
                task.name,
                task.elapsed,
            )


# ---------------------------------------------------------------------------
# Module-level singletons (import-time, not app-bound)
# ---------------------------------------------------------------------------

#: Global registry – plugins call ``prewarm_registry.register(...)`` at module level.
prewarm_registry = PrewarmRegistry()

#: Global manager – app bootstrap calls ``prewarm_manager.run(app)``.
prewarm_manager  = PrewarmManager(registry=prewarm_registry)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def register_prewarm(
    name:        str,
    func:        Callable[[], Any],
    priority:    PrewarmPriority = PrewarmPriority.NORMAL,
    timeout:     Optional[float] = 60.0,
    background:  bool = True,
    tags:        Optional[List[str]] = None,
    depends_on:  Optional[List[str]] = None,
    description: str = "",
    replace:     bool = False,
    delay:       float = 0.0,
) -> PrewarmTask:
    """Shortcut for ``prewarm_registry.register(...)``."""
    return prewarm_registry.register(
        name=name, func=func, priority=priority,
        timeout=timeout, background=background,
        tags=tags, depends_on=depends_on,
        description=description, replace=replace,
        delay=delay,
    )


def prewarm_task(
    name:        str,
    priority:    PrewarmPriority = PrewarmPriority.NORMAL,
    timeout:     Optional[float] = 60.0,
    background:  bool = True,
    tags:        Optional[List[str]] = None,
    depends_on:  Optional[List[str]] = None,
    description: str = "",
    delay:       float = 0.0,
) -> Callable:
    """Decorator form of :func:`register_prewarm`.

    ::

        @prewarm_task("twse_calendar", priority=PrewarmPriority.HIGH, timeout=120)
        def _warmup_calendar():
            from finfun.utils.fin_cale import _ensure_calendar_registered
            _ensure_calendar_registered()

        # Delayed: start 30 s after app startup (low-urgency tasks)
        @prewarm_task("quant_modules", priority=PrewarmPriority.LOW, delay=30.0)
        def _warmup_quant():
            import numpy  # noqa: F401
            import pandas  # noqa: F401
    """
    def decorator(func: Callable) -> Callable:
        register_prewarm(
            name=name, func=func, priority=priority,
            timeout=timeout, background=background,
            tags=tags, depends_on=depends_on,
            description=description or func.__doc__ or "",
            delay=delay,
        )
        return func
    return decorator
