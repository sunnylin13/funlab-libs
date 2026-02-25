"""
Enhanced Plugin Base Classes with Modern Features
增強的Plugin基礎類別，支援現代化功能
"""
from __future__ import annotations

from abc import ABC
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

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
    RELOADING = "reloading"  # reload() 進行期間，涵蓋 stop→reconfigure→start 全程
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
    """增強的ViewPlugin基礎類別

    Lifecycle Architecture
    ======================
    Plugin 生命週期透過三層機制協同運作，各層職責分明：

    Layer 1 — Template Method（子類覆寫點）
    ────────────────────────────────────────
    供 Plugin 子類覆寫以注入自身邏輯的 protected methods：

    - ``_on_init()``         : 在 __init__() 末尾被呼叫，執行結構初始化（無 I/O）
    - ``_on_start()``        : 在 start() 流程中被呼叫，執行啟動邏輯（含 I/O）
    - ``_on_stop()``         : 在 stop() 流程中被呼叫，執行停止 / 清理邏輯
    - ``_on_reload()``       : 在 reload() 流程中，stop 後 start 前；預設重讀 config
    - ``_on_menu_reload()``  : 由 _on_reload() 呼叫；預設重建 Menu 物件
    - ``_on_unload()``       : 在 unload() 流程中，stop 之前被呼叫，用於資源釋放
    - ``_on_error()``        : 在 start/stop/reload 發生例外時被呼叫

    **「__init__ 職責」與「start 職責」分界原則**::

        __init__ / _on_init()   → 結構（無 I/O）: Blueprint, routes, menus, 建立物件
        _on_start()             → 資源（含 I/O）: 連線, 背景執行緒, 載入資料
        _on_stop()              → 釋放資源      : 斷線, 停止執行緒, 清空快取
        _on_reload() → 整備     : 重讀設定, 清空舊狀態（不連線）
        _on_unload() → 不可逆   : 永久清理（只執行一次）

    Layer 2 — Instance Hooks（實例級觀察者）
    ────────────────────────────────────────
    允許外部程式碼對 *特定* plugin 實例動態註冊 callback：

    - ``add_lifecycle_hook(event, callback)``
    - 支援事件：``before_start``, ``after_start``, ``before_stop``,
      ``after_stop``, ``on_error``

    Layer 3 — Global Hooks（應用級觀察者）
    ────────────────────────────────────────
    透過 ``app.hook_manager`` 廣播至全應用，供 cross-cutting concerns 監聽：

    - ``plugin_after_init``
    - ``plugin_before_start`` / ``plugin_after_start``
    - ``plugin_before_stop``  / ``plugin_after_stop``
    - ``plugin_before_reload`` / ``plugin_after_reload``

    Lifecycle State Machine
    =======================
    ::

        INITIALIZING → READY ──→ STARTING  → RUNNING ──→ STOPPING → STOPPED
                         ↑                      │                      │
                         │                      ↓                      │
                         │                    ERROR                    │
                         │                      ↑                      │
                         └──── RELOADING ←──────┘                      │
                                  ↑────────────────────────────────────┘
                                (reload: RUNNING/STOPPED → RELOADING → stop → reconfigure → start)

    Note: RELOADING 是 reload() 的外包裹狀態，其內部仍會經過 STOPPING → STOPPED
    und STARTING → RUNNING sub-transitions。

    Method Execution Order
    =======================

    ``start()`` ::

        1. Global hook  : plugin_before_start
        2. Instance hook: before_start
        3. Template      : _on_start()
        4. State → RUNNING
        5. Instance hook: after_start
        6. Global hook  : plugin_after_start

    ``stop()`` ::

        1. Global hook  : plugin_before_stop
        2. Instance hook: before_stop
        3. Template      : _on_stop()
        4. State → STOPPED
        5. Instance hook: after_stop
        6. Global hook  : plugin_after_stop

    ``reload()`` ::

        1. State → RELOADING
        2. Global hook  : plugin_before_reload
        3. stop()           ← 完整的 stop 流程（含 _on_stop 及所有 hooks）
        4. Template      : _on_reload()  ← 預設重讀 config + _on_menu_reload()
        5. Template      : _on_menu_reload()  ← 預設重建 Menu 物件
        6. start()          ← 完整的 start 流程（含 _on_start 及所有 hooks）
        7. Global hook  : plugin_after_reload

    **三個 init 操作與 reload 的關係**::

        _init_blueprint()      → 結構永久型（Flask 限制），只在 __init__ 執行一次
                                  Blueprint 一旦向 app 注冊即不可撤銷，reload 不觸碰
        _init_configuration()  → 可變型，_on_reload() 預設重新讀取
        setup_menus()          → 可重建型，_on_menu_reload() 預設重新建立 Menu 物件

    ``unload()`` ::

        1. Template      : _on_unload()  ← 不可逆的最終資源釋放（僅此一次）
        2. stop()         ← 完整的 stop 流程（_on_stop() 也會在此被呼叫）

        注意：_on_unload() 與 _on_stop() 在卸載時都會被呼叫，順序是
        _on_unload() 先、_on_stop() 後。請確保兩者邏輯不會重複釋放同一資源。
    """

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

        # Blueprint設置
        self._init_blueprint(url_prefix)

        # 配置管理
        self._init_configuration()

        # 選單設置
        self.setup_menus()

        # Layer 2: Instance-level lifecycle hooks (觀察者模式)
        # 必須在 _on_init() 與 plugin_after_init 之前初始化，
        # 確保 handler 可安全呼叫 add_lifecycle_hook()
        self._lifecycle_hooks: Dict[str, List[callable]] = {
            'before_start': [],
            'after_start': [],
            'before_stop': [],
            'after_stop': [],
            'on_error': []
        }

        # Layer 1: _on_init() — 子類別 init-time 擴充點（結構初始化，不做 I/O）
        # 適合在此：註冊額外 routes、建立物件（非連線）、讀取靜態設定
        # 不適合在此：連線外部服務、啟動背景執行緒（應移至 _on_start()）
        self._on_init()

        # Layer 3: Global lifecycle hook — notify app-level observers
        # 此時 _lifecycle_hooks 已就緒，_on_init() 已完成，
        # 所有 handler 均可安全呼叫 add_lifecycle_hook()
        if hasattr(self.app, 'hook_manager'):
            self.mylogger.info(f"Triggering plugin_after_init hook for {self.name}")
            self.app.hook_manager.call_hook(
                'plugin_after_init',
                plugin=self,
                plugin_name=self.name,
            )

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
        """初始化 Blueprint（僅在 __init__ 執行一次，reload 不重新呼叫）

        Flask 的 Blueprint 一旦透過 ``app.register_blueprint()`` 注冊，
        路由規則即寫入 ``app.url_map``，框架並未提供撤銷 API。
        因此 Blueprint 是「結構永久型」資源：

        - ``_on_reload()`` 不呼叫此方法
        - 路由（routes）在 reload 前後保持不變
        - 需要動態路由行為，請在路由 handler 內讀取 ``self.plugin_config``
        """
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

    # ══════════════════════════════════════════════════════════════════
    # Lifecycle Management
    #
    # 三層機制依序觸發，詳細流程請參閱 class docstring。
    #   Layer 1: Template Method  — _on_start / _on_stop / _on_reload / _on_unload
    #   Layer 2: Instance Hooks   — add_lifecycle_hook / _execute_hooks
    #   Layer 3: Global Hooks     — app.hook_manager.call_hook('plugin_*')
    # ══════════════════════════════════════════════════════════════════

    # --- Layer 2: Instance Hook registration / execution ---------------

    def add_lifecycle_hook(self, event: str, callback: callable):
        """註冊實例級生命週期 callback

        Args:
            event: 事件名稱，可選 ``before_start``, ``after_start``,
                   ``before_stop``, ``after_stop``, ``on_error``
            callback: 事件觸發時執行的 callable
        """
        if event in self._lifecycle_hooks:
            self._lifecycle_hooks[event].append(callback)

    def _execute_hooks(self, event: str, *args, **kwargs):
        """執行指定事件的所有實例級 hooks"""
        for hook in self._lifecycle_hooks.get(event, []):
            try:
                hook(*args, **kwargs)
            except Exception as e:
                self.mylogger.error(f"Error executing {event} hook: {e}")

    def _call_global_hook(self, hook_name: str, **extra_context):
        """觸發 Layer 3 全域 hook（若 hook_manager 存在）"""
        if hasattr(self.app, 'hook_manager'):
            self.app.hook_manager.call_hook(
                hook_name, plugin=self, plugin_name=self.name, **extra_context
            )

    # --- Public lifecycle methods --------------------------------------

    def start(self):
        """啟動Plugin

        執行順序::

            Global  → plugin_before_start
            Instance→ before_start
            Template→ _on_start()
            State   → RUNNING
            Instance→ after_start
            Global  → plugin_after_start

        已在過渡狀態（STARTING / STOPPING / RELOADING）時，直接返回 False
        以防重入。RUNNING 時返回 True（冪等）。
        """
        with self._lock:
            if self._state == PluginLifecycleState.RUNNING:
                return True
            if self._state in (PluginLifecycleState.STARTING,
                               PluginLifecycleState.STOPPING,
                               PluginLifecycleState.RELOADING):
                self.mylogger.warning(
                    f"Plugin {self.name} is in transition state {self._state.value}, "
                    f"start() ignored to prevent re-entrancy."
                )
                return False

            try:
                self._state = PluginLifecycleState.STARTING

                # Layer 3: Global hook
                self._call_global_hook('plugin_before_start')
                # Layer 2: Instance hooks
                self._execute_hooks('before_start')
                # Layer 1: Template method
                self._on_start()

                self._state = PluginLifecycleState.RUNNING
                self._health.is_healthy = True

                # Layer 2: Instance hooks
                self._execute_hooks('after_start')
                # Layer 3: Global hook
                self._call_global_hook('plugin_after_start')

                self.mylogger.info(f"Plugin {self.name} started successfully")
                return True

            except Exception as e:
                self._state = PluginLifecycleState.ERROR
                self._health.is_healthy = False
                self._health.last_error = str(e)
                self._execute_hooks('on_error', e)
                self._on_error(e)  # Layer 1: Template method for error handling
                self.mylogger.error(f"Failed to start plugin {self.name}: {e}")
                return False

    def stop(self):
        """停止Plugin

        執行順序::

            Global  → plugin_before_stop
            Instance→ before_stop
            Template→ _on_stop()
            State   → STOPPED
            Instance→ after_stop
            Global  → plugin_after_stop
        """
        with self._lock:
            if self._state == PluginLifecycleState.STOPPED:
                return True

            try:
                self._state = PluginLifecycleState.STOPPING

                # Layer 3: Global hook
                self._call_global_hook('plugin_before_stop')
                # Layer 2: Instance hooks
                self._execute_hooks('before_stop')
                # Layer 1: Template method
                self._on_stop()

                self._state = PluginLifecycleState.STOPPED

                # Layer 2: Instance hooks
                self._execute_hooks('after_stop')
                # Layer 3: Global hook
                self._call_global_hook('plugin_after_stop')

                self.mylogger.info(f"Plugin {self.name} stopped successfully")
                return True

            except Exception as e:
                self._state = PluginLifecycleState.ERROR
                self._health.last_error = str(e)
                self._execute_hooks('on_error', e)
                self._on_error(e)  # Layer 1: Template method for error handling
                self.mylogger.error(f"Failed to stop plugin {self.name}: {e}")
                return False

    def reload(self):
        """重新載入Plugin

        執行順序::

            State   → RELOADING
            Global  → plugin_before_reload
            stop()    ← 完整 stop 流程（含 _on_stop 及所有 hooks）
            Template→ _on_reload()  ← stop 後、start 前的重新整備
            start()   ← 完整 start 流程（含 _on_start 及所有 hooks）
            Global  → plugin_after_reload

        stop() 或 _on_reload() 失敗時，state 轉為 ERROR 並返回 False。
        reload() 進行期間，外部的 start() 呼叫會被拒絕（防重入）。
        """
        with self._lock:
            if self._state == PluginLifecycleState.RELOADING:
                self.mylogger.warning(f"Plugin {self.name} is already reloading.")
                return False
            self._state = PluginLifecycleState.RELOADING

        self.mylogger.info(f"Reloading plugin {self.name}")
        try:
            self._call_global_hook('plugin_before_reload')
            stop_ok = self.stop()
            if not stop_ok:
                # stop() 已將 state 設為 ERROR，此處僅記錄並跳出
                self.mylogger.error(f"Reload aborted: stop() failed for plugin {self.name}")
                return False
            self._on_reload()
            result = self.start()
            self._call_global_hook('plugin_after_reload')
            return result
        except Exception as e:
            self._state = PluginLifecycleState.ERROR
            self._health.last_error = str(e)
            self._execute_hooks('on_error', e)
            self._on_error(e)  # Layer 1: Template method for error handling
            self.mylogger.error(f"Failed to reload plugin {self.name}: {e}")
            return False

    def unload(self):
        """卸載Plugin

        執行順序::

            Template→ _on_unload()
            stop()    ← 完整 stop 流程（含 _on_stop 及所有 hooks）
        """
        self._on_unload()
        self.stop()
        self.mylogger.info(f"Plugin {self.name} unloaded")

    # --- Health check ---------------------------------------------------

    def health_check(self) -> bool:
        """健康檢查"""
        try:
            self._health.last_check = time.time()

            # 基本健康檢查
            if self._state == PluginLifecycleState.ERROR:
                self._health.is_healthy = False
                return False

            # 子類別可重寫 _perform_health_check() 進行更詳細的檢查
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

    # ══════════════════════════════════════════════════════════════════
    # Layer 1: Template Methods — 子類覆寫點
    # ══════════════════════════════════════════════════════════════════

    def setup_menus(self):
        """設置選單項目，子類可覆寫以自訂選單結構"""
        self._mainmenu = Menu(title=self.name, dummy=True)
        self._usermenu = Menu(title=self.name, dummy=True, collapsible=True)

    def _on_init(self):
        """Plugin 初始化完成時調用（__init__ 末尾，plugin_after_init hook 之前）

        子類覆寫此方法，安全地執行「結構初始化」邏輯，例如：

        - 呼叫 ``register_routes()`` / ``self._blueprint.route(...)``
        - 建立 scheduler、queue 等物件（不啟動）
        - 讀取靜態設定或常數
        - 呼叫 ``add_lifecycle_hook()``（此時 ``_lifecycle_hooks`` 已就緒）

        **注意**：此方法中 **不應** 執行任何 I/O 操作（連線、讀取資料庫、
        啟動執行緒等）。這些操作屬於 ``_on_start()`` 的職責。

        典型使用模式::

            class MyService(EnhancedServicePlugin):

                def _on_init(self):
                    self._scheduler = BackgroundScheduler()  # 建立物件，不啟動
                    self._task_registry = {}                  # 初始化資料結構
                    self.register_routes()                    # 路由只需註冊一次

                def _on_start(self):
                    self._scheduler.start()   # I/O 與執行緒在此啟動
                    self._load_tasks()        # 連線資料庫讀取任務

                def _on_stop(self):
                    self._scheduler.shutdown(wait=False)

        整合到 stop / reload / unload:

        - ``stop()``   : 不呼叫 _on_init()（只呼叫 _on_stop()）
        - ``reload()`` : 不呼叫 _on_init()（只呼叫 _on_reload() + 完整 start/stop）
        - ``unload()`` : 不呼叫 _on_init()（_on_unload() → _on_stop()）

        結論：_on_init() **只執行一次**（plugin 建立時），不在任何 lifecycle
        操作中重複執行，與可重複執行的 _on_start() 形成明確分工。
        """
        pass

    def _perform_health_check(self) -> bool:
        """執行自訂健康檢查，子類可覆寫"""
        return True

    def _on_start(self):
        """Plugin 啟動時調用

        子類覆寫此方法以執行啟動邏輯（如連線、載入資源）。
        在 ``start()`` 流程中，Instance ``before_start`` hooks 之後、
        狀態轉為 RUNNING 之前被呼叫。
        """
        pass

    def _on_stop(self):
        """Plugin 停止時調用

        子類覆寫此方法以執行清理邏輯（如斷線、釋放資源）。
        在 ``stop()`` 流程中，Instance ``before_stop`` hooks 之後、
        狀態轉為 STOPPED 之前被呼叫。
        """
        pass

    def _on_reload(self):
        """Plugin 重新整備時調用（stop 完成後、start 開始前）

        **預設實作** 依序執行：

        1. ``_init_configuration()`` — 從 plugin.toml 重新讀取設定，
           使 ``self.plugin_config`` 反映最新值
        2. ``_on_menu_reload()``    — 重建 Menu 物件（預設呼叫 ``setup_menus()``）

        子類覆寫時，**請務必呼叫 super()._on_reload()**，
        以確保設定刷新行為被保留，再加入自身的重整備邏輯：:

            def _on_reload(self):
                super()._on_reload()          # 重讀 config + 重建 menus
                self._clear_state_cache()      # 額外清空運行時快取
                self._rebuild_connection_params()  # 根據新 config 重建連線參數

        此方法 **不應** 連線外部服務或啟動執行緒（屬 ``_on_start()`` 職責），
        只負責為下一次 ``_on_start()`` 準備好設定與物件狀態。

        **不觸碰** ``_init_blueprint()``：Blueprint/路由是結構永久型資源，
        Flask 不支援撤銷，reload 不重建（詳見 ``_init_blueprint()`` docstring）。

        類比：
          - Java Bukkit：等效邏輯放在 ``onEnable()`` 開頭的 ``reloadConfig()`` 呼叫
          - Spring：等效於 ``AbstractApplicationContext.refreshBeanFactory()`` 前的 reset
        """
        self._init_configuration()
        self._on_menu_reload()

    def _on_menu_reload(self):
        """選單重建時調用（由 _on_reload() 呼叫）

        **預設實作** 呼叫 ``setup_menus()`` 就地重建 ``self._mainmenu``
        與 ``self._usermenu``，使選單反映最新的 ``plugin_config``。

        **注意：物件參考問題**
        ``setup_menus()`` 會建立新的 Menu 物件並覆蓋 ``self._mainmenu``、
        ``self._usermenu``。若 app 或其他元件在初始化時已快取了舊的 Menu
        物件引用（如 ``m = plugin.menu``），快取不會自動更新。

        安全模式 — 透過 ``plugin.menu`` property 每次重新查詢，確保取得最新物件：:

            # ✅ 安全：每次透過 property 取得，reload 後自動得到新物件
            app.render_menu(plugin.menu)

            # ⚠️ 危險：快取引用，reload 後仍指向舊物件
            cached_menu = plugin.menu
            app.render_menu(cached_menu)  # reload 後不會更新

        若選單結構固定、不依賴 config，子類可覆寫為 no-op：:

            def _on_menu_reload(self):
                pass  # 選單不需隨 config 重建
        """
        self.setup_menus()

    def _on_unload(self):
        """Plugin 卸載時調用（stop() 之前，且僅此一次）

        子類覆寫此方法，執行**不可逆的**最終資源釋放。
        典型用途：
          - 寫入最終狀態至持久化儲存
          - 向外部系統取消永久訂閱
          - 釋放無法在 stop/start 間重建的 OS 資源

        **重要**：``unload()`` 後會緊接呼叫 ``stop()``，
        因此 ``_on_stop()`` 也將被執行。
        請確保 ``_on_unload()`` 與 ``_on_stop()`` 不重複釋放同一資源。
        建議在 ``_on_unload()`` 中設置 flag，讓 ``_on_stop()`` 檢查後略過特定步驟。

        類比：
          - Java OSGi：``BundleActivator.stop()`` 同時承擔永久清理職責
          - Java Bukkit：``onDisable()`` 是唯一的清理 hook，框架不區分 stop 與 unload
        """
        pass

    def _on_error(self, error: Exception):
        """Plugin 在 start / stop / reload 期間發生錯誤時調用

        子類可覆寫此方法，以執行：
          - 自動恢復（如重試連線、fallback 模式）
          - 通知外部系統（Slack、PagerDuty 等）
          - 記錄診斷資訊

        此方法在 ``_execute_hooks('on_error', error)`` 之後被呼叫，
        確保 Layer 2 觀察者先處理，再由 plugin 自身做最後的錯誤響應。

        Args:
            error: 導致失敗的例外物件

        類比：
          - Java Bukkit：無官方 onError()，通常用 try/catch + logger 處理
          - Spring SmartLifecycle：無 error hook，依賴 ApplicationListener<ContextClosedEvent>
          - Go (hashicorp/go-plugin)：透過 gRPC health checks 偵測，非直接 callback
        """
        pass


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

        # Layer 3: Global lifecycle hook — service-specific init notification
        if hasattr(self.app, 'hook_manager'):
            self.mylogger.info(f"Triggering plugin_service_init hook for {self.name}")
            self.app.hook_manager.call_hook(
                'plugin_service_init',
                plugin=self,
                plugin_name=self.name,
            )

        # 服務相關狀態
        self._service_running = False
        self._service_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def _on_start(self):
        """Plugin啟動時調用，子類覆寫此方法執行服務啟動邏輯。

        這是 Enhanced ServicePlugin 的 override 點：
          - 改寫 _on_start() 而非 start()，以保留 lifecycle 狀態機管理。
          - 預設為 no-op；子類覆寫即可。
        """
        pass

    def _on_stop(self):
        """Plugin停止時調用，子類覆寫此方法執行服務停止與資源釋放邏輯。

        預設為 no-op；子類覆寫即可。
        """
        pass

    def _perform_health_check(self) -> bool:
        """檢查服務是否正常運行

        以 PluginLifecycleState.RUNNING 作為健康基線，額外確認
        背景執行緒（若有）是否存活。

        子類可覆寫以加入更細緻的檢查（例如：連線心跳、queue 深度）。
        """
        if self._state != PluginLifecycleState.RUNNING:
            return False
        return (
            self._service_thread is None
            or self._service_thread.is_alive()
        )


# 向後兼容的別名
ViewPlugin = EnhancedViewPlugin
SecurityPlugin = EnhancedSecurityPlugin
ServicePlugin = EnhancedServicePlugin
