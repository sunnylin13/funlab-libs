from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import logging
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
    def __init__(self, app: Any):
        self.app = app
        self.logger = log.get_logger(self.__class__.__name__, level=logging.INFO)
        self._hooks: Dict[str, List[Tuple[int, Callable[..., Any], Optional[str]]]] = defaultdict(list)

    def register_hook(self, hook_name: str, callback: Callable[..., Any], priority: int = 100,
                      plugin_name: Optional[str] = None) -> None:
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

        results: List[HookCallResult] = []
        for _, callback, plugin_name in self._hooks.get(hook_name, []):
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