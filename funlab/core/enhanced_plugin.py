"""
Enhanced Plugin Base Classes with Modern Features
增強的Plugin基礎類別，支援現代化功能
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import logging
import threading
import time
import weakref
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Type, Union

from flask_login import LoginManager
from flask import Blueprint
from .menu import Menu
from funlab.core.config import Config
from funlab.core import _Configuable
from funlab.utils import log

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from funlab.flaskr.app import FunlabFlask


class PluginLifecycleState(Enum):
    """Plugin生命週期狀態"""
    INITIALIZING = "initializing"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class PluginHealth:
    """Plugin健康狀態"""
    is_healthy: bool = True
    last_check: Optional[float] = None
    error_count: int = 0
    last_error: Optional[str] = None
    uptime: float = 0.0


class PluginMetrics:
    """Plugin效能指標"""

    def __init__(self):
        self._lock = threading.RLock()
        self.start_time = time.time()
        self.request_count = 0
        self.error_count = 0
        self.total_response_time = 0.0
        self.min_response_time = float('inf')
        self.max_response_time = 0.0
        self.last_activity = time.time()

    def record_request(self, response_time: float, success: bool = True):
        """記錄請求指標"""
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
        """獲取指標數據"""
        with self._lock:
            uptime = time.time() - self.start_time
            avg_response_time = (self.total_response_time / max(1, self.request_count - self.error_count))

            return {
                'uptime': uptime,
                'request_count': self.request_count,
                'error_count': self.error_count,
                'error_rate': self.error_count / max(1, self.request_count),
                'avg_response_time': avg_response_time,
                'min_response_time': self.min_response_time if self.min_response_time != float('inf') else 0,
                'max_response_time': self.max_response_time,
                'last_activity': self.last_activity,
                'requests_per_second': self.request_count / max(1, uptime)
            }


class EnhancedViewPlugin(_Configuable, ABC):
    """增強的ViewPlugin基礎類別"""

    def __init__(self, app: FunlabFlask, url_prefix: str = None):
        """
        初始化增強的ViewPlugin

        Args:
            app (FunlabFlask): The FunlabFlask app that have this plugin
            url_prefix (str, optional): The blueprint's url_prefix
        """
        # 基本設置
        self.mylogger = log.get_logger(self.__class__.__name__, level=logging.INFO)
        self.app: FunlabFlask = app
        self.name = self._generate_plugin_name()

        # 生命週期管理
        self._state = PluginLifecycleState.INITIALIZING
        self._health = PluginHealth()
        self._metrics = PluginMetrics()
        self._lock = threading.RLock()

        # 註冊到應用
        self.app.extensions[self.name] = self

        # 配置管理
        self._init_configuration()

        # Blueprint設置
        self._init_blueprint(url_prefix)

        # 選單設置
        self.setup_menus()

        # 生命週期hooks
        self._lifecycle_hooks: Dict[str, List[callable]] = {
            'before_start': [],
            'after_start': [],
            'before_stop': [],
            'after_stop': [],
            'on_error': []
        }

        # 標記為就緒
        self._state = PluginLifecycleState.READY
        self.mylogger.info(f"Plugin {self.name} initialized successfully")

    def _generate_plugin_name(self) -> str:
        """生成plugin名稱"""
        return (self.__class__.__name__
                .removesuffix('View')
                .removesuffix('Security')
                .removesuffix('Service')
                .removesuffix('Plugin')
                .lower())

    def _init_configuration(self):
        """初始化配置"""
        ext_config = self.app.get_section_config(
            section=self.__class__.__name__,
            default=Config({self.__class__.__name__: {}},
                          env_file_or_values=self.app._config._env_vars),
            keep_section=True
        )
        self.plugin_config = self.get_config(file_name='plugin.toml', ext_config=ext_config)

    def _init_blueprint(self, url_prefix: str):
        """初始化Blueprint"""
        self.bp_name = self.name + '_bp'
        self._blueprint = Blueprint(
            self.bp_name,
            self.__class__.__module__,
            static_folder='static',
            template_folder='templates',
            url_prefix='/' + (self.name if url_prefix is None else url_prefix)
        )

        # 添加性能監控中間件
        self._add_performance_middleware()

    def _add_performance_middleware(self):
        """添加性能監控中間件"""
        @self._blueprint.before_request
        def before_request():
            import flask
            flask.g.plugin_start_time = time.time()

        @self._blueprint.after_request
        def after_request(response):
            import flask
            if hasattr(flask.g, 'plugin_start_time'):
                response_time = time.time() - flask.g.plugin_start_time
                success = 200 <= response.status_code < 400
                self._metrics.record_request(response_time, success)
            return response

    # Properties
    @property
    def blueprint(self):
        """獲取Blueprint"""
        return self._blueprint

    @property
    def state(self) -> PluginLifecycleState:
        """獲取Plugin狀態"""
        return self._state

    @property
    def health(self) -> PluginHealth:
        """獲取Plugin健康狀態"""
        return self._health

    @property
    def metrics(self) -> Dict[str, Any]:
        """獲取Plugin指標"""
        return self._metrics.get_metrics()

    @property
    def login_view(self):
        """Use to create blueprint_login_views of flask-login for the view if not None"""
        return None

    @property
    def menu(self) -> Menu:
        return self._mainmenu

    @property
    def usermenu(self) -> Menu:
        return self._usermenu

    @property
    def needDivider(self) -> bool:
        """subclass implement to let app decide if create usermenu with divider or not"""
        return True

    @property
    def entities_registry(self):
        """FunlabFlask use to table creation by sqlalchemy in __init__ for application initiation"""
        return None

    # Lifecycle Management
    def add_lifecycle_hook(self, event: str, callback: callable):
        """添加生命週期hook"""
        if event in self._lifecycle_hooks:
            self._lifecycle_hooks[event].append(callback)

    def _execute_hooks(self, event: str, *args, **kwargs):
        """執行生命週期hooks"""
        for hook in self._lifecycle_hooks.get(event, []):
            try:
                hook(*args, **kwargs)
            except Exception as e:
                self.mylogger.error(f"Error executing {event} hook: {e}")

    def start(self):
        """啟動Plugin"""
        with self._lock:
            if self._state == PluginLifecycleState.RUNNING:
                return True

            try:
                self._state = PluginLifecycleState.STARTING
                self._execute_hooks('before_start')

                # 子類別可重寫此方法
                self._on_start()

                self._state = PluginLifecycleState.RUNNING
                self._health.is_healthy = True
                self._execute_hooks('after_start')

                self.mylogger.info(f"Plugin {self.name} started successfully")
                return True

            except Exception as e:
                self._state = PluginLifecycleState.ERROR
                self._health.is_healthy = False
                self._health.last_error = str(e)
                self._execute_hooks('on_error', e)
                self.mylogger.error(f"Failed to start plugin {self.name}: {e}")
                return False

    def stop(self):
        """停止Plugin"""
        with self._lock:
            if self._state == PluginLifecycleState.STOPPED:
                return True

            try:
                self._state = PluginLifecycleState.STOPPING
                self._execute_hooks('before_stop')

                # 子類別可重寫此方法
                self._on_stop()

                self._state = PluginLifecycleState.STOPPED
                self._execute_hooks('after_stop')

                self.mylogger.info(f"Plugin {self.name} stopped successfully")
                return True

            except Exception as e:
                self._state = PluginLifecycleState.ERROR
                self._health.last_error = str(e)
                self._execute_hooks('on_error', e)
                self.mylogger.error(f"Failed to stop plugin {self.name}: {e}")
                return False

    def health_check(self) -> bool:
        """健康檢查"""
        try:
            self._health.last_check = time.time()

            # 基本健康檢查
            if self._state == PluginLifecycleState.ERROR:
                self._health.is_healthy = False
                return False

            # 子類別可重寫此方法進行更詳細的檢查
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

    # Abstract/Customizable Methods
    def setup_menus(self):
        """設置選單項目"""
        self._mainmenu = Menu(title=self.name, dummy=True)
        self._usermenu = Menu(title=self.name, dummy=True, collapsible=True)

    def _on_start(self):
        """Plugin啟動時調用"""
        pass

    def _on_stop(self):
        """Plugin停止時調用"""
        pass

    def _perform_health_check(self) -> bool:
        """執行健康檢查"""
        return True

    def reload(self):
        """重新載入Plugin"""
        self.mylogger.info(f"Reloading plugin {self.name}")
        self.stop()
        # 這裡可以重新初始化配置等
        self.start()

    def unload(self):
        """卸載Plugin"""
        self.stop()
        self.mylogger.info(f"Plugin {self.name} unloaded")


class EnhancedSecurityPlugin(EnhancedViewPlugin):
    """增強的SecurityPlugin"""

    def __init__(self, app: FunlabFlask, url_prefix: str = None):
        super().__init__(app, url_prefix)

        # 初始化LoginManager
        self._login_manager = LoginManager()
        self._login_manager.login_view = self.bp_name + ".login"
        self._login_manager.login_message = "Please log in to access this page."
        self._login_manager.login_message_category = "warning"
        self._login_manager.needs_refresh_message_category = "info"

        # 安全相關指標
        self._security_metrics = {
            'login_attempts': 0,
            'failed_logins': 0,
            'successful_logins': 0,
            'active_sessions': 0
        }
        self._security_lock = threading.RLock()

    @property
    def login_manager(self):
        return self._login_manager

    def record_login_attempt(self, success: bool):
        """記錄登入嘗試"""
        with self._security_lock:
            self._security_metrics['login_attempts'] += 1
            if success:
                self._security_metrics['successful_logins'] += 1
            else:
                self._security_metrics['failed_logins'] += 1

    def get_security_metrics(self) -> Dict[str, Any]:
        """獲取安全指標"""
        with self._security_lock:
            return self._security_metrics.copy()


class EnhancedServicePlugin(EnhancedViewPlugin):
    """增強的ServicePlugin"""

    def __init__(self, app: FunlabFlask):
        super().__init__(app)

        # 服務相關狀態
        self._service_running = False
        self._service_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @abstractmethod
    def start_service(self):
        """啟動服務"""
        pass

    @abstractmethod
    def stop_service(self):
        """停止服務"""
        pass

    def restart_service(self):
        """重啟服務"""
        self.stop_service()
        self.start_service()

    @abstractmethod
    def reload_service(self):
        """重新載入服務"""
        pass

    def _on_start(self):
        """Plugin啟動時啟動服務"""
        self.start_service()

    def _on_stop(self):
        """Plugin停止時停止服務"""
        self.stop_service()

    def _perform_health_check(self) -> bool:
        """檢查服務是否正常運行"""
        return self._service_running and (
            self._service_thread is None or self._service_thread.is_alive()
        )


# 向後兼容的別名
ViewPlugin = EnhancedViewPlugin
SecurityPlugin = EnhancedSecurityPlugin
ServicePlugin = EnhancedServicePlugin
