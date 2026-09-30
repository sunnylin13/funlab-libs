from __future__ import annotations

from abc import ABC
import inspect
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional, Protocol, runtime_checkable

from flask import Blueprint, request

from .menu import Menu
from funlab.core.config import Config
from funlab.core import _Configuable
from funlab.utils import log

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from funlab.flaskr.app import FunlabFlask


@runtime_checkable
class ISecurityProvider(Protocol):
    """Structural protocol for plugins that provide Flask-Login authentication.

    Any plugin that exposes a ``login_manager`` property returning a
    ``flask_login.LoginManager`` instance will be recognised by
    ``ModernPluginManager`` as a security provider and automatically wired
    into the Flask application — no inheritance required.

    This keeps the infrastructure layer (``ModernPluginManager``) coupled only
    to this structural contract, honouring the Dependency Inversion Principle.
    (The old ``SecurityPlugin`` subclass was removed 2026-09-30 — zero
    consumers system-wide; wire authentication by exposing ``login_manager``.)
    """

    @property
    def login_manager(self) -> Any:  # LoginManager, kept as Any to avoid circular
        """Return the :class:`flask_login.LoginManager` managed by this plugin."""
        ...


class PluginLifecycleState(Enum):
    INITIALIZING = "initializing"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    RELOADING = "reloading"
    ERROR = "error"


@dataclass
class PluginHealth:
    is_healthy: bool = True
    last_check: Optional[float] = None
    error_count: int = 0
    last_error: Optional[str] = None
    uptime: float = 0.0


class PluginMetrics:
    def __init__(self):
        self._lock = threading.RLock()
        self.start_time = time.time()
        self.request_count = 0
        self.error_count = 0
        self.total_response_time = 0.0
        self.min_response_time = float("inf")
        self.max_response_time = 0.0
        self.last_activity = time.time()

    def record_request(self, response_time: float, success: bool = True):
        with self._lock:
            self.request_count += 1
            self.last_activity = time.time()
            if success:
                self.total_response_time += response_time
                self.min_response_time = min(self.min_response_time, response_time)
                self.max_response_time = max(self.max_response_time, response_time)
            else:
                self.error_count += 1

    def get_metrics(self) -> Dict[str, Any]:
        with self._lock:
            uptime = time.time() - self.start_time
            avg_response_time = self.total_response_time / max(1, self.request_count - self.error_count)
            return {
                "uptime": uptime,
                "request_count": self.request_count,
                "error_count": self.error_count,
                "error_rate": self.error_count / max(1, self.request_count),
                "avg_response_time": avg_response_time,
                "min_response_time": self.min_response_time if self.min_response_time != float("inf") else 0,
                "max_response_time": self.max_response_time,
                "last_activity": self.last_activity,
                "requests_per_second": self.request_count / max(1, uptime),
            }


class Plugin(_Configuable, ABC):
    default_route_policy: Callable | None = None
    default_route_exempt_endpoints: set[str] = set()

    def __init__(self, app: FunlabFlask, url_prefix: str = None):
        self.mylogger = log.get_logger(self.__class__.__name__, level=logging.INFO)
        self.app: FunlabFlask = app
        self.name = self._generate_plugin_name()

        self._state = PluginLifecycleState.INITIALIZING
        self._health = PluginHealth()
        self._metrics = PluginMetrics()
        self._lock = threading.RLock()
        self._stop_executed = False

        self.app.extensions[self.name] = self

        self._init_blueprint(url_prefix)
        self._init_configuration()
        self.setup_menus()

        # R2 (2026-09-30): the Layer-2 per-instance lifecycle hooks
        # (_lifecycle_hooks / add_lifecycle_hook / _execute_hooks) were removed
        # — zero production consumers system-wide; use the global
        # hook_manager (Layer 3) or template-method overrides (Layer 1).

        self._on_init()
        self.register_prewarm_tasks()

        if hasattr(self.app, "hook_manager"):
            self.app.hook_manager.call_hook(
                "plugin_after_init",
                plugin=self,
                plugin_name=self.name,
            )

        self._state = PluginLifecycleState.READY

    def _generate_plugin_name(self) -> str:
        return (
            self.__class__.__name__
            .removesuffix("View")
            .removesuffix("Security")
            .removesuffix("Service")
            .removesuffix("Plugin")
            .lower()
        )

    def _init_configuration(self):
        ext_config = self.app.get_section_config(
            section=self.__class__.__name__,
            default=Config({self.__class__.__name__: {}}, env_file_or_values=self.app._config._env_vars),
            keep_section=True,
        )
        self.plugin_config = self.get_config(file_name="plugin.toml", ext_config=ext_config)

    def _init_blueprint(self, url_prefix: str):
        self.bp_name = self.name + "_bp"
        self._blueprint = Blueprint(
            self.bp_name,
            self.__class__.__module__,
            static_folder="static",
            template_folder="templates",
            url_prefix="/" + (self.name if url_prefix is None else url_prefix),
        )
        self._add_performance_middleware()
        self._add_default_policy_middleware()

    @staticmethod
    def skip_default_policy(func):
        setattr(func, '_skip_default_policy', True)
        return func

    def _resolve_default_route_policy(self):
        """Resolve ``default_route_policy`` into a callable taking ``current_user``.

        A plain function assigned as a CLASS attribute goes through the
        descriptor protocol on ``getattr(self, ...)`` and arrives here as a
        bound method whose signature has swallowed ``self`` (the policy would
        receive no user argument). When that happens — bound to this instance
        with zero remaining required positional parameters — unwrap to the
        underlying function so the policy receives ``current_user``.
        Preferred style (and what avoids this whole branch) is
        ``default_route_policy = staticmethod(policy_fn)``; instance methods
        that take ``(self, user)`` keep their binding untouched.
        """
        policy = getattr(self, 'default_route_policy', None)
        if policy is None:
            return None

        bound_self = getattr(policy, '__self__', None)
        unbound_func = getattr(policy, '__func__', None)
        if bound_self is self and callable(unbound_func):
            try:
                signature = inspect.signature(policy)
            except (TypeError, ValueError):
                return unbound_func

            required_positional = [
                parameter for parameter in signature.parameters.values()
                if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                and parameter.default is inspect.Parameter.empty
            ]
            has_varargs = any(
                parameter.kind == inspect.Parameter.VAR_POSITIONAL
                for parameter in signature.parameters.values()
            )

            if not has_varargs and len(required_positional) == 0:
                return unbound_func

        return policy

    def _add_default_policy_middleware(self):
        # The policy is fixed once the plugin instance exists, but resolve it
        # lazily (first request) and cache only a non-None result so tests or
        # late wiring that assigns default_route_policy after construction
        # keep working. Avoids re-running inspect.signature per request.
        cache = {}

        @self._blueprint.before_request
        def enforce_default_policy():
            policy = cache.get('policy')
            if policy is None:
                policy = self._resolve_default_route_policy()
                if policy is not None:
                    cache['policy'] = policy
            if policy is None:
                return None

            endpoint = request.endpoint or ''
            if not endpoint.startswith(f'{self.bp_name}.'):
                return None

            endpoint_name = endpoint.split('.', 1)[1] if '.' in endpoint else endpoint
            exempt_endpoints = set(getattr(self, 'default_route_exempt_endpoints', set()) or set())
            if endpoint_name in exempt_endpoints:
                return None

            view_func = self.app.view_functions.get(endpoint)
            if view_func is not None and getattr(view_func, '_skip_default_policy', False):
                return None

            from funlab.core.auth import evaluate_policy
            return evaluate_policy(policy)

    def _add_performance_middleware(self):
        @self._blueprint.before_request
        def before_request():
            import flask
            flask.g.plugin_start_time = time.time()

        @self._blueprint.after_request
        def after_request(response):
            import flask
            if hasattr(flask.g, "plugin_start_time"):
                response_time = time.time() - flask.g.plugin_start_time
                success = 200 <= response.status_code < 400
                self._metrics.record_request(response_time, success)
            return response

    @property
    def blueprint(self):
        return self._blueprint

    @property
    def state(self) -> PluginLifecycleState:
        return self._state

    @property
    def health(self) -> PluginHealth:
        return self._health

    @property
    def metrics(self) -> Dict[str, Any]:
        return self._metrics.get_metrics()

    @property
    def menu(self) -> Menu:
        return self._mainmenu

    @property
    def usermenu(self) -> Menu:
        return self._usermenu

    @property
    def needDivider(self) -> bool:
        return True

    @property
    def entities_registry(self):
        return None

    def _call_global_hook(self, hook_name: str, **extra_context):
        if hasattr(self.app, "hook_manager"):
            self.app.hook_manager.call_hook(
                hook_name,
                plugin=self,
                plugin_name=self.name,
                **extra_context,
            )

    def start(self):
        with self._lock:
            if self._state == PluginLifecycleState.RUNNING:
                return True
            if self._state in (
                PluginLifecycleState.STARTING,
                PluginLifecycleState.STOPPING,
                PluginLifecycleState.RELOADING,
            ):
                self.mylogger.warning(
                    f"Plugin {self.name} is in transition state {self._state.value}, start() ignored to prevent re-entrancy."
                )
                return False

            try:
                self._state = PluginLifecycleState.STARTING
                # R1 fix: a previous stop() (including the one inside reload())
                # sets _stop_executed=True; without this reset every subsequent
                # stop() would permanently skip _on_stop via _run_stop_safely().
                self._stop_executed = False
                self._call_global_hook("plugin_before_start")
                self._on_start()
                self._state = PluginLifecycleState.RUNNING
                self._health.is_healthy = True
                self._call_global_hook("plugin_after_start")
                return True
            except Exception as e:
                self._state = PluginLifecycleState.ERROR
                self._health.is_healthy = False
                self._health.last_error = str(e)
                self._on_error(e)
                self.mylogger.error(f"Failed to start plugin {self.name}: {e}")
                return False

    def stop(self):
        with self._lock:
            if self._state == PluginLifecycleState.STOPPED:
                return True
            try:
                self._state = PluginLifecycleState.STOPPING
                self._call_global_hook("plugin_before_stop")
                # Run plugin stop handler in an idempotent, time-limited way.
                self._run_stop_safely()
                self._state = PluginLifecycleState.STOPPED
                self._call_global_hook("plugin_after_stop")
                self.mylogger.info(f"Plugin {self.name} stopped successfully")
                return True
            except Exception as e:
                self._state = PluginLifecycleState.ERROR
                self._health.last_error = str(e)
                self._on_error(e)
                self.mylogger.error(f"Failed to stop plugin {self.name}: {e}")
                return False

    def _run_stop_safely(self, timeout: float = 5.0) -> bool:
        """Run `_on_stop()` in a separate thread with idempotency and timeout.

        This ensures plugin stop handlers are executed at most once and do not
        block the manager indefinitely. Exceptions are logged; return value
        indicates whether handler completed within `timeout`.
        """
        with self._lock:
            if getattr(self, "_stop_executed", False):
                self.mylogger.debug(f"_on_stop already executed for plugin {self.name}; skipping.")
                return True
            self._stop_executed = True

        result = {"ok": True}

        def _runner():
            try:
                self._on_stop()
            except Exception as exc:
                result["ok"] = False
                try:
                    self.mylogger.error(f"Error in _on_stop for {self.name}: {exc}")
                except Exception:
                    pass

        t = threading.Thread(target=_runner, name=f"{getattr(self,'name','plugin')}_stop", daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            try:
                self.mylogger.warning(
                    f"[{getattr(self,'name','plugin')}] _on_stop did not complete within {timeout}s; continuing shutdown."
                )
            except Exception:
                pass
            return False
        return result.get("ok", False)

    def reload(self):
        with self._lock:
            if self._state == PluginLifecycleState.RELOADING:
                self.mylogger.warning(f"Plugin {self.name} is already reloading.")
                return False
            self._state = PluginLifecycleState.RELOADING

        self.mylogger.info(f"Reloading plugin {self.name}")
        try:
            self._call_global_hook("plugin_before_reload")
            stop_ok = self.stop()
            if not stop_ok:
                self.mylogger.error(f"Reload aborted: stop() failed for plugin {self.name}")
                return False
            self._on_reload()
            result = self.start()
            self._call_global_hook("plugin_after_reload")
            return result
        except Exception as e:
            self._state = PluginLifecycleState.ERROR
            self._health.last_error = str(e)
            self._on_error(e)
            self.mylogger.error(f"Failed to reload plugin {self.name}: {e}")
            return False

    def unload(self):
        self._on_unload()
        self.stop()
        self.mylogger.info(f"Plugin {self.name} unloaded")

    def health_check(self) -> bool:
        try:
            self._health.last_check = time.time()
            # R6 fix: PluginHealth.uptime had no assignment anywhere and was
            # always 0.0; derive it from the metrics clock on every check.
            self._health.uptime = time.time() - self._metrics.start_time
            if self._state == PluginLifecycleState.ERROR:
                self._health.is_healthy = False
                return False

            result = self._perform_health_check()
            self._health.is_healthy = result
            if not result:
                self._health.error_count += 1
            return result
        except Exception as e:
            self._health.is_healthy = False
            self._health.error_count += 1
            self._health.last_error = str(e)
            self.mylogger.error(f"Health check failed for plugin {self.name}: {e}")
            return False

    def setup_menus(self):
        self._mainmenu = Menu(title=self.name, dummy=True)
        self._usermenu = Menu(title=self.name, dummy=True, collapsible=True)

    def _on_init(self):
        pass

    def register_prewarm_tasks(self) -> None:
        pass

    def _perform_health_check(self) -> bool:
        return True

    def _on_start(self):
        pass

    def _on_stop(self):
        pass

    def _on_reload(self):
        self._init_configuration()
        self._on_menu_reload()

    def _on_menu_reload(self):
        self.setup_menus()

    def _on_unload(self):
        pass

    def _on_error(self, error: Exception):
        pass


class ServicePlugin(Plugin):
    def __init__(self, app: FunlabFlask):
        # plugin_service_init hook 呼叫已刪除（2026-09-30 使用者裁決，零生產消費；
        # plugin_after_init 由 Plugin.__init__ 統一觸發）。
        super().__init__(app)

    def _on_start(self):
        pass

    def _on_stop(self):
        pass

    def _perform_health_check(self) -> bool:
        return self._state == PluginLifecycleState.RUNNING


class BackgroundWorkerMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_stop_event = threading.Event()

    @property
    def worker_stop_event(self) -> threading.Event:
        return self._worker_stop_event

    @property
    def worker_stop_requested(self) -> bool:
        return self._worker_stop_event.is_set()

    def start_worker(self, target: callable, name: str = None, daemon: bool = True):
        self._worker_stop_event.clear()
        self._worker_thread = threading.Thread(
            target=target,
            name=name or f"{getattr(self, 'name', 'plugin')}_worker",
            daemon=daemon,
        )
        self._worker_thread.start()

    def stop_worker(self, timeout: float = 5.0):
        self._worker_stop_event.set()
        thread = self._worker_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive() and hasattr(self, "mylogger"):
                self.mylogger.warning(
                    f"[{getattr(self, 'name', 'plugin')}] Worker thread did not stop within {timeout}s"
                )
        self._worker_thread = None

    def _perform_health_check(self) -> bool:
        base_ok = super()._perform_health_check()
        if not base_ok:
            return False
        thread = self._worker_thread
        if thread is not None and not thread.is_alive():
            return False
        return True
