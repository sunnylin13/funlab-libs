"""
Modern Plugin Manager with Performance Optimizations
現代化Plugin管理器，具備效能優化功能
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
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
    # load_mode controls when the plugin module is imported and instantiated:
    #
    #   "lazy"    (default) — import deferred until first get_plugin() call.
    #                         Keeps startup fast; ideal for optional features.
    #
    #   "startup" — imported and instantiated during register_plugins(), before
    #                Flask handles its first request.  Required for plugins that:
    #                • register a Blueprint (routes must exist before routing starts)
    #                • install flask-login handlers (SecurityPlugin / AuthView)
    #                • add menu items built at __init__ time
    #                • start background threads or hold shared resources
    #
    # Declare in pyproject.toml:
    #   [tool.funlab_plugin_metadata.AuthView]
    #   load_mode = "startup"
    load_mode: str = "lazy"
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
        # Cache live EntryPoint objects keyed by plugin name.
        # Populated unconditionally in discover_plugins() Step 1 (before file-cache
        # restore), so load_plugin_class() can always call ep.load() on every startup.
        self._entry_points: Dict[str, Any] = {}

    def discover_plugins(self, group: str, force_refresh: bool = False) -> Dict[str, PluginMetadata]:
        """發現並快取plugins

        Note: @lru_cache removed because force_refresh parameter needs to bypass cache.
        The file-based cache below handles memory efficiency for repeated calls.

        _entry_points population strategy
        ──────────────────────────────────
        ``entry_points(group=group)`` only reads dist-info metadata strings — it
        does NOT import any plugin module. It is therefore cheap (~milliseconds)
        and safe to call on every startup unconditionally.

        We always enumerate live EntryPoint objects so that ``_entry_points`` is
        populated regardless of whether the richer PluginMetadata comes from the
        file cache or from live pyproject.toml discovery.  Without this, every
        startup after the first would find ``_entry_points`` empty and ``ep.load()``
        would fail with a missing-entry-point RuntimeError.
        """
        cache_key = self.cache.get_cache_key(group)

        # ── Step 1: always enumerate live EntryPoint objects (fast, no import) ──
        # Populates _entry_points so load_plugin_class() can call ep.load()
        # on every startup, not just the first one after cache creation.
        live_entry_points = entry_points(group=group)
        for ep in live_entry_points:
            self._entry_points[ep.name] = ep

        # ── Step 2: try file cache for enriched PluginMetadata ──────────────
        if not force_refresh:
            cached_data = self.cache.load_cache(cache_key)
            if cached_data:
                self.logger.debug(f"Loading plugin metadata from cache for group: {group}")
                field_names = set(PluginMetadata.__dataclass_fields__.keys())
                def _make_meta(d: Dict[str, Any]) -> PluginMetadata:
                    filtered = {k: v for k, v in d.items() if k in field_names}
                    # ── Backwards-compat: stale cache written before load_mode field ──
                    # Old cache entries have lazy_load/immediate_load booleans instead of
                    # the load_mode string.  Map them so startup plugins are not silently
                    # downgraded to "lazy" after a schema change.
                    if 'load_mode' not in filtered:
                        if d.get('immediate_load', False):
                            filtered['load_mode'] = 'startup'
                        elif not d.get('lazy_load', True):
                            filtered['load_mode'] = 'startup'
                        # else: leave absent → dataclass default "lazy"
                    return PluginMetadata(**filtered)
                # _entry_points already populated above; return cached metadata
                return {name: _make_meta(metadata)
                       for name, metadata in cached_data.items()}

        # ── Step 3: live discovery — read pyproject.toml for enriched metadata ─
        self.logger.info(f"Discovering plugins for group: {group}")
        start_time = time.time()

        plugins = {}
        for entry_point in live_entry_points:
            try:
                # 不直接載入class，只收集metadata（含 pyproject.toml 豐富資訊）
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
        """提取plugin metadata而不載入class

        Reads the package's pyproject.toml (for editable/dev installs) to pick
        up ``[tool.funlab_plugin_metadata.<PluginName>]`` declarations:
          - dependencies         – required sibling plugin names
          - optional_dependencies – soft-required sibling plugin names
          - load_mode            – "lazy" (default) | "startup"
                                   Legacy keys lazy_load / immediate_load also
                                   accepted and mapped to load_mode automatically.
          (priority is intentionally omitted: load order is fully expressed
           through the dependency graph and requires no separate numeric hint)
        """
        metadata = PluginMetadata(
            name=entry_point.name,
            entry_point=f"{entry_point.module}:{entry_point.attr}",
        )
        # Try to enrich from pyproject.toml of the distributing package
        try:
            source_root = self._find_dist_source_root(entry_point)
            if source_root:
                pyproject_path = source_root / 'pyproject.toml'
                if pyproject_path.exists():
                    import tomllib
                    with open(pyproject_path, 'rb') as f:
                        toml_data = tomllib.load(f)
                    plugin_meta = (
                        toml_data
                        .get('tool', {})
                        .get('funlab_plugin_metadata', {})
                        .get(entry_point.name, {})
                    )
                    if plugin_meta:
                        # Plugin ordering / hard-deps (plugin names)
                        metadata.dependencies = plugin_meta.get('dependencies', [])
                        metadata.optional_dependencies = plugin_meta.get('optional_dependencies', [])
                        # load_mode: canonical key.
                        # Backwards-compat: honour legacy lazy_load / immediate_load booleans
                        # if the new load_mode key is absent.
                        if 'load_mode' in plugin_meta:
                            metadata.load_mode = plugin_meta['load_mode']
                        elif plugin_meta.get('immediate_load', False):
                            metadata.load_mode = 'startup'
                        elif not plugin_meta.get('lazy_load', True):
                            metadata.load_mode = 'startup'
                        # else: no load_mode declared → keep dataclass default "lazy"
                        self.logger.debug(
                            f"Plugin '{entry_point.name}' metadata enriched from pyproject.toml: {plugin_meta}"
                        )
        except Exception as exc:
            self.logger.debug(f"Could not enrich metadata from pyproject.toml for {entry_point.name}: {exc}")
        return metadata

    def _find_dist_source_root(self, entry_point) -> Optional[Path]:
        """Resolve the source root of a distribution (works for editable/dev installs).

        For editable installs Poetry writes a ``direct_url.json`` into the dist-info
        directory containing the file:// URL of the checked-out source tree.
        """
        try:
            import json as _json
            from urllib.request import url2pathname
            dist = entry_point.dist
            # direct_url.json is the PEP 610 file that indicates the original URL
            raw = dist.read_text('direct_url.json')
            if raw:
                info = _json.loads(raw)
                url: str = info.get('url', '')
                if url.startswith('file:'):
                    # url2pathname correctly handles file:// and file:/// on all OSes
                    path_part = url[len('file:'):]
                    while path_part.startswith('//'):
                        path_part = path_part[1:]
                    # On Windows: /D:/foo → D:/foo
                    if len(path_part) >= 3 and path_part[0] == '/' and path_part[2] == ':':
                        path_part = path_part[1:]
                    return Path(path_part)
        except Exception:
            pass
        return None

    def _format_module_not_found_hint(self, missing_module: str, plugin_module: str) -> str:
        """Build a human-readable hint for a ModuleNotFoundError."""
        lines = [
            f"Missing module: '{missing_module}'",
            f"  → Required (directly or transitively) by plugin module: '{plugin_module}'",
        ]
        lines.append(
            f"  → Install the package that provides '{missing_module}'."
        )
        return "\n".join(lines)

    def load_plugin_class(self, entry_point_name: str, metadata: PluginMetadata) -> Any:
        """載入 plugin class（同步，直接呼叫；不經過 thread pool）

        Uses ``EntryPoint.load()`` which is the standard packaging API:
            ep.load()  ≡  getattr(importlib.import_module(ep.module), ep.attr)

        First call pays disk-I/O + bytecode cost; subsequent calls for the same
        module are a free ``sys.modules`` dict lookup (~1µs).

        ``_entry_points`` is always populated in ``discover_plugins()`` Step 1
        (before file-cache restore), so ``ep`` is never None in normal flow.
        """
        try:
            self.logger.info(f"Loading plugin class: {entry_point_name}...")
            start_time = time.time()

            ep: EntryPoint = self._entry_points.get(entry_point_name)
            if ep is None:
                raise RuntimeError(
                    f"EntryPoint '{entry_point_name}' not found in _entry_points. "
                    f"This should not happen — ensure discover_plugins() was called first."
                )
            plugin_class = ep.load()

            load_time = time.time() - start_time
            self.logger.info(f"Plugin class {entry_point_name} loaded in {load_time:.3f}s")
            return plugin_class

        except ModuleNotFoundError as e:
            import traceback
            missing = e.name or str(e)
            plugin_module = metadata.entry_point.split(':')[0]
            hint = self._format_module_not_found_hint(missing, plugin_module)
            self.logger.warning(
                f"[PluginLoader] ⚠ Plugin '{entry_point_name}' skipped – {hint}"
            )
            self.logger.debug(f"Full traceback for {entry_point_name}:\n{traceback.format_exc()}")
            raise
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            self.logger.error(f"Failed to load plugin {entry_point_name}: {e}")
            self.logger.error(f"Full traceback for {entry_point_name}:\n{error_detail}")
            raise

    def load_plugin_async(self, entry_point_name: str, metadata: PluginMetadata) -> Any:
        """異步載入plugin（提交到 thread pool；適用於後台預載入懶加載 plugins）

        NOTE: 在主線程的同步載入路徑（_load_plugin_sync）請直接使用
        load_plugin_class() 以避免不必要的 thread context switch 開銷。
        此方法保留給真正需要並行的場景（例如：Flask 啟動後預熱懶加載 plugins）。
        """
        future = self._executor.submit(self.load_plugin_class, entry_point_name, metadata)
        return future

    def shutdown(self):
        """關閉載入器"""
        self._executor.shutdown(wait=True)


class PluginDependencyResolver:
    """Plugin依賴解析器"""

    def __init__(self):
        self.logger = log.get_logger(self.__class__.__name__)

    def resolve_load_order(self, plugins: Dict[str, PluginMetadata]) -> List[str]:
        """解析plugin載入順序（支援 hard/optional 兩種依賴）

        * ``dependencies``          – hard deps: missing → WARNING, plugin skipped
        * ``optional_dependencies`` – soft deps: missing → INFO, still loaded (feature degraded)
        Both affect topological sort so that when a dependency IS present it is
        loaded before the dependent plugin.
        """
        # ── Validate hard dependencies ─────────────────────────────────────
        for plugin_name, metadata in plugins.items():
            for dep in metadata.dependencies:
                if dep not in plugins:
                    self.logger.warning(
                        f"⚠ Plugin '{plugin_name}' has a REQUIRED plugin dependency "
                        f"'{dep}' that is NOT installed. "
                        f"'{plugin_name}' will be disabled. "
                        f"Install the package that provides the '{dep}' plugin."
                    )

        # ── Topological sort (Kahn's algorithm) ────────────────────────────
        visited: set[str] = set()
        temp_visited: set[str] = set()
        result: list[str] = []

        def visit(plugin_name: str):
            if plugin_name in temp_visited:
                raise ValueError(f"Circular dependency detected involving '{plugin_name}'")
            if plugin_name in visited:
                return

            temp_visited.add(plugin_name)
            metadata = plugins.get(plugin_name)
            if metadata:
                # Hard deps first
                for dep in metadata.dependencies:
                    if dep in plugins:
                        visit(dep)
                    # Missing hard dep already warned above; skip silently here
                # Optional deps: load them first when available, otherwise just INFO
                for dep in metadata.optional_dependencies:
                    if dep in plugins:
                        visit(dep)
                    else:
                        self.logger.info(
                            f"ℹ Plugin '{plugin_name}': optional plugin dependency "
                            f"'{dep}' is not installed – related features will be limited."
                        )

            temp_visited.discard(plugin_name)
            visited.add(plugin_name)
            result.append(plugin_name)

        # Sort alphabetically for determinism; dependency ordering is fully
        # handled by the DFS topo sort below – explicit priority field removed.
        sorted_plugins = sorted(plugins.items(), key=lambda x: x[0])
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
                        force_refresh: bool = False):
        """註冊plugins"""
        start_time = time.time()
        self.logger.info(f"Starting plugin registration for group: {group}")

        # 發現plugins
        discovered_plugins = self.plugin_loader.discover_plugins(group, force_refresh)
        self.logger.info(f"Discovered {len(discovered_plugins)} plugins")

        # 解析載入順序（由各plugin的priority及dependencies宣告決定，無需外部PRIORITY_PLUGINS覆寫）
        load_order = self.dependency_resolver.resolve_load_order(discovered_plugins)
        self.logger.info(f"Plugin load order: {load_order}")

        # 創建plugin info
        for plugin_name in load_order:
            if plugin_name in discovered_plugins:
                metadata = discovered_plugins[plugin_name]
                plugin_info = PluginInfo(metadata=metadata)
                self.plugins[plugin_name] = plugin_info

                # ── Load decision (metadata-driven) ──────────────────────────────────
                #
                # metadata.load_mode (set in pyproject.toml):
                #
                #  "lazy"    (default) — add to _lazy_plugins; module NOT imported.
                #                        Triggered on first get_plugin() call.
                #
                #  "startup" — import + instantiate now, before Flask handles
                #               its first request.  Required for Blueprints,
                #               login handlers, menus, background services.
                #
                # Backwards-compat: _extract_metadata() maps legacy
                # lazy_load=false / immediate_load=true → load_mode="startup".

                if metadata.load_mode == 'startup':
                    self.logger.info(f"Startup-load plugin: {plugin_name}")
                    self._load_plugin_sync(plugin_name)
                else:
                    self._lazy_plugins.add(plugin_name)
                    self.logger.debug(f"Plugin {plugin_name} deferred (lazy)")

        registration_time = time.time() - start_time
        self.logger.info(f"Plugin registration completed in {registration_time:.3f}s")

        # 輸出統計資訊
        self._log_plugin_stats()

    def _load_plugin_sync(self, plugin_name: str) -> bool:
        """同步載入plugin

        Calls load_plugin_class() directly (no thread pool) to avoid the
        overhead of a thread context switch when the result is waited on
        immediately.  The GIL means Python I/O during import can release it,
        but sequential plugin loading within _lock gains nothing from a
        thread pool submit+result round-trip.
        """
        with self._lock:
            plugin_info = self.plugins.get(plugin_name)
            if not plugin_info:
                return False

            if plugin_info.state in [PluginState.LOADED, PluginState.ACTIVE]:
                return True

            try:
                plugin_info.state = PluginState.LOADING
                start_time = time.time()

                # 直接呼叫 load_plugin_class()，無 thread pool 額外開銷
                plugin_class = self.plugin_loader.load_plugin_class(plugin_name, plugin_info.metadata)

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

            except ModuleNotFoundError as e:
                missing = e.name or str(e)
                plugin_module = plugin_info.metadata.entry_point.split(':')[0]
                hint = self.plugin_loader._format_module_not_found_hint(missing, plugin_module)
                plugin_info.state = PluginState.DISABLED
                plugin_info.error_message = f"Missing module: {missing}"
                self.logger.warning(
                    f"[ModernPluginManager] ⚠ Plugin '{plugin_name}' disabled – {hint}"
                )
                return False

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

        # Check for SecurityPlugin to initialise flask-login
        from funlab.core.enhanced_plugin import EnhancedSecurityPlugin

        if isinstance(plugin_instance, EnhancedSecurityPlugin):
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
            'disabled_plugins': len([p for p in self.plugins.values() if p.state == PluginState.DISABLED]),
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
        self.logger.info(f"  Total:    {stats['total_plugins']}")
        self.logger.info(f"  Active:   {stats['active_plugins']}")
        self.logger.info(f"  Lazy:     {stats['lazy_plugins']}")
        self.logger.info(f"  Disabled: {stats['disabled_plugins']}")
        self.logger.info(f"  Errors:   {stats['error_plugins']}")
        # Surface disabled/error details so operators know what to fix
        for name, info in self.plugins.items():
            if info.state == PluginState.DISABLED:
                self.logger.warning(f"  [DISABLED] {name}: {info.error_message}")
            elif info.state == PluginState.ERROR:
                self.logger.error(f"  [ERROR]    {name}: {info.error_message}")

    def cleanup(self):
        """清理資源"""
        self.logger.info("Cleaning up plugin manager...")

        # 卸載所有plugins (按逆序卸載)
        for plugin_name in reversed(list(self.plugins.keys())):
            plugin_info = self.plugins[plugin_name]
            if plugin_info.instance:
                try:
                    # 只調用 stop() 方法，不調用 unload()
                    if hasattr(plugin_info.instance, 'stop'):
                        self.logger.debug(f"Stopping plugin: {plugin_name}")
                        plugin_info.instance.stop()
                    elif hasattr(plugin_info.instance, 'unload'):
                        self.logger.debug(f"Unloading plugin: {plugin_name}")
                        plugin_info.instance.unload()
                except Exception as e:
                    self.logger.error(f"Error stopping plugin {plugin_name}: {e}", exc_info=True)

        # 關閉plugin loader
        try:
            self.plugin_loader.shutdown()
        except Exception as e:
            self.logger.error(f"Error shutting down plugin loader: {e}")

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

