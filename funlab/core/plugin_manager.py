"""
Modern Plugin Manager with Performance Optimizations
現代化Plugin管理器，具備效能優化功能
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import weakref
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Type, Union
import json
import hashlib

from funlab.utils import log

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from funlab.flaskr.app import FunlabFlask


class PluginState(Enum):
    """Plugin狀態列舉"""
    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    ACTIVE = "active"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass
class PluginMetadata:
    """Plugin元數據"""
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    dependencies: List[str] = field(default_factory=list)
    optional_dependencies: List[str] = field(default_factory=list)
    priority: int = 100  # 數字越小優先級越高
    lazy_load: bool = True
    auto_enable: bool = True
    min_python_version: str = "3.11"
    entry_point: str = ""
    config_schema: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginInfo:
    """Plugin資訊"""
    metadata: PluginMetadata
    state: PluginState = PluginState.UNLOADED
    instance: Optional[Any] = None
    load_time: Optional[float] = None
    error_message: Optional[str] = None
    last_access: Optional[float] = None


class PluginCache:
    """Plugin快取管理"""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "plugin_cache.json"
        self._cache_lock = threading.RLock()

    def get_cache_key(self, entry_point_group: str) -> str:
        """生成快取鍵值"""
        return hashlib.md5(entry_point_group.encode()).hexdigest()

    def load_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """載入快取"""
        try:
            with self._cache_lock:
                if self.cache_file.exists():
                    with open(self.cache_file, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                        return cache_data.get(cache_key)
        except Exception:
            pass
        return None

    def save_cache(self, cache_key: str, data: Dict[str, Any]):
        """儲存快取"""
        try:
            with self._cache_lock:
                cache_data = {}
                if self.cache_file.exists():
                    with open(self.cache_file, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)

                cache_data[cache_key] = data

                with open(self.cache_file, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.warning(f"Failed to save plugin cache: {e}")

    def invalidate_cache(self):
        """清除快取"""
        try:
            with self._cache_lock:
                if self.cache_file.exists():
                    self.cache_file.unlink()
        except Exception:
            pass


class PluginLoader:
    """高效能Plugin載入器"""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.logger = log.get_logger(self.__class__.__name__, level=logging.INFO)
        self.cache = PluginCache(cache_dir or Path.cwd() / ".plugin_cache")
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="PluginLoader")

    @lru_cache(maxsize=128)
    def discover_plugins(self, group: str, force_refresh: bool = False) -> Dict[str, PluginMetadata]:
        """發現並快取plugins"""
        cache_key = self.cache.get_cache_key(group)

        if not force_refresh:
            cached_data = self.cache.load_cache(cache_key)
            if cached_data:
                self.logger.debug(f"Loading plugins from cache for group: {group}")
                return {name: PluginMetadata(**metadata)
                       for name, metadata in cached_data.items()}

        self.logger.info(f"Discovering plugins for group: {group}")
        start_time = time.time()

        plugins = {}
        plugin_entry_points = entry_points(group=group)

        for entry_point in plugin_entry_points:
            try:
                # 不直接載入class，只收集metadata
                metadata = self._extract_metadata(entry_point)
                plugins[entry_point.name] = metadata
            except Exception as e:
                self.logger.error(f"Failed to extract metadata from {entry_point.name}: {e}")

        # 快取結果
        cache_data = {name: metadata.__dict__ for name, metadata in plugins.items()}
        self.cache.save_cache(cache_key, cache_data)

        discovery_time = time.time() - start_time
        self.logger.info(f"Discovered {len(plugins)} plugins in {discovery_time:.3f}s")

        return plugins

    def _extract_metadata(self, entry_point) -> PluginMetadata:
        """提取plugin metadata而不載入class"""
        # 這裡可以通過讀取pyproject.toml或其他方式獲得metadata
        # 為了簡化，先使用基本資訊
        return PluginMetadata(
            name=entry_point.name,
            entry_point=f"{entry_point.module}:{entry_point.attr}",
            priority=100,  # 預設優先級
            lazy_load=True
        )

    def load_plugin_async(self, entry_point_name: str, metadata: PluginMetadata) -> Any:
        """異步載入plugin"""
        def _load():
            try:
                self.logger.debug(f"Loading plugin: {entry_point_name}")
                start_time = time.time()

                # 實際載入plugin class
                module_name, class_name = metadata.entry_point.split(':')
                module = __import__(module_name, fromlist=[class_name])
                plugin_class = getattr(module, class_name)

                load_time = time.time() - start_time
                self.logger.debug(f"Plugin {entry_point_name} loaded in {load_time:.3f}s")

                return plugin_class
            except Exception as e:
                import traceback
                error_detail = traceback.format_exc()
                self.logger.error(f"Failed to load plugin {entry_point_name}: {e}")
                self.logger.error(f"Full traceback for {entry_point_name}:\n{error_detail}")
                raise

        # 提交到線程池執行
        future = self._executor.submit(_load)
        return future

    def shutdown(self):
        """關閉載入器"""
        self._executor.shutdown(wait=True)


class PluginDependencyResolver:
    """Plugin依賴解析器"""

    def __init__(self):
        self.logger = log.get_logger(self.__class__.__name__)

    def resolve_load_order(self, plugins: Dict[str, PluginMetadata]) -> List[str]:
        """解析plugin載入順序"""
        # 使用拓撲排序解決依賴關係
        visited = set()
        temp_visited = set()
        result = []

        def visit(plugin_name: str):
            if plugin_name in temp_visited:
                raise ValueError(f"Circular dependency detected involving {plugin_name}")
            if plugin_name in visited:
                return

            temp_visited.add(plugin_name)

            if plugin_name in plugins:
                metadata = plugins[plugin_name]
                for dep in metadata.dependencies:
                    if dep in plugins:
                        visit(dep)
                    else:
                        self.logger.warning(f"Missing dependency: {dep} for plugin {plugin_name}")

            temp_visited.remove(plugin_name)
            visited.add(plugin_name)
            result.append(plugin_name)

        # 按優先級排序後進行拓撲排序
        sorted_plugins = sorted(plugins.items(), key=lambda x: x[1].priority)

        for plugin_name, _ in sorted_plugins:
            if plugin_name not in visited:
                visit(plugin_name)

        return result


class ModernPluginManager:
    """現代化Plugin管理器"""

    def __init__(self, app: FunlabFlask, cache_dir: Optional[Path] = None):
        self.app = app
        self.logger = log.get_logger(self.__class__.__name__, level=logging.INFO)

        # Plugin管理相關
        self.plugins: Dict[str, PluginInfo] = {}
        self.plugin_loader = PluginLoader(cache_dir)
        self.dependency_resolver = PluginDependencyResolver()

        # 效能監控
        self._access_times: Dict[str, float] = {}
        self._load_stats: Dict[str, Dict[str, Any]] = {}

        # 線程安全
        self._lock = threading.RLock()

        # Lazy loading相關
        self._lazy_plugins: Set[str] = set()
        self._active_plugins: Set[str] = set()

    def register_plugins(self, group: str = 'funlab_plugin',
                        priority_plugins: List[str] = None,
                        force_refresh: bool = False):
        """註冊plugins"""
        start_time = time.time()
        self.logger.info(f"Starting plugin registration for group: {group}")

        # 發現plugins
        discovered_plugins = self.plugin_loader.discover_plugins(group, force_refresh)
        self.logger.info(f"Discovered {len(discovered_plugins)} plugins")

        # 解析載入順序
        load_order = self.dependency_resolver.resolve_load_order(discovered_plugins)
        self.logger.info(f"Plugin load order: {load_order}")

        # 處理優先級plugins
        if priority_plugins:
            # 將優先級plugins移到前面
            priority_order = [p for p in priority_plugins if p in load_order]
            regular_order = [p for p in load_order if p not in priority_plugins]
            load_order = priority_order + regular_order

        # 創建plugin info
        for plugin_name in load_order:
            if plugin_name in discovered_plugins:
                metadata = discovered_plugins[plugin_name]
                plugin_info = PluginInfo(metadata=metadata)
                self.plugins[plugin_name] = plugin_info

                # ✅ 特殊處理：某些擴充功能必須立即載入
                should_immediate_load = False
                load_reason = ""

                try:
                    module_name = metadata.entry_point.split(':')[0]

                    # 1. SecurityPlugin 必須立即載入
                    if 'auth' in module_name.lower() or plugin_name == 'AuthView':
                        should_immediate_load = True
                        load_reason = "SecurityPlugin"

                    # 2. 有選單的 ViewPlugin 需要立即載入以確保選單顯示
                    elif plugin_name in ['FundMgrView', 'OptionView']:
                        should_immediate_load = True
                        load_reason = "MenuRequired"

                    # 3. 檢查擴充功能類別是否有選單相關方法（進階檢測）
                    elif self._plugin_likely_has_menu(metadata):
                        should_immediate_load = True
                        load_reason = "MenuDetected"

                    # 4. 實際檢查擴充功能是否有 blueprint（需要路由註冊）
                    elif self._plugin_actually_has_blueprint(metadata):
                        should_immediate_load = True
                        load_reason = "BlueprintDetected"

                except Exception as e:
                    self.logger.warning(f"Error analyzing plugin {plugin_name}: {e}")

                if metadata.lazy_load and not should_immediate_load:
                    self._lazy_plugins.add(plugin_name)
                    self.logger.debug(f"Plugin {plugin_name} marked for lazy loading")
                else:
                    # 立即載入
                    if should_immediate_load:
                        self.logger.info(f"Loading {load_reason} immediately: {plugin_name}")
                    self._load_plugin_sync(plugin_name)

        registration_time = time.time() - start_time
        self.logger.info(f"Plugin registration completed in {registration_time:.3f}s")

        # 輸出統計資訊
        self._log_plugin_stats()

    def _load_plugin_sync(self, plugin_name: str) -> bool:
        """同步載入plugin"""
        with self._lock:
            plugin_info = self.plugins.get(plugin_name)
            if not plugin_info:
                return False

            if plugin_info.state in [PluginState.LOADED, PluginState.ACTIVE]:
                return True

            try:
                plugin_info.state = PluginState.LOADING
                start_time = time.time()

                # 載入plugin class
                future = self.plugin_loader.load_plugin_async(plugin_name, plugin_info.metadata)
                plugin_class = future.result(timeout=60)  # 增加到60秒超時

                # 初始化plugin
                plugin_instance = plugin_class(self.app)

                plugin_info.instance = plugin_instance
                plugin_info.state = PluginState.LOADED
                plugin_info.load_time = time.time() - start_time

                # 註冊到Flask
                self._register_plugin_to_flask(plugin_name, plugin_instance)

                plugin_info.state = PluginState.ACTIVE
                self._active_plugins.add(plugin_name)

                self.logger.info(f"Plugin {plugin_name} loaded successfully in {plugin_info.load_time:.3f}s")
                return True

            except Exception as e:
                import traceback
                error_detail = traceback.format_exc()
                plugin_info.state = PluginState.ERROR
                plugin_info.error_message = str(e)
                self.logger.error(f"Failed to load plugin {plugin_name}: {e}")
                self.logger.error(f"Full traceback for {plugin_name}:\n{error_detail}")
                return False

    def get_plugin(self, plugin_name: str) -> Optional[Any]:
        """獲取plugin實例（支援lazy loading）"""
        with self._lock:
            plugin_info = self.plugins.get(plugin_name)
            if not plugin_info:
                return None

            # 記錄訪問時間
            current_time = time.time()
            plugin_info.last_access = current_time
            self._access_times[plugin_name] = current_time

            # Lazy loading
            if plugin_info.state == PluginState.UNLOADED and plugin_name in self._lazy_plugins:
                self.logger.info(f"Lazy loading plugin: {plugin_name}")
                if self._load_plugin_sync(plugin_name):
                    return plugin_info.instance
                else:
                    return None

            return plugin_info.instance if plugin_info.state == PluginState.ACTIVE else None

    def _register_plugin_to_flask(self, plugin_name: str, plugin_instance: Any):
        """將plugin註冊到Flask應用（使用與舊系統相同的邏輯）"""
        # 使用和原有的register_plugin相同的邏輯
        self.app.plugins[plugin_instance.name] = plugin_instance

        if blueprint := getattr(plugin_instance, 'blueprint', None):
            # 檢查Flask應用是否已經開始處理請求
            try:
                self.app.register_blueprint(blueprint)
                self.logger.debug(f"Blueprint registered for plugin {plugin_name}")
            except AssertionError as e:
                if "has already handled its first request" in str(e):
                    self.logger.warning(f"Cannot register blueprint for plugin {plugin_name}: "
                                      f"Flask app has already started. Plugin functionality will be limited.")
                    # 設置一個標記，表示這個擴充功能的blueprint沒有被註冊
                    plugin_instance._blueprint_registered = False
                else:
                    raise  # 重新拋出其他AssertionError
            else:
                plugin_instance._blueprint_registered = True

        # 創建SQLAlchemy registry db table for each plugin
        if hasattr(plugin_instance, 'entities_registry') and plugin_instance.entities_registry:
            self.app.dbmgr.create_registry_tables(plugin_instance.entities_registry)

        # 處理SecurityPlugin - 使用importlib檢查類型以避免循環導入
        from funlab.core.plugin import SecurityPlugin
        from funlab.core.enhanced_plugin import EnhancedSecurityPlugin

        if isinstance(plugin_instance, (SecurityPlugin, EnhancedSecurityPlugin)):
            # ✅ 修復：允許AuthView覆蓋默認的login_manager設置
            if self.app.login_manager is not None and hasattr(self.app.login_manager, '_default_user_loader'):
                # 如果當前的login_manager有_default_user_loader標記，表示是默認設置，可以被覆蓋
                self.logger.info(f"Replacing default login_manager with SecurityPlugin: {plugin_name}")
                self.app.login_manager = plugin_instance.login_manager
                self.app.login_manager.init_app(self.app)
            elif self.app.login_manager is None:
                # 第一次設置login_manager
                self.logger.info(f"Installing SecurityPlugin login_manager: {plugin_name}")
                self.app.login_manager = plugin_instance.login_manager
                self.app.login_manager.init_app(self.app)
            else:
                # 已經有其他SecurityPlugin安裝了，警告但不跳過路由註冊
                self.logger.warning(f"SecurityPlugin already installed, but continuing to register routes for {plugin_name}")

            # 設置blueprint-specific login view
            if hasattr(plugin_instance, 'login_view') and plugin_instance.login_view:
                if not hasattr(self.app.login_manager, 'blueprint_login_views'):
                    self.app.login_manager.blueprint_login_views = {}
                self.app.login_manager.blueprint_login_views[plugin_instance.bp_name] = plugin_instance.login_view

            # ✅ 重要：設置全局默認login_view為AuthView的登入頁面
            if plugin_name == 'AuthView':
                self.app.login_manager.login_view = f'{plugin_instance.bp_name}.login'
                self.logger.info(f"Set global login_view to: {self.app.login_manager.login_view}")

        # 注意：setup_menus()已經在ViewPlugin.__init__()中調用過了，無需重複調用

    def reload_plugin(self, plugin_name: str) -> bool:
        """重新載入plugin"""
        with self._lock:
            plugin_info = self.plugins.get(plugin_name)
            if not plugin_info:
                return False

            try:
                # 卸載現有plugin
                if plugin_info.instance:
                    if hasattr(plugin_info.instance, 'unload'):
                        plugin_info.instance.unload()

                # 清除快取
                self.plugin_loader.cache.invalidate_cache()

                # 重新載入
                plugin_info.state = PluginState.UNLOADED
                plugin_info.instance = None

                return self._load_plugin_sync(plugin_name)

            except Exception as e:
                self.logger.error(f"Failed to reload plugin {plugin_name}: {e}")
                return False

    def get_plugin_stats(self) -> Dict[str, Any]:
        """獲取plugin統計資訊"""
        stats = {
            'total_plugins': len(self.plugins),
            'active_plugins': len(self._active_plugins),
            'lazy_plugins': len(self._lazy_plugins),
            'error_plugins': len([p for p in self.plugins.values() if p.state == PluginState.ERROR]),
            'plugins': {}
        }

        for name, info in self.plugins.items():
            stats['plugins'][name] = {
                'state': info.state.value,
                'load_time': info.load_time,
                'last_access': info.last_access,
                'error_message': info.error_message
            }

        return stats

    def _log_plugin_stats(self):
        """輸出plugin統計資訊"""
        stats = self.get_plugin_stats()
        self.logger.info(f"Plugin Statistics:")
        self.logger.info(f"  Total: {stats['total_plugins']}")
        self.logger.info(f"  Active: {stats['active_plugins']}")
        self.logger.info(f"  Lazy: {stats['lazy_plugins']}")
        self.logger.info(f"  Errors: {stats['error_plugins']}")

    def _plugin_likely_has_menu(self, metadata: PluginMetadata) -> bool:
        """檢測擴充功能是否可能有選單項目"""
        try:
            # 檢查模組路徑是否包含視圖相關關鍵字
            entry_point = metadata.entry_point
            if 'view' in entry_point.lower():
                return True

            # 檢查擴充功能名稱是否為 ViewPlugin 類型
            if any(suffix in metadata.name for suffix in ['View', 'view']):
                return True

            return False
        except Exception:
            return False

    def _plugin_actually_has_blueprint(self, metadata: PluginMetadata) -> bool:
        """實際檢查擴充功能類是否有 blueprint 屬性（需要載入類但不初始化）"""
        try:
            # 實際載入擴充功能類（但不初始化）來檢查是否有 blueprint
            module_name, class_name = metadata.entry_point.split(':')
            module = __import__(module_name, fromlist=[class_name])
            plugin_class = getattr(module, class_name)

            # 檢查類是否有 blueprint 相關的方法或屬性
            if hasattr(plugin_class, 'register_routes'):
                self.logger.debug(f"Plugin {metadata.name} has register_routes method")
                return True

            # 檢查父類是否暗示會有 blueprint
            for base in plugin_class.__mro__:
                if 'ViewPlugin' in base.__name__ or 'ServicePlugin' in base.__name__:
                    # ServicePlugin 和 ViewPlugin 通常有 blueprint
                    if hasattr(base, 'blueprint') or 'blueprint' in [attr for attr in dir(base) if not attr.startswith('_')]:
                        self.logger.debug(f"Plugin {metadata.name} inherits from {base.__name__} which suggests blueprint usage")
                        return True

            return False
        except Exception as e:
            self.logger.debug(f"Could not check blueprint for {metadata.name}: {e}")
            return False

    def cleanup(self):
        """清理資源"""
        self.logger.info("Cleaning up plugin manager...")

        # 卸載所有plugins
        for plugin_info in self.plugins.values():
            if plugin_info.instance and hasattr(plugin_info.instance, 'unload'):
                try:
                    plugin_info.instance.unload()
                except Exception as e:
                    self.logger.error(f"Error unloading plugin: {e}")

        # 關閉plugin loader
        self.plugin_loader.shutdown()

        self.logger.info("Plugin manager cleanup completed")


# 便利函數保持向後兼容
def load_plugins(group: str) -> dict:
    """向後兼容的plugin載入函數"""
    import warnings
    warnings.warn(
        "load_plugins function is deprecated. Use ModernPluginManager instead.",
        DeprecationWarning,
        stacklevel=2
    )

    # 為了向後兼容，提供簡化版本
    from importlib.metadata import entry_points
    # load dynamically, ref: https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/
    plugins = {}
    plugin_entry_points = entry_points(group=group)
    for entry_point in plugin_entry_points:
        plugin_name = entry_point.name
        try:
            plugin_class = entry_point.load()
            plugins[plugin_name] = plugin_class
        except Exception as e:
            raise e
    return plugins

