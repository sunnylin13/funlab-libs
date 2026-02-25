# Plugin Lifecycle: Practical Code Examples

This document provides concrete, real-world code examples for each layer of the three-layer hook architecture.

---

## Layer 1: Template Method — Plugin-Owned Logic

### Example 1: Simple Service Plugin

```python
from funlab.core.enhanced_plugin import EnhancedServicePlugin

class ReportGeneratorService(EnhancedServicePlugin):
    """Service that generates reports on a schedule."""

    def _on_start(self):
        """Called when service is started."""
        self.logger.info("Starting report generator service")

        # Initialize resources
        self.db_pool = DatabaseConnectionPool(max_connections=10)
        self.report_queue = Queue()

        # Start background worker thread
        self.worker_thread = Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        self.logger.info("Report generator service started successfully")

    def _on_stop(self):
        """Called when service is stopped."""
        self.logger.info("Stopping report generator service")

        # Graceful shutdown
        self.stop_event.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=10)

        # Close resources
        if self.db_pool:
            self.db_pool.close()

        self.logger.info("Report generator service stopped")

    def _on_reload(self):
        """Called between stop() and start() during reload."""
        self.logger.info("Reloading report generator configuration")

        # Reload config from file
        self.config = self.load_config()

        # Rebuild internal state
        self.report_templates = self.compile_templates()

    def _worker_loop(self):
        """Background worker that processes report requests."""
        while not self.stop_event.is_set():
            try:
                report_request = self.report_queue.get(timeout=5)
                self._generate_report(report_request)
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error(f"Error generating report: {e}")


# Usage: The framework automatically calls _on_start, _on_stop, _on_reload
#
# app.plugins['ReportGenerator'].start()   # Calls _on_start()
# app.plugins['ReportGenerator'].stop()    # Calls _on_stop()
# app.plugins['ReportGenerator'].reload()  # Calls _on_stop() -> _on_reload() -> _on_start()
```

### Example 2: View Plugin with Database Setup

```python
from funlab.core.enhanced_plugin import EnhancedViewPlugin

class UserManagementView(EnhancedViewPlugin):
    """Plugin that manages user administration interface."""

    def _on_start(self):
        """Initialize database tables and register data models."""
        self.logger.info("Setting up user management plugin")

        # Ensure database tables exist
        with self.app.dbmgr.session_context() as session:
            # Create ORM table if not exists
            self.entities_registry.create_all(session.bind)

            # Seed default roles if empty
            if session.query(Role).count() == 0:
                self._initialize_default_roles(session)
                session.commit()

        # Register routes (blueprint already registered during __init__)
        self._register_api_endpoints()

        self.logger.info("User management plugin ready")

    def _perform_health_check(self) -> bool:
        """Custom health check for this plugin."""
        try:
            # Can we reach the database?
            with self.app.dbmgr.session_context() as session:
                session.execute("SELECT 1")
                return True
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return False

    @property
    def entities_registry(self):
        """Tables managed by this plugin."""
        from .models import Role, Permission, UserRole
        return Base.registry  # SQLAlchemy registry


# Usage:
#
# app.plugins['UserManagement'].start()        # Calls _on_start()
# status = app.plugins['UserManagement'].health_check()  # Custom check
```

### Example 3: Security Plugin with Custom Reload

```python
from funlab.core.enhanced_plugin import EnhancedSecurityPlugin

class OAuthSecurityPlugin(EnhancedSecurityPlugin):
    """OAuth-based authentication plugin."""

    def _on_start(self):
        """Initialize OAuth clients and session storage."""
        self.logger.info("Starting OAuth security plugin")

        # Load OAuth configuration
        oauth_config = self.plugin_config.get('oauth', {})

        # Initialize OAuth clients
        self.oauth = {
            'github': GitHubOAuth(
                client_id=oauth_config['github']['client_id'],
                client_secret=oauth_config['github']['client_secret']
            ),
            'google': GoogleOAuth(
                client_id=oauth_config['google']['client_id'],
                client_secret=oauth_config['google']['client_secret']
            )
        }

        # Initialize session backend
        self.session_backend = RedisSessionBackend(
            host=oauth_config.get('redis_host', 'localhost')
        )

        self.logger.info("OAuth security plugin started")

    def _on_stop(self):
        """Revoke sessions and cleanup OAuth resources."""
        self.logger.info("Stopping OAuth security plugin")

        # Revoke all active sessions
        if self.session_backend:
            self.session_backend.revoke_all()

        # Cleanup OAuth clients
        for client_name, client in self.oauth.items():
            try:
                client.cleanup()
            except Exception as e:
                self.logger.warning(f"Error cleaning up {client_name}: {e}")

        self.logger.info("OAuth security plugin stopped")

    def _on_reload(self):
        """Reload OAuth configuration without losing active sessions."""
        self.logger.info("Reloading OAuth configuration")

        # Reload config from file
        oauth_config = self.plugin_config.get('oauth', {})

        # Update OAuth client secrets (useful if they're rotated externally)
        for provider in self.oauth:
            if provider in oauth_config:
                self.oauth[provider].update_credentials(
                    oauth_config[provider]
                )

        self.logger.info("OAuth configuration reloaded")


# Usage:
#
# app.plugins['OAuth'].start()           # Full init
# app.plugins['OAuth'].reload()          # Reload config without losing sessions
```

---

## Layer 2: Instance Hooks — Dynamic Per-Plugin Monitoring

### Example 1: Plugin Dependency Manager

```python
from funlab.core.enhanced_plugin import PluginLifecycleState

class PluginDependencyManager:
    """
    Ensures plugins respect their dependencies at runtime.
    Uses Layer 2 instance hooks to monitor specific plugins.
    """

    # Define which plugins depend on which
    DEPENDENCIES = {
        'Option': ['Quote'],           # OptionView depends on QuoteService
        'FundMgr': ['Quote'],          # FundMgrView depends on QuoteService
        'SSE': ['Auth'],               # SSEService depends on AuthView
    }

    def __init__(self, app, plugin_manager):
        self.app = app
        self.plugin_manager = plugin_manager
        self.logger = logging.getLogger(__name__)

    def setup_dependency_hooks(self):
        """Register instance hooks on all dependent plugins."""
        for dependent, dependencies in self.DEPENDENCIES.items():
            plugin = self.plugin_manager.get_plugin(dependent)
            if plugin:
                # Register hook on this specific plugin
                plugin.add_lifecycle_hook(
                    'before_start',
                    self._make_dependency_checker(dependent, dependencies)
                )
                self.logger.info(f"Dependency check registered for {dependent}")

    def _make_dependency_checker(self, dependent_name, dependencies):
        """Create a closure that checks dependencies."""
        def check_dependencies():
            missing = []
            for dep_name in dependencies:
                dep_plugin = self.plugin_manager.get_plugin(dep_name)
                if not dep_plugin:
                    missing.append(f"{dep_name} (not installed)")
                elif dep_plugin.state != PluginLifecycleState.RUNNING:
                    missing.append(f"{dep_name} (state: {dep_plugin.state.value})")

            if missing:
                raise RuntimeError(
                    f"Cannot start {dependent_name}: "
                    f"missing dependencies: {', '.join(missing)}"
                )

            self.logger.info(f"{dependent_name}: all dependencies satisfied")

        return check_dependencies

# Usage in app initialization:
#
# dep_mgr = PluginDependencyManager(app, plugin_manager)
# dep_mgr.setup_dependency_hooks()
#
# # Now if user tries: app.plugins['Option'].start() without Quote running
# # → Error: "Cannot start Option: missing dependencies: Quote (state: ready)"
```

### Example 2: Dashboard Real-Time Monitor

```python
class DashboardMonitor:
    """
    Monitors plugin lifecycle and broadcasts updates to connected web clients.
    Uses Layer 2 instance hooks to track individual plugins.
    """

    def __init__(self, app):
        self.app = app
        self.plugin_states = {}
        self.websocket_clients = set()
        self.logger = logging.getLogger(__name__)

    def register_all_plugin_monitors(self):
        """Set up instance hooks on all plugins."""
        for plugin_name, plugin in self.app.plugins.items():
            # Monitor state changes for THIS plugin
            plugin.add_lifecycle_hook('before_start',
                lambda p=plugin_name: self._on_before_start(p))
            plugin.add_lifecycle_hook('after_start',
                lambda p=plugin_name: self._on_after_start(p))
            plugin.add_lifecycle_hook('before_stop',
                lambda p=plugin_name: self._on_before_stop(p))
            plugin.add_lifecycle_hook('after_stop',
                lambda p=plugin_name: self._on_after_stop(p))
            plugin.add_lifecycle_hook('on_error',
                lambda e, p=plugin_name: self._on_error(p, e))

            # Initialize state
            self.plugin_states[plugin_name] = plugin.state.value

    def _on_before_start(self, plugin_name):
        self.plugin_states[plugin_name] = 'starting'
        self._broadcast({
            'plugin': plugin_name,
            'state': 'starting',
            'timestamp': time.time()
        })

    def _on_after_start(self, plugin_name):
        self.plugin_states[plugin_name] = 'running'
        self._broadcast({
            'plugin': plugin_name,
            'state': 'running',
            'timestamp': time.time(),
            'uptime': self.app.plugins[plugin_name].health.uptime
        })

    def _on_before_stop(self, plugin_name):
        self.plugin_states[plugin_name] = 'stopping'
        self._broadcast({
            'plugin': plugin_name,
            'state': 'stopping',
            'timestamp': time.time()
        })

    def _on_after_stop(self, plugin_name):
        self.plugin_states[plugin_name] = 'stopped'
        self._broadcast({
            'plugin': plugin_name,
            'state': 'stopped',
            'timestamp': time.time()
        })

    def _on_error(self, plugin_name, error):
        self.plugin_states[plugin_name] = 'error'
        self._broadcast({
            'plugin': plugin_name,
            'state': 'error',
            'error': str(error),
            'timestamp': time.time()
        })

    def _broadcast(self, message):
        """Send state change to all connected WebSocket clients."""
        # In real implementation: use flask-socketio or websockets
        for client in self.websocket_clients:
            try:
                client.send(json.dumps(message))
            except Exception as e:
                self.logger.error(f"Failed to broadcast to client: {e}")

# Usage:
#
# monitor = DashboardMonitor(app)
# monitor.register_all_plugin_monitors()
#
# # Now whenever any plugin starts/stops, all dashboard clients get real-time update
```

### Example 3: Plugin-to-Plugin Communication

```python
class QuoteDataFeedPlugin(EnhancedServicePlugin):
    """Provides real-time quote data feed."""

    def _on_start(self):
        self.logger.info("Starting quote feed")
        self.data_consumer = DataStreamConsumer()
        self.data_consumer.connect()


class OptionAnalysisPlugin(EnhancedViewPlugin):
    """Uses quote data to analyze options."""

    def __init__(self, app, url_prefix=None):
        super().__init__(app, url_prefix)

        # Hook into quote service's lifecycle
        # (This is Layer 2: monitoring a specific plugin)
        quote_service = app.plugins.get('QuoteDataFeed')
        if quote_service:
            quote_service.add_lifecycle_hook(
                'after_start',
                self._on_quote_service_ready
            )
            quote_service.add_lifecycle_hook(
                'before_stop',
                self._on_quote_service_stopping
            )

    def _on_quote_service_ready(self):
        """Called when QuoteDataFeed service is ready."""
        self.logger.info("Quote service is ready, subscribing to data feed")
        quote_service = self.app.plugins['QuoteDataFeed']
        # Subscribe to data updates from quote service
        quote_service.subscribe('option_requests', self.on_quote_update)

    def _on_quote_service_stopping(self):
        """Called when QuoteDataFeed is about to stop."""
        self.logger.info("Quote service stopping, unsubscribing")
        quote_service = self.app.plugins['QuoteDataFeed']
        quote_service.unsubscribe('option_requests', self.on_quote_update)

    def on_quote_update(self, update):
        """Receive quote updates from the service."""
        # Process update
        pass

# Key difference from Layer 1:
# - OptionAnalysis NEEDS to monitor QuoteDataFeed (dynamic dependency)
# - But we don't want to bake this into OptionAnalysis's _on_start()
# - Use Layer 2: Option registers hook on Quote after instantiation
```

---

## Layer 3: Global Hooks — App-Level Infrastructure

### Example 1: Audit Logging System

```python
class AuditLogger:
    """
    Logs all plugin lifecycle events to audit database.
    Uses Layer 3 global hooks (broadcast to all plugins).
    """

    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger('audit')

    def register_hooks(self):
        """Register global hook handlers."""

        app.hook_manager.register_hook(
            'plugin_after_init',
            callback=self._log_plugin_init,
            priority=100
        )
        app.hook_manager.register_hook(
            'plugin_before_start',
            callback=self._log_plugin_before_start,
            priority=100
        )
        app.hook_manager.register_hook(
            'plugin_after_start',
            callback=self._log_plugin_after_start,
            priority=100
        )
        # ... similar for stop, reload, error ...

    def _log_plugin_init(self, context):
        """Log plugin initialization."""
        AuditLog.create(
            event='plugin_init',
            plugin_name=context['plugin_name'],
            user_id=get_current_user().id if has_request_context() else None,
            timestamp=time.time(),
            details={'version': context['plugin'].health.version}
        )

    def _log_plugin_before_start(self, context):
        """Log before plugin starts (for timing measurements)."""
        context['_audit_start_time'] = time.time()

    def _log_plugin_after_start(self, context):
        """Log after plugin starts (record actual startup duration)."""
        elapsed = time.time() - context.get('_audit_start_time', 0)
        AuditLog.create(
            event='plugin_start',
            plugin_name=context['plugin_name'],
            timestamp=time.time(),
            details={
                'startup_duration_sec': elapsed,
                'status': 'success'
            }
        )

# Usage in app initialization:
#
# audit = AuditLogger(app)
# audit.register_hooks()
#
# # Now EVERY plugin start/stop is automatically logged to AuditLog table
# # Audit system doesn't care about specific plugins, it watches ALL
```

### Example 2: Metrics Collection

```python
from prometheus_client import Counter, Histogram

class MetricsCollector:
    """
    Collects metrics on all plugin lifecycle events.
    Uses Layer 3 global hooks (one handler for all plugins).
    """

    # Prometheus metrics
    plugin_starts = Counter(
        'plugin_starts_total',
        'Total plugin starts',
        ['plugin_name']
    )
    plugin_stops = Counter(
        'plugin_stops_total',
        'Total plugin stops',
        ['plugin_name']
    )
    plugin_errors = Counter(
        'plugin_errors_total',
        'Total plugin errors',
        ['plugin_name', 'error_type']
    )
    plugin_startup_duration = Histogram(
        'plugin_startup_duration_seconds',
        'Plugin startup duration',
        ['plugin_name']
    )

    def __init__(self, app):
        self.app = app
        self.start_times = {}

    def register_hooks(self):
        """Register global hook handlers."""
        app.hook_manager.register_hook(
            'plugin_before_start',
            callback=self._on_before_start,
            priority=10  # Early priority to capture timing
        )
        app.hook_manager.register_hook(
            'plugin_after_start',
            callback=self._on_after_start,
            priority=900  # Late priority to capture full duration
        )
        app.hook_manager.register_hook(
            'plugin_before_stop',
            callback=self._on_before_stop,
            priority=10
        )
        app.hook_manager.register_hook(
            'plugin_after_stop',
            callback=self._on_after_stop,
            priority=900
        )

    def _on_before_start(self, context):
        plugin_name = context['plugin_name']
        self.start_times[plugin_name] = time.time()

    def _on_after_start(self, context):
        plugin_name = context['plugin_name']
        elapsed = time.time() - self.start_times.get(plugin_name, 0)

        self.plugin_starts.labels(plugin_name=plugin_name).inc()
        self.plugin_startup_duration.labels(plugin_name=plugin_name).observe(elapsed)

    def _on_before_stop(self, context):
        plugin_name = context['plugin_name']
        self.start_times[plugin_name] = time.time()

    def _on_after_stop(self, context):
        plugin_name = context['plugin_name']
        self.plugin_stops.labels(plugin_name=plugin_name).inc()

# Usage:
#
# metrics = MetricsCollector(app)
# metrics.register_hooks()
#
# # Metrics automatically collected; view at /metrics endpoint:
# # plugin_starts_total{plugin_name="Quote"} = 5
# # plugin_startup_duration_seconds{plugin_name="Quote"} = 1.234
```

### Example 3: Cascade/Dependent Startup

```python
class PluginStartupOrchestrator:
    """
    Manages cascading startup: when A starts, automatically start B and C.
    Uses Layer 3 global hooks.
    """

    STARTUP_ORDER = {
        'Quote': [],              # No dependencies
        'Option': ['Quote'],      # Start after Quote
        'FundMgr': ['Quote'],     # Start after Quote
        'SSE': ['Auth'],          # Start after Auth
    }

    def __init__(self, app, plugin_manager):
        self.app = app
        self.plugin_manager = plugin_manager
        self.logger = logging.getLogger(__name__)

    def register_hooks(self):
        """Register hook to orchestrate cascading starts."""
        app.hook_manager.register_hook(
            'plugin_after_start',
            callback=self._on_plugin_started,
            priority=500  # Mid priority (after audit, before health)
        )

    def _on_plugin_started(self, context):
        """When a plugin starts, start its dependents."""
        started_plugin = context['plugin_name']

        # Find who depends on the plugin that just started
        for plugin_name, dependencies in self.STARTUP_ORDER.items():
            if started_plugin in dependencies:
                dependents_ready = all(
                    self.plugin_manager.get_plugin(dep) and
                    self.plugin_manager.get_plugin(dep).state == PluginLifecycleState.RUNNING
                    for dep in dependencies
                )

                if dependents_ready:
                    # All dependencies are met, try to start
                    dependent = self.plugin_manager.get_plugin(plugin_name)
                    if dependent and dependent.state in [
                        PluginLifecycleState.READY,
                        PluginLifecycleState.STOPPED
                    ]:
                        self.logger.info(
                            f"Auto-starting {plugin_name} "
                            f"(all dependencies satisfied)"
                        )
                        dependent.start()

# Usage:
#
# orchestrator = PluginStartupOrchestrator(app, plugin_manager)
# orchestrator.register_hooks()
#
# # Now starting Quote automatically cascades to Option and FundMgr
# app.plugins['Quote'].start()  # Triggers auto-start of Option, FundMgr
```

### Example 4: Health Check Integration

```python
class PluginHealthMonitor:
    """
    Integrates plugins into centralized health monitoring.
    Uses Layer 3 global hooks to register/unregister from health system.
    """

    def __init__(self, app, health_check_service):
        self.app = app
        self.health_service = health_check_service
        self.logger = logging.getLogger(__name__)

    def register_hooks(self):
        """Register plugins in health check system as they start."""
        app.hook_manager.register_hook(
            'plugin_after_start',
            callback=self._on_plugin_started,
            priority=750  # Late priority (after normal operations)
        )
        app.hook_manager.register_hook(
            'plugin_before_stop',
            callback=self._on_plugin_stopping,
            priority=50   # Early priority (before normal cleanup)
        )

    def _on_plugin_started(self, context):
        """Register plugin in health check system."""
        plugin_name = context['plugin_name']
        plugin = context['plugin']

        self.health_service.register_check(
            name=f"plugin_{plugin_name}",
            check_func=lambda p=plugin: self._check_plugin_health(p),
            interval_sec=30,
            timeout_sec=5
        )
        self.logger.info(f"Registered health check for {plugin_name}")

    def _on_plugin_stopping(self, context):
        """Unregister plugin from health check system."""
        plugin_name = context['plugin_name']
        self.health_service.unregister_check(f"plugin_{plugin_name}")
        self.logger.info(f"Unregistered health check for {plugin_name}")

    def _check_plugin_health(self, plugin):
        """Perform health check on plugin."""
        return plugin.health_check()

# Usage:
#
# monitor = PluginHealthMonitor(app, health_service)
# monitor.register_hooks()
#
# # Now /health endpoint automatically tracks all running plugins
```

---

## Comparison: Which Layer to Use?

### Decision Flowchart

```
Does your code represent:

  ┌─ Plugin's own core behavior?
  │  YES → Layer 1 (override _on_start, _on_stop)
  │  NO  → Continue...
  │
  └─ Monitoring THIS specific plugin?
     YES → Layer 2 (add_lifecycle_hook on that plugin)
     NO  → Layer 3 (app.hook_manager, watch all plugins)
```

### Quick Reference

| Scenario | Layer | Example |
|----------|-------|---------|
| "Service needs to initialize database" | 1 | `_on_start()` creates tables |
| "Component X monitors Component Y starting" | 2 | Dashboard monitors Quote service |
| "All plugin starts logged to audit table" | 3 | AuditLogger |
| "Metrics collected for all plugins" | 3 | MetricsCollector |
| "Plugin A won't start until B is running" | 2 | DependencyManager |
| "Plugin auto-restart when dependency comes back" | 3 | PluginStartupOrchestrator |
| "Plugin gets notified of config reload" | 2 | OptionAnalysis listens to Quote |
| "Health check system tracks all plugins" | 3 | PluginHealthMonitor |

---

## Testing Each Layer

### Testing Layer 1 (Template Methods)

```python
def test_quote_service_startup():
    """Test that quote service initializes APIs on startup."""
    service = QuoteService(app)

    # Initially not connected
    assert service.quote_apis is None

    # Call start
    assert service.start() == True

    # Now APIs should be loaded
    assert service.quote_apis is not None
    assert len(service.quote_apis) > 0

    # Cleanup
    service.stop()


def test_quote_service_reload():
    """Test that reload updates config without losing state."""
    service = QuoteService(app)
    service.start()
    original_apis = service.quote_apis.copy()

    # Reload
    assert service.reload() == True

    # Should have new config
    assert service.quote_apis is not None
    # But still the same API instances
    assert id(original_apis) == id(service.quote_apis)

    service.stop()
```

### Testing Layer 2 (Instance Hooks)

```python
def test_instance_hook_called_on_start():
    """Test that instance hooks are called during plugin start."""
    plugin = MyPlugin(app)

    callback_called = []

    def my_callback():
        callback_called.append(True)

    plugin.add_lifecycle_hook('before_start', my_callback)

    plugin.start()

    assert callback_called == [True]


def test_multiple_instance_hooks():
    """Test that multiple hooks for same event all execute."""
    plugin = MyPlugin(app)

    call_order = []

    plugin.add_lifecycle_hook('after_start', lambda: call_order.append('first'))
    plugin.add_lifecycle_hook('after_start', lambda: call_order.append('second'))

    plugin.start()

    assert call_order == ['first', 'second']
```

### Testing Layer 3 (Global Hooks)

```python
def test_global_hook_called_for_all_plugins():
    """Test that global hooks execute for every plugin."""
    app.plugins['Plugin1'] = mock_plugin_1
    app.plugins['Plugin2'] = mock_plugin_2

    start_log = []

    def log_all_starts(context):
        start_log.append(context['plugin_name'])

    app.hook_manager.register_hook('plugin_after_start', log_all_starts)

    mock_plugin_1.start()
    mock_plugin_2.start()

    assert start_log == ['Plugin1', 'Plugin2']
```

---

## Migration Guide: From Layer 3 to Layer 2

If you currently use Layer 3 (global hooks) but realize you only care about specific plugins, consider switching to Layer 2:

### Before (Layer 3 — overly broad)

```python
def audit_plugin_starts(context):
    """Log EVERY plugin start—but we only care about specific ones."""
    plugin_name = context['plugin_name']

    if plugin_name not in MONITORED_PLUGINS:
        return  # Filtered at runtime ← inefficient

    # Log this one
    AuditLog.create(...)

app.hook_manager.register_hook('plugin_after_start', audit_plugin_starts)
```

### After (Layer 2 — targeted)

```python
class TargetedMonitor:
    def __init__(self, app):
        self.app = app

    def setup(self):
        # Only register hooks on plugins we care about
        for plugin_name in MONITORED_PLUGINS:
            plugin = self.app.plugins.get(plugin_name)
            if plugin:
                plugin.add_lifecycle_hook('after_start', self._log_start)

    def _log_start(self):
        AuditLog.create(...)  # No filtering needed

monitor = TargetedMonitor(app)
monitor.setup()
```

Benefits:
- More efficient (no filtering needed)
- Clearer intent (only monitoring specific plugins)
- Easier to add/remove from monitoring list
