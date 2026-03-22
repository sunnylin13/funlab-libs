"""
Modern Plugin Manager with Performance Optimizations
Modern plugin manager with performance-oriented discovery and loading.
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
    """Plugin lifecycle state within the manager."""
    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    ACTIVE = "active"
    ERROR = "error"
    DISABLED = "disabled"

@dataclass
class PluginMetadata:
    """Static metadata describing a plugin distribution."""
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    dependencies: List[str] = field(default_factory=list)
    optional_dependencies: List[str] = field(default_factory=list)
    # load_mode controls when the plugin module is imported and instantiated:
    #
    #   "lazy"    (default) import is deferred until the first get_plugin() call.
    #                Keeps startup fast; ideal for optional features.
    #
    #   "startup" imported and instantiated during register_plugins(), before
    #                Flask handles its first request. Required for plugins that:
    #                - register a Blueprint (routes must exist before routing starts)
    #                - install flask-login handlers (SecurityPlugin / AuthView)
    #                - add menu items built at __init__ time
    #                - start background threads or hold shared resources
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
    """Runtime plugin record managed by ``ModernPluginManager``."""
    metadata: PluginMetadata
    state: PluginState = PluginState.UNLOADED
    instance: Optional[Any] = None
    load_time: Optional[float] = None
    error_message: Optional[str] = None
    last_access: Optional[float] = None

class PluginCache:
    """Simple JSON-backed cache for discovered plugin metadata."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "plugin_cache.json"
        self._cache_lock = threading.RLock()

    def get_cache_key(self, entry_point_group: str) -> str:
        """Build a stable cache key for an entry-point group."""
        return hashlib.md5(entry_point_group.encode()).hexdigest()

    def load_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Load cached plugin metadata from disk."""
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
        """Persist discovered plugin metadata to disk."""
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
        """Remove the plugin metadata cache file."""
        try:
            with self._cache_lock:
                if self.cache_file.exists():
                    self.cache_file.unlink()
        except Exception:
            pass

class PluginLoader:
    """Discovery and import helper for plugin entry points."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.logger = log.get_logger(self.__class__.__name__, level=logging.INFO)
        self.cache = PluginCache(cache_dir or Path.cwd() / ".plugin_cache")
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="PluginLoader")
        # Cache live EntryPoint objects keyed by plugin name.
        # Populated unconditionally in discover_plugins() Step 1 (before file-cache
        # restore), so load_plugin_class() can always call ep.load() on every startup.
        self._entry_points: Dict[str, Any] = {}

    def discover_plugins(self, group: str, force_refresh: bool = False) -> Dict[str, PluginMetadata]:
        """Discover plugins for an entry-point group and cache their metadata.

        Note: the old ``@lru_cache`` approach was removed because
        ``force_refresh=True`` must bypass stale results.

        ``entry_points(group=group)`` only reads distribution metadata. It does
        not import plugin modules, so it is safe to execute on every startup.
        We always enumerate live ``EntryPoint`` objects first so
        ``load_plugin_class()`` can call ``ep.load()`` reliably even when the
        richer metadata comes from the file cache.
        """
        cache_key = self.cache.get_cache_key(group)

        # Step 1: enumerate live entry points (fast, no imports).
        # Populates _entry_points so load_plugin_class() can call ep.load()
        # on every startup, not just the first one after cache creation.
        live_entry_points = entry_points(group=group)
        for ep in live_entry_points:
            self._entry_points[ep.name] = ep

        # Step 2: try the file cache for enriched ``PluginMetadata``.
        if not force_refresh:
            cached_data = self.cache.load_cache(cache_key)
            if cached_data:
                self.logger.debug(f"Loading plugin metadata from cache for group: {group}")
                field_names = set(PluginMetadata.__dataclass_fields__.keys())
                def _make_meta(d: Dict[str, Any]) -> PluginMetadata:
                    filtered = {k: v for k, v in d.items() if k in field_names}
                    # Backward compatibility for cache entries written before ``load_mode``.
                    # Old cache entries have lazy_load/immediate_load booleans instead of
                    # the load_mode string.  Map them so startup plugins are not silently
                    # downgraded to "lazy" after a schema change.
                    if 'load_mode' not in filtered:
                        if d.get('immediate_load', False):
                            filtered['load_mode'] = 'startup'
                        elif not d.get('lazy_load', True):
                            filtered['load_mode'] = 'startup'
                        # Otherwise leave it absent and use the dataclass default ``lazy``.
                    return PluginMetadata(**filtered)
                # _entry_points already populated above; return cached metadata
                return {name: _make_meta(metadata)
                       for name, metadata in cached_data.items()}

        # Step 3: live discovery by reading ``pyproject.toml`` metadata.
        self.logger.progress(f"Discovering plugins for group: {group}", key='discover_plugins')

        plugins = {}
        for entry_point in live_entry_points:
            try:
                # Do not import the plugin class yet; only collect metadata.
                metadata = self._extract_metadata(entry_point)
                plugins[entry_point.name] = metadata
            except Exception as e:
                self.logger.error("")
                self.logger.error(f"Failed to extract metadata from {entry_point.name}: {e}")
                self.logger.end_progress(key='discover_plugins')

        # Cache the discovery result.
        cache_data = {name: metadata.__dict__ for name, metadata in plugins.items()}
        self.cache.save_cache(cache_key, cache_data)
        self.logger.end_progress(f"Discovered {len(plugins)} plugins in group {group}.")

        return plugins

    def _extract_metadata(self, entry_point) -> PluginMetadata:
        """Extract plugin metadata without importing the plugin class.

        Reads the package's pyproject.toml (for editable/dev installs) to pick
        up ``[tool.funlab_plugin_metadata.<PluginName>]`` declarations:
          - dependencies          required sibling plugin names
          - optional_dependencies soft-required sibling plugin names
          - load_mode             "lazy" (default) | "startup"
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
                        # Otherwise keep the dataclass default ``lazy``.
                        self.logger.debug(
                            f"Plugin '{entry_point.name}' metadata enriched from pyproject.toml: {plugin_meta}"
                        )
                else:
                    # Fallback: try to read pyproject.toml from the installed
                    # distribution files (some installs expose files via dist.files)
                    try:
                        import tomllib
                        dist = entry_point.dist
                        files = list(dist.files or [])
                        candidates = [p for p in files if p.name == 'pyproject.toml']
                        if candidates:
                            # take the first candidate
                            rel = str(candidates[0])
                            try:
                                raw = dist.read_text(rel)
                            except Exception:
                                raw = None
                            if raw:
                                # dist.read_text returns str for text files
                                toml_data = tomllib.loads(raw)
                                plugin_meta = (
                                    toml_data
                                    .get('tool', {})
                                    .get('funlab_plugin_metadata', {})
                                    .get(entry_point.name, {})
                                )
                                if plugin_meta:
                                    metadata.dependencies = plugin_meta.get('dependencies', [])
                                    metadata.optional_dependencies = plugin_meta.get('optional_dependencies', [])
                                    if 'load_mode' in plugin_meta:
                                        metadata.load_mode = plugin_meta['load_mode']
                                    elif plugin_meta.get('immediate_load', False):
                                        metadata.load_mode = 'startup'
                                    elif not plugin_meta.get('lazy_load', True):
                                        metadata.load_mode = 'startup'
                                    self.logger.debug(
                                        f"Plugin '{entry_point.name}' metadata enriched from dist pyproject.toml: {plugin_meta}"
                                    )
                    except Exception as exc:
                        self.logger.debug(f"Fallback pyproject read failed for {entry_point.name}: {exc}")
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
                    # On Windows: /D:/foo -> D:/foo
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
            f"  -> Required (directly or transitively) by plugin module: '{plugin_module}'",
        ]
        lines.append(
            f"  -> Install the package that provides '{missing_module}'."
        )
        return "\n".join(lines)

    def load_plugin_class(self, entry_point_name: str, metadata: PluginMetadata) -> Any:
        """Load a plugin class directly without using the thread pool.

        Uses ``EntryPoint.load()`` which is the standard packaging API:
            ep.load() == getattr(importlib.import_module(ep.module), ep.attr)

        First call pays disk-I/O + bytecode cost; subsequent calls for the same
        module are a free ``sys.modules`` dict lookup (~1µs).

        ``_entry_points`` is always populated in ``discover_plugins()`` Step 1
        (before file-cache restore), so ``ep`` is never None in normal flow.
        """
        try:
            self.logger.progress(f"Loading plugin class: {entry_point_name}...", key='load_plugin_class')
            ep: EntryPoint = self._entry_points.get(entry_point_name)
            if ep is None:
                raise RuntimeError(
                    f"EntryPoint '{entry_point_name}' not found in _entry_points. "
                    f"This should not happen; ensure discover_plugins() was called first."
                )
            plugin_class = ep.load()
            self.logger.end_progress(f"Plugin class {entry_point_name} loaded.", key='load_plugin_class')
            return plugin_class

        except ModuleNotFoundError as e:
            import traceback
            missing = e.name or str(e)
            plugin_module = metadata.entry_point.split(':')[0]
            hint = self._format_module_not_found_hint(missing, plugin_module)
            self.logger.warning("")
            self.logger.warning(
                f"[PluginLoader] Plugin '{entry_point_name}' skipped. {hint}"
            )
            self.logger.end_progress(key='load_plugin_class')
            self.logger.debug(f"Full traceback for {entry_point_name}:\n{traceback.format_exc()}")
            raise
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            self.logger.error("")
            self.logger.error(f"Failed to load plugin {entry_point_name}: {e}")
            self.logger.error(f"Full traceback for {entry_point_name}:\n{error_detail}")
            self.logger.end_progress(key='load_plugin_class')
            raise

    def load_plugin_async(self, entry_point_name: str, metadata: PluginMetadata) -> Any:
        """Submit plugin-class loading to the thread pool for background work.

        The synchronous path should call ``load_plugin_class()`` directly to avoid
        unnecessary thread context switching. This method is reserved for cases
        where true background preloading is desired.
        """
        future = self._executor.submit(self.load_plugin_class, entry_point_name, metadata)
        return future

    def shutdown(self):
        """Shut down the background loader executor."""
        self._executor.shutdown(wait=True)


class PluginDependencyResolver:
    """Resolve plugin load order from hard and optional dependencies."""

    def __init__(self):
        self.logger = log.get_logger(self.__class__.__name__)

    def resolve_load_order(self, plugins: Dict[str, PluginMetadata]) -> List[str]:
        """Resolve plugin load order with hard and optional dependencies.

        * ``dependencies``          hard dependencies; missing ones disable the plugin
        * ``optional_dependencies`` soft dependencies; missing ones only degrade features
        Both affect topological sort so that when a dependency IS present it is
        loaded before the dependent plugin.
        """
        # Validate hard dependencies and remove plugins with missing hard deps.
        # Start from the set of declared plugins and iteratively remove any
        # plugin that has a hard dependency not present in the current set.
        available = set(plugins.keys())
        removed: Set[str] = set()

        while True:
            to_remove: Set[str] = set()
            for plugin_name in list(available):
                metadata = plugins.get(plugin_name)
                if not metadata:
                    continue
                # If any hard dependency is not in the currently available set,
                # this plugin cannot be satisfied and must be removed.
                for dep in metadata.dependencies:
                    if dep not in available:
                        to_remove.add(plugin_name)
                        break

            if not to_remove:
                break
            # Log and apply removals
            for p in to_remove:
                missing = [d for d in plugins[p].dependencies if d not in available]
                self.logger.warning(
                    f"Plugin '{p}' has missing REQUIRED dependencies {missing}; skipping {p}."
                )
            available -= to_remove
            removed |= to_remove

        # If everything was removed, nothing to load
        if not available:
            return []

        # Build a reduced view of plugins limited to available ones
        reduced_plugins = {name: plugins[name] for name in available}

        # Topological sort (DFS) over the reduced graph.
        visited: set[str] = set()
        temp_visited: set[str] = set()
        result: list[str] = []

        def visit(plugin_name: str):
            if plugin_name in temp_visited:
                raise ValueError(f"Circular dependency detected involving '{plugin_name}'")
            if plugin_name in visited:
                return

            temp_visited.add(plugin_name)
            metadata = reduced_plugins.get(plugin_name)
            if metadata:
                # Hard deps first (guaranteed to be in reduced_plugins)
                for dep in metadata.dependencies:
                    if dep in reduced_plugins:
                        visit(dep)
                # Optional deps: attempt to visit when available, otherwise INFO
                for dep in metadata.optional_dependencies:
                    if dep in reduced_plugins:
                        visit(dep)
                    else:
                        self.logger.info(
                            f"Plugin '{plugin_name}': optional plugin dependency '{dep}' is not installed; features degraded."
                        )

            temp_visited.discard(plugin_name)
            visited.add(plugin_name)
            result.append(plugin_name)

        # Sort alphabetically for determinism; DFS will enforce dependency ordering
        sorted_plugins = sorted(reduced_plugins.items(), key=lambda x: x[0])
        for plugin_name, _ in sorted_plugins:
            if plugin_name not in visited:
                visit(plugin_name)

        return result


class ModernPluginManager:
    """Modern plugin manager responsible for discovery, loading, and cleanup."""

    def __init__(self, app: FunlabFlask, cache_dir: Optional[Path] = None):
        self.app = app
        self.logger = log.get_logger(self.__class__.__name__, level=logging.INFO)

        # Core plugin-management state.
        self.plugins: Dict[str, PluginInfo] = {}
        self.plugin_loader = PluginLoader(cache_dir)
        self.dependency_resolver = PluginDependencyResolver()

        # Performance metrics.
        self._access_times: Dict[str, float] = {}
        self._load_stats: Dict[str, Dict[str, Any]] = {}

        # Thread safety.
        self._lock = threading.RLock()

        # Lazy-loading state.
        self._lazy_plugins: Set[str] = set()
        self._active_plugins: Set[str] = set()

    def register_plugins(self, group: str = 'funlab_plugin',
                        force_refresh: bool = False):
        """Register plugins for the configured entry-point group."""
        start_time = time.time()
        self.logger.progress(f"Starting plugin registration for group: {group}", key='register_plugins')
        # Discover plugins.
        discovered_plugins = self.plugin_loader.discover_plugins(group, force_refresh)
        # Debug: dump discovered plugin metadata for troubleshooting dependency issues
        try:
            import logging as _logging
            if self.logger and self.logger.isEnabledFor(_logging.INFO):
                for _name, _meta in discovered_plugins.items():
                    self.logger.debug(
                        f"Discovered plugin metadata: {_name} -> load_mode={_meta.load_mode}, "
                        f"dependencies={_meta.dependencies}, optional_dependencies={_meta.optional_dependencies}, "
                        f"entry_point={_meta.entry_point}"
                    )
        except Exception:
            # Never fail registration because of logging
            pass
        # Resolve load order from dependency declarations.
        load_order = self.dependency_resolver.resolve_load_order(discovered_plugins)
        self.logger.info(f"Plugin load order: {load_order}")

        # Create runtime plugin records.
        for plugin_name in load_order:
            if plugin_name in discovered_plugins:
                metadata = discovered_plugins[plugin_name]
                plugin_info = PluginInfo(metadata=metadata)
                self.plugins[plugin_name] = plugin_info

                # Choose startup or lazy loading based on metadata.
                #
                # metadata.load_mode (set in pyproject.toml):
                #
                #  "lazy"    (default) adds to _lazy_plugins; module is not imported yet.
                #                        Triggered on first get_plugin() call.
                #
                #  "startup" imports and instantiates immediately, before Flask handles
                #               its first request.  Required for Blueprints,
                #               login handlers, menus, background services.
                #
                # Backward compatibility: legacy booleans are mapped to startup mode.

                if metadata.load_mode == 'startup':
                    self._load_plugin_sync(plugin_name)
                else:
                    self._lazy_plugins.add(plugin_name)
                    self.logger.debug(f"Plugin {plugin_name} deferred (lazy)")

        self.logger.end_progress(f"Plugin registration completed.", key='register_plugins')

        # Log summary statistics.
        self._log_plugin_stats()

    def _load_plugin_sync(self, plugin_name: str) -> bool:
        """Synchronously load a plugin instance.

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

                # Load directly without thread-pool overhead.
                plugin_class = self.plugin_loader.load_plugin_class(plugin_name, plugin_info.metadata)

                # Instantiate the plugin.
                plugin_instance = plugin_class(self.app)

                plugin_info.instance = plugin_instance
                plugin_info.state = PluginState.LOADED
                plugin_info.load_time = time.time() - start_time
                plugin_info.error_message = None

                # Register with the Flask app.
                self._register_plugin_to_flask(plugin_name, plugin_instance)

                # Start plugin lifecycle so health/metrics semantics align with UI.
                start_ok = True
                if hasattr(plugin_instance, 'start'):
                    start_ok = bool(plugin_instance.start())

                if not start_ok:
                    plugin_info.state = PluginState.ERROR
                    plugin_info.error_message = 'Plugin start() returned False'
                    self._active_plugins.discard(plugin_name)
                    return False

                plugin_info.state = PluginState.ACTIVE
                self._active_plugins.add(plugin_name)
                return True

            except ModuleNotFoundError as e:
                missing = e.name or str(e)
                plugin_module = plugin_info.metadata.entry_point.split(':')[0]
                hint = self.plugin_loader._format_module_not_found_hint(missing, plugin_module)
                plugin_info.state = PluginState.DISABLED
                plugin_info.error_message = f"Missing module: {missing}"
                self.logger.warning(
                    f"[ModernPluginManager] Plugin '{plugin_name}' disabled. {hint}"
                )
                return False

            except Exception as e:
                import traceback
                error_detail = traceback.format_exc()
                plugin_info.state = PluginState.ERROR
                plugin_info.error_message = str(e)
                # If an exception happened after the plugin instance was created
                # ensure we do not leave a partially-registered instance in the
                # app mapping or in-memory state.
                if 'plugin_instance' in locals() and plugin_instance is not None:
                    try:
                        # Remove from app.plugins if it points to this instance
                        name = getattr(plugin_instance, 'name', None)
                        if name and self.app.plugins.get(name) is plugin_instance:
                            del self.app.plugins[name]
                        if plugin_name in self.app.plugins and self.app.plugins.get(plugin_name) is plugin_instance:
                            del self.app.plugins[plugin_name]
                    except Exception:
                        pass
                    plugin_info.instance = None
                self.logger.error(f"Failed to load plugin {plugin_name}: {e}")
                self.logger.error(f"Full traceback for {plugin_name}:\n{error_detail}")
                return False

    def get_plugin(self, plugin_name: str) -> Optional[Any]:
        """Get a plugin instance, loading it lazily if necessary."""
        with self._lock:
            plugin_info = self.plugins.get(plugin_name)
            if not plugin_info:
                return None

            # Record access time.
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

    def load_plugin(self, plugin_name: str) -> bool:
        """Load a plugin explicitly.

        Returns True when the plugin exists and is active after this call.
        """
        with self._lock:
            plugin_info = self.plugins.get(plugin_name)
            if not plugin_info:
                return False
            if plugin_info.state == PluginState.ACTIVE and plugin_info.instance is not None:
                return True

        return self._load_plugin_sync(plugin_name)

    def peek_plugin(self, plugin_name: str) -> Optional[Any]:
        """Return active plugin instance without triggering lazy loading."""
        with self._lock:
            plugin_info = self.plugins.get(plugin_name)
            if not plugin_info:
                return None
            return plugin_info.instance if plugin_info.state == PluginState.ACTIVE else None

    def get_plugin_state(self, plugin_name: str) -> Optional[str]:
        """Return the current plugin state value or None when plugin is unknown."""
        with self._lock:
            plugin_info = self.plugins.get(plugin_name)
            if not plugin_info:
                return None
            return plugin_info.state.value

    def unload_plugin(self, plugin_name: str) -> bool:
        """Unload a plugin instance and reset runtime state."""
        with self._lock:
            plugin_info = self.plugins.get(plugin_name)
            if not plugin_info:
                return False

            try:
                if plugin_info.instance is not None:
                    instance = plugin_info.instance
                    if hasattr(instance, 'stop'):
                        instance.stop()
                    elif hasattr(instance, 'unload'):
                        instance.unload()

                # Remove any mapping from the Flask app that points to this instance
                try:
                    name = getattr(instance, 'name', None)
                    if name and self.app.plugins.get(name) is instance:
                        del self.app.plugins[name]
                    if plugin_name in self.app.plugins and self.app.plugins.get(plugin_name) is instance:
                        del self.app.plugins[plugin_name]
                except Exception:
                    pass

                plugin_info.instance = None
                plugin_info.state = PluginState.UNLOADED
                plugin_info.error_message = None
                self._active_plugins.discard(plugin_name)
                return True
            except Exception as e:
                plugin_info.state = PluginState.ERROR
                plugin_info.error_message = str(e)
                self._active_plugins.discard(plugin_name)
                self.logger.error(f"Failed to unload plugin {plugin_name}: {e}")
                return False

    def _register_plugin_to_flask(self, plugin_name: str, plugin_instance: Any):
        """Register a plugin instance with the Flask application."""
        # Preserve the app's legacy registration flow.
        self.app.plugins[plugin_instance.name] = plugin_instance

        if blueprint := getattr(plugin_instance, 'blueprint', None):
            # Check whether Flask has already started serving requests.
            try:
                self.app.register_blueprint(blueprint)
                self.logger.debug(f"Blueprint registered for plugin {plugin_name}")
            except AssertionError as e:
                if "has already handled its first request" in str(e):
                    self.logger.warning(f"Cannot register blueprint for plugin {plugin_name}: "
                                      f"Flask app has already started. Plugin functionality will be limited.")
                    # Mark that the plugin blueprint could not be registered after startup.
                    plugin_instance._blueprint_registered = False
                else:
                    raise
            else:
                plugin_instance._blueprint_registered = True

        # Create SQLAlchemy registry tables for the plugin if needed.
        if hasattr(plugin_instance, 'entities_registry') and plugin_instance.entities_registry:
            self.app.dbmgr.create_registry_tables(plugin_instance.entities_registry)

        # Check for SecurityPlugin to initialise flask-login
        from funlab.core.plugin import SecurityPlugin

        if isinstance(plugin_instance, SecurityPlugin):
            # Allow a security plugin to replace the default login manager.
            if self.app.login_manager is not None and hasattr(self.app.login_manager, '_default_user_loader'):
                # Replace the bootstrap default login manager with the security plugin.
                self.logger.info(f"Replacing default login_manager with SecurityPlugin: {plugin_name}")
                self.app.login_manager = plugin_instance.login_manager
                self.app.login_manager.init_app(self.app)
            elif self.app.login_manager is None:
                # First security plugin installs the login manager.
                self.logger.info(f"Installing SecurityPlugin login_manager: {plugin_name}")
                self.app.login_manager = plugin_instance.login_manager
                self.app.login_manager.init_app(self.app)
            else:
                # Another security plugin is already installed; warn but keep routes.
                self.logger.warning(f"SecurityPlugin already installed, but continuing to register routes for {plugin_name}")

            # 設置blueprint-specific login view
            if hasattr(plugin_instance, 'login_view') and plugin_instance.login_view:
                if not hasattr(self.app.login_manager, 'blueprint_login_views'):
                    self.app.login_manager.blueprint_login_views = {}
                self.app.login_manager.blueprint_login_views[plugin_instance.bp_name] = plugin_instance.login_view

            # When AuthView is present, set the global default login view.
            # if plugin_name == 'AuthView':
            #     self.app.login_manager.login_view = f'{plugin_instance.bp_name}.login'
            #     self.logger.info(f"Set global login_view to: {self.app.login_manager.login_view}")

        # Menus are built during plugin initialization; no extra call is needed here.

    def reload_plugin(self, plugin_name: str) -> bool:
        """Reload a plugin instance in-place to avoid Flask blueprint re-registration conflicts."""
        with self._lock:
            plugin_info = self.plugins.get(plugin_name)
            if not plugin_info:
                return False

            try:
                # If not loaded yet, treat reload as load.
                if plugin_info.instance is None:
                    return self._load_plugin_sync(plugin_name)

                plugin_instance = plugin_info.instance
                success = False

                if hasattr(plugin_instance, 'reload'):
                    success = bool(plugin_instance.reload())
                else:
                    stop_ok = bool(plugin_instance.stop()) if hasattr(plugin_instance, 'stop') else True
                    start_ok = bool(plugin_instance.start()) if hasattr(plugin_instance, 'start') else True
                    success = bool(stop_ok and start_ok)

                if success:
                    plugin_info.state = PluginState.ACTIVE
                    plugin_info.error_message = None
                    self._active_plugins.add(plugin_name)
                    return True

                plugin_info.state = PluginState.ERROR
                plugin_info.error_message = 'Plugin reload() returned False'
                self._active_plugins.discard(plugin_name)
                return False

            except Exception as e:
                plugin_info.state = PluginState.ERROR
                plugin_info.error_message = str(e)
                self._active_plugins.discard(plugin_name)
                self.logger.error(f"Failed to reload plugin {plugin_name}: {e}")
                return False

    def get_plugin_stats(self) -> Dict[str, Any]:
        """Return plugin statistics for monitoring and debugging."""
        active_count = sum(1 for p in self.plugins.values() if p.state == PluginState.ACTIVE)
        unloaded_count = sum(1 for p in self.plugins.values() if p.state == PluginState.UNLOADED)
        loaded_count = sum(1 for p in self.plugins.values() if p.state == PluginState.LOADED)
        loading_count = sum(1 for p in self.plugins.values() if p.state == PluginState.LOADING)
        stats = {
            'total_plugins': len(self.plugins),
            'active_plugins': active_count,
            'lazy_plugins': len(self._lazy_plugins),
            'unloaded_plugins': unloaded_count,
            'loaded_plugins': loaded_count,
            'loading_plugins': loading_count,
            'error_plugins': len([p for p in self.plugins.values() if p.state == PluginState.ERROR]),
            'disabled_plugins': len([p for p in self.plugins.values() if p.state == PluginState.DISABLED]),
            'plugins': {}
        }

        for name, info in self.plugins.items():
            stats['plugins'][name] = {
                'state': info.state.value,
                'load_time': info.load_time,
                'last_access': info.last_access,
                'error_message': info.error_message,
                'load_mode': info.metadata.load_mode
            }

        return stats

    def _log_plugin_stats(self):
        """Log plugin statistics."""
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
        """Clean up loaded plugins and the background loader."""
        self.logger.info("Cleaning up plugin manager...")
        # Unload plugins in reverse order using the dedicated helper.
        for plugin_name in reversed(list(self.plugins.keys())):
            try:
                unloaded = self.unload_plugin(plugin_name)
                if not unloaded:
                    self.logger.debug(f"unload_plugin returned False for {plugin_name}")
            except Exception as e:
                self.logger.error(f"Error unloading plugin {plugin_name}: {e}", exc_info=True)

        # Shut down the plugin loader.
        try:
            self.plugin_loader.shutdown()
        except Exception as e:
            self.logger.error(f"Error shutting down plugin loader: {e}")

        self.logger.info("Plugin manager cleanup completed")
