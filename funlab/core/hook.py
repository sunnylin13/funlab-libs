from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import logging
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from markupsafe import Markup
from funlab.utils import log

try:
    from flask import request
    from flask_login import current_user
except Exception:  # pragma: no cover - safe fallback outside Flask context
    request = None
    current_user = None


@dataclass
class HookCallResult:
    hook_name: str
    callback: Callable[..., Any]
    result: Any


class HookManager:
    """Global (Layer 3) hook registry.

    框架內建全域 hook（權威矩陣見 funlab-libs ``docs/PLUGIN_LIFECYCLE.md`` §2）：

    - ``plugin_after_init``：每個 Plugin 構造完成（``plugin.py:Plugin.__init__``）。
    - ``plugins_registration_complete``：**框架內建、恰觸發一次**——
      ``FunlabFlask._register_plugin_manager_view()`` 成功後由 funlab-flaskr
      ``app.py`` 廣播（R10，kanban t_e56e99f5），context 自動帶 ``app``。
      「等全部 plugin 註冊完成再啟動 X」的消費端（SchedService／QuoteService）
      一律監聽此 hook，不要再以 ``plugin_after_init``＋plugin_name 字串匹配推斷。
    - ``plugin_before/after_{start,stop,reload}``、``controller_*``、
      ``view_layouts_*``、``task_*``、``model_*``：見 §2 矩陣。
    """

    def __init__(self, app: Any):
        self.app = app
        self.logger = log.get_logger(self.__class__.__name__, level=logging.INFO)
        self._hooks: Dict[str, List[Tuple[int, Callable[..., Any], Optional[str]]]] = defaultdict(list)
        # Guards _hooks mutation vs. dispatch-snapshot-taking.  Callbacks are
        # invoked OUTSIDE the lock so a callback may register/unregister hooks
        # without deadlocking, and never mutates the in-flight dispatch.
        self._lock = threading.RLock()

    def register_hook(self, hook_name: str, callback: Callable[..., Any], priority: int = 100,
                      plugin_name: Optional[str] = None) -> None:
        with self._lock:
            self._hooks[hook_name].append((priority, callback, plugin_name))
            self._hooks[hook_name].sort(key=lambda item: item[0])

    def call_hook(self, hook_name: str, **context: Any) -> List[HookCallResult]:
        if "app" not in context:
            context["app"] = self.app
        if request is not None:
            try:
                context.setdefault("request", request)
            except RuntimeError:
                pass
        if current_user is not None:
            try:
                context.setdefault("current_user", current_user)
            except Exception:
                pass

        with self._lock:
            entries = list(self._hooks.get(hook_name, []))

        results: List[HookCallResult] = []
        for _, callback, plugin_name in entries:
            try:
                result = callback(context)
                results.append(HookCallResult(hook_name, callback, result))
            except Exception as exc:
                self.logger.error(
                    "Hook %s from %s failed: %s",
                    hook_name,
                    plugin_name or getattr(callback, "__module__", "unknown"),
                    exc,
                )
        return results

    def render_hook(self, hook_name: str, **context: Any) -> Markup:
        output = []
        for result in self.call_hook(hook_name, **context):
            if result.result is None:
                continue
            if isinstance(result.result, (list, tuple)):
                output.extend([str(item) for item in result.result if item is not None])
            else:
                output.append(str(result.result))
        return Markup("".join(output))

    def list_hooks(self, hook_name: Optional[str] = None) -> Dict[str, Iterable[Callable[..., Any]]]:
        if hook_name:
            return {hook_name: [item[1] for item in self._hooks.get(hook_name, [])]}
        return {name: [item[1] for item in hooks] for name, hooks in self._hooks.items()}