# Plugin Lifecycle Architecture: Three-Layer Hook Design

## Executive Summary

Funlab's plugin system implements a **three-layer lifecycle hook architecture** that combines industry best practices from Django, pytest, and Flask. This document clarifies:

1. **Why** we need three layers (they are **not redundant**)
2. **When** to use each layer
3. **Practical examples** of each layer in action
4. **Migration path** for Layer 2 (currently unused)

---

## Part 1: The Three-Layer Model

### Quick Comparison Table

| **Layer** | **Mechanism** | **Scope** | **Frequency** | **Use When** | **Current Status** |
|-----------|---|---|---|---|---|
| **Layer 1** | Template Method | Per-plugin class | Plugin lifecycle | Plugin needs core behavior override | ✅ In use (3 services) |
| **Layer 2** | Instance Hooks | Per-plugin instance | Dynamic runtime | External code monitors *specific* plugin | ⚠️ **Defined but unused** |
| **Layer 3** | Global Hooks | App level | Broadcast | App monitors *all* plugins + cross-cutting concerns | ✅ In use (init, start, stop, reload) |

---

## Part 2: Layer 1 — Template Method Pattern

### What It Is
**Subclasses override protected methods** to inject plugin-specific logic.

```python
class MyServicePlugin(EnhancedServicePlugin):
    def _on_start(self):
        """Plugin's core startup logic."""
        self._load_config()
        self._connect_to_database()
        self._start_background_worker()

    def _on_stop(self):
        """Plugin's core shutdown logic."""
        self._stop_background_worker()
        self._disconnect_from_database()

    def _on_reload(self):
        """Re-initialize state between stop and start."""
        self._reload_config()
        self._rebuild_cache()
```

### Characteristics
- **Bound at class definition** (not runtime)
- **Type-safe** — IDE and static analysis can verify
- **Mandatory** — plugin must decide what to do
- **One per plugin** — single implementation of each template

### Real-World Usage in Funlab

| Service | `_on_start()` | `_on_stop()` | `_on_reload()` | `_on_unload()` |
|---------|---|---|---|---|
| **QuoteService** | Loads APIs | Logs out APIs | ❌ Unused | ❌ Unused |
| **SSEService** | Logs start | Shuts down EventManager | ❌ Unused | ❌ Unused |
| **SchedService** | Starts APScheduler | Shuts down APScheduler | ❌ Unused | ❌ Unused |

**Finding:** All three use `_on_start`/`_on_stop`, but **no one uses `_on_reload`/`_on_unload`**. This is normal—not all plugins need reload logic.

### When to Use Layer 1
✅ Plugin must initialize resources on startup
✅ Plugin must clean up resources on shutdown
✅ Plugin needs stateful lifecycle management
✅ Behavior is determined at plugin authoring time

### When NOT to Use Layer 1
❌ External code wants to monitor the plugin (→ use Layer 2/3)
❌ Multiple handlers needed for same event (→ use Layer 2/3)
❌ Logic is optional or configurable (→ use Layer 2/3)

---

## Part 3: Layer 2 — Instance Hooks (Observer Pattern)

### What It Is
**External code dynamically registers callbacks** to monitor a *specific plugin instance*.

```python
# Example: Plugin B wants to know when Plugin A is starting
plugin_a = PluginManager.get_plugin('QuoteService')

def on_quote_service_starting():
    print("Quote service is starting, preparing dependent systems...")

plugin_a.add_lifecycle_hook('before_start', on_quote_service_starting)
```

### Characteristics
- **Registered at runtime** (not class definition)
- **Per-instance** — each plugin instance has its own hooks
- **Multiple handlers per event** — can register many callbacks
- **Optional** — observer can decide whether to listen
- **Decoupled** — plugin doesn't know about observers

### API

```python
# Registration
plugin.add_lifecycle_hook(event, callback)

# Supported events
'before_start'  # Before _on_start()
'after_start'   # After _on_start() + state → RUNNING
'before_stop'   # Before _on_stop()
'after_stop'    # After _on_stop() + state → STOPPED
'on_error'      # If exception during start/stop
```

### Real-World Usage in Funlab
**Currently UNUSED** — documented but no production code calls `add_lifecycle_hook()`.

### Comparison with Layer 1 (The Key Difference!)

```
Layer 1 (Template Method):          Layer 2 (Instance Hooks):
================================  ================================

class QuoteService:               # In some other application code:
    def _on_start(self):          quote_svc = get_plugin('quote')
        # Core startup logic
        load_apis()               # External monitoring
        log('started')            quote_svc.add_lifecycle_hook(
                                      'before_start',
                                      lambda: print("Quote svc starting")
                                  )

Plugin DECIDES action            |  External code REACTS to action
Code at plugin definition time   |  Code registered at runtime
Single implementation            |  Multiple implementations
Type-safe                        |  Flexible/dynamic


Purpose: Plugin's own behavior   |  Purpose: External observers
(What the plugin does)           |  (Who cares when it changes)
```

### Practical Example: When Layer 2 Would Be Useful

**Scenario:** Dashboard application that monitors all plugins

```python
class DashboardMonitor:
    """Monitors plugin lifecycle for real-time dashboard updates."""

    def __init__(self, app):
        self.app = app
        self.plugin_states = {}

    def register_plugin_monitors(self):
        """Register instance hooks on all plugins."""
        for plugin_name, plugin in app.plugins.items():
            # Monitor this specific plugin's lifecycle
            plugin.add_lifecycle_hook(
                'before_start',
                lambda p=plugin_name: self._on_plugin_starting(p)
            )
            plugin.add_lifecycle_hook(
                'after_start',
                lambda p=plugin_name: self._on_plugin_started(p)
            )
            plugin.add_lifecycle_hook(
                'before_stop',
                lambda p=plugin_name: self._on_plugin_stopping(p)
            )
            plugin.add_lifecycle_hook(
                'on_error',
                lambda e, p=plugin_name: self._on_plugin_error(p, e)
            )

    def _on_plugin_starting(self, plugin_name):
        self.plugin_states[plugin_name] = 'starting'
        self._broadcast_to_dashboard(plugin_name, 'starting')

    def _on_plugin_started(self, plugin_name):
        self.plugin_states[plugin_name] = 'running'
        self._broadcast_to_dashboard(plugin_name, 'running')

    def _on_plugin_stopping(self, plugin_name):
        self.plugin_states[plugin_name] = 'stopping'
        self._broadcast_to_dashboard(plugin_name, 'stopping')

    def _on_plugin_error(self, plugin_name, error):
        self.plugin_states[plugin_name] = 'error'
        self._alert_on_dashboard(plugin_name, str(error))

    def _broadcast_to_dashboard(self, plugin_name, status):
        # WebSocket broadcast, DB update, etc.
        pass
```

### When to Use Layer 2
✅ External code needs to monitor *specific* plugin
✅ Multiple unrelated systems need to react (decoupling)
✅ Monitoring is optional or pluggable
✅ Behavior is determined at configuration/runtime time

### When NOT to Use Layer 2
❌ Logic is core to the plugin (→ use Layer 1)
❌ Plugin itself must enforce behavior (→ use Layer 1)
❌ Plugin doesn't exist yet when code runs (→ use Layer 3)

---

## Part 4: Layer 3 — Global Hooks (Broadcast Pattern)

### What It Is
**App broadcasts lifecycle events to *all* interested listeners**, regardless of which plugin.

```python
# In plugin start() method:
self._call_global_hook('plugin_before_start')  # Broadcast to app
self._on_start()                               # Plugin's own logic
self._call_global_hook('plugin_after_start')   # Broadcast to app

# Elsewhere in the app (during initialization):
app.hook_manager.register_hook(
    'plugin_before_start',
    callback=audit_logger.log_plugin_event,
    priority=100
)
app.hook_manager.register_hook(
    'plugin_before_start',
    callback=metrics_collector.increment_start_counter,
    priority=200
)
```

### Characteristics
- **App-level centralization** — single `HookManager` instance
- **Broadcast to all** — all registered handlers execute
- **Cross-cutting concerns** — designed for infrastructure features
- **Priority/ordering** — can control handler execution order
- **Runtime registration** — like Layer 2, but app-level not instance-level

### Supported Events

| Event | When | Use For |
|-------|------|---------|
| `plugin_after_init` | After plugin instantiation, during app startup | Plugin registration, discovery |
| `plugin_before_start` | Before `_on_start()` executes | Pre-flight checks, dependency validation |
| `plugin_after_start` | After `_on_start()` succeeds | Metrics, logging, cascade startup |
| `plugin_before_stop` | Before `_on_stop()` executes | Close external connections |
| `plugin_after_stop` | After `_on_stop()` completes | Cleanup, logging, cascade shutdown |
| `plugin_before_reload` | Before `stop()` + `_on_reload()` | Backup state, halt dependent services |
| `plugin_after_reload` | After `start()` completes | Restore state, resume dependent services |
| `plugin_service_init` | During `EnhancedServicePlugin.__init__` | Service-specific setup |

### Real-World Usage in Funlab

Currently implemented but **only one demo handler** (QuoteService):

```python
# In plugin_manager.py, when loading plugins:
if hasattr(self.app, 'hook_manager'):
    self.app.hook_manager.call_hook('plugin_after_init', plugin=self)

# In QuoteService (demo only, gated by HOOK_EXAMPLES config):
app.hook_manager.register_hook(
    'plugin_after_start',
    callback=self._on_plugin_after_start_example,
    priority=100
)
```

### Comparison with Layer 2 (The Key Difference!)

```
Layer 2 (Instance Hooks):        Layer 3 (Global Hooks):
================================  ================================

plugin.add_lifecycle_hook(        app.hook_manager.register_hook(
    'before_start',                   'plugin_before_start',
    callback                          callback
)                                 )

Per-plugin instance              All plugins broadcast here
Tight coupling                   Loose coupling
External code knows about        Infrastructure doesn't know
specific plugin                  about specific plugins


Purpose: Monitor ONE plugin      Purpose: Monitor ALL plugins
(What's Plugin A doing?)         (What's happening in the plugin system?)
```

### Practical Example: Layer 3 Use Cases

**Use Case 1: Audit Logging**
```python
# Infrastructure code that logs ALL plugin changes
def audit_all_plugins(context):
    plugin_name = context['plugin_name']
    plugin = context['plugin']
    timestamp = time.time()

    # Log to audit DB
    AuditLog.create(
        timestamp=timestamp,
        plugin=plugin_name,
        action=context.get('hook_name', 'unknown'),
        status='starting'
    )

app.hook_manager.register_hook(
    'plugin_before_start',
    audit_all_plugins,
    priority=100
)
```

**Use Case 2: System Metrics**
```python
# Collect metrics on all plugins
def track_plugin_timing(context):
    context['start_time'] = time.time()

def record_plugin_timing(context):
    elapsed = time.time() - context.get('start_time', 0)
    Metrics.record(
        'plugin_startup_time',
        value=elapsed,
        tags={'plugin': context['plugin_name']}
    )

app.hook_manager.register_hook('plugin_before_start', track_plugin_timing, 10)
app.hook_manager.register_hook('plugin_after_start', record_plugin_timing, 900)
```

**Use Case 3: Cascade Startup**
```python
# When ServiceA starts, automatically start ServiceB
def cascade_startup(context):
    if context['plugin_name'] == 'ServiceA':
        # Start dependent service
        service_b = app.plugins.get('ServiceB')
        if service_b:
            service_b.start()

app.hook_manager.register_hook('plugin_after_start', cascade_startup, priority=500)
```

**Use Case 4: Health Check System**
```python
# When any plugin starts, add it to health check routine
def register_for_monitoring(context):
    plugin_name = context['plugin_name']
    plugin = context['plugin']

    # Add to background health checker
    HealthMonitor.register_plugin(plugin)

app.hook_manager.register_hook('plugin_after_start', register_for_monitoring)
```

### When to Use Layer 3
✅ Infrastructure code (metrics, logging, auditing)
✅ Need to observe *all* plugins uniformly
✅ Don't care which specific plugin, only that *something* happened
✅ Want loose coupling with plugins
✅ Cross-cutting concerns (health checks, cascade operations)

### When NOT to Use Layer 3
❌ Plugin's own behavior (→ use Layer 1)
❌ Monitoring specific plugin (→ use Layer 2)
❌ Plugin not yet instantiated (use module-level hooks instead)

---

## Part 5: Layer 2 vs Layer 3 — The Critical Distinction

### Are They Redundant?

**NO. They serve fundamentally different concerns:**

```
Layer 2: "I want to monitor Plugin A"
→ Tight coupling, explicit dependency
→ Example: Dashboard UI showing single plugin status

Layer 3: "I want to audit ALL plugin lifecycle events"
→ Loose coupling, no specific plugin knowledge
→ Example: Application-wide audit log
```

### Decision Tree

```
Does your handler care WHICH plugin triggered the event?
│
├─ YES → Use Layer 2 (Instance Hook)
│   - Register hook on that specific plugin
│   - Tight coupling OK because you already depend on the plugin
│   - Example: Plugin A reacts to Plugin B starting
│
└─ NO → Use Layer 3 (Global Hook)
    - Register with app.hook_manager
    - Loose coupling, scales to any plugin
    - Example: Metrics system counts all plugin starts
```

### Table: When to Use Each

| Scenario | Layer 2 | Layer 3 | Why |
|----------|---------|---------|-----|
| "Log when Plugin A starts" | ✅ | ❌ | You específicially care about A |
| "Log when ANY plugin starts" | ❌ | ✅ | You don't care which plugin |
| "Plugin B reacts to A starting" | ✅ | ❌ | B has explicit dependency on A |
| "Metrics system tracks all starts" | ❌ | ✅ | Metrics is app infrastructure |
| "Dashboard shows plugin status" | ✅ | ❌ | Dashboard monitors specific plugins |
| "Health check all plugins" | ❌ | ✅ | Unified system for all plugins |
| "Cascade start: A→B→C" | ✅ | ⚠️ | B/C hook on A; OR use Layer 3 for chain |

---

## Part 6: Module-Time Init (Not Currently Used)

### What It Is
**One-time hook during plugin discovery/registration**, not during start/stop.

### Currently in Code

```python
# In EnhancedViewPlugin.__init__():
if hasattr(self.app, 'hook_manager'):
    self.app.hook_manager.call_hook(
        'plugin_after_init',  # ← Module-time hook
        plugin=self,
        plugin_name=self.name,
    )

# In EnhancedServicePlugin.__init__():
if hasattr(self.app, 'hook_manager'):
    self.app.hook_manager.call_hook(
        'plugin_service_init',  # ← Service-specific init
        plugin=self,
        plugin_name=self.name,
    )
```

### Purpose
**Discovery/registration phase** — when app first loads plugins.

| Hook | Frequency | Use For |
|------|-----------|---------|
| `plugin_after_init` | Once per plugin, during app startup | Register routes, populate menus, initialize dependencies |
| `plugin_service_init` | Once per service, during discovery | Service-specific initialization |

### Comparison with Start/Stop Hooks

```
Module-time (once per app lifetime):
  plugin_after_init() → Register routes, menus, etc.

Runtime (once per plugin instance lifecycle):
  plugin_before_start() → Prepare resources
  _on_start() → Acquire resources
  plugin_after_start() → Announce ready

  [... running ...]

  plugin_before_stop() → Prepare to close
  _on_stop() → Release resources
  plugin_after_stop() → Announce stopped
```

### When to Use Module-Time Hooks
✅ Plugin registration (routes, menus, models)
✅ One-time initialization per app startup
✅ App startup phase concerns

### When NOT to Use Module-Time Hooks
❌ Plugin wants to start/stop multiple times (→ use Layer 1 start/stop)
❌ Repeated per plugin reload (→ use Layer 1 `_on_reload`)
❌ Runtime concerns like metrics (→ use Layer 3 runtime hooks)

---

## Part 7: Real-World Architecture Comparison

### Django Apps System

```python
# Module-time (one per app startup):
class MyAppConfig(AppConfig):
    name = 'myapp'

    def ready(self):  # ← Layer 1: Template method (init)
        from . import signals  # Layer 2/3: Register signal handlers

# Runtime (repeated per operation):
from django.db.models.signals import post_save

@receiver(post_save, sender=MyModel)  # ← Layer 3: Global signal
def on_model_saved(sender, instance, **kwargs):
    # Infrastructure code that runs on every save
    invalidate_cache(instance)
    audit_log(instance)
```

**Mapping to Funlab:**
- Layer 1: `ready()` ↔ `_on_start()`
- Layer 2/3: Signal handlers ↔ `add_lifecycle_hook()` + `app.hook_manager`

### pytest Plugin System

```python
# Module-time (once per test session):
def pytest_configure(config):  # ← Layer 1: Template (class-level equiv)
    """Called once when pytest starts."""

# Runtime (once per test):
def pytest_runtest_setup(item):  # ← Layer 3: Global hook
    """Called before each test runs."""

# Hook registration:
# (implicit via function naming convention in conftest.py)
```

**Mapping to Funlab:**
- Module: `pytest_configure()` ↔ `plugin_after_init`
- Runtime: `pytest_runtest_setup()` ↔ `plugin_before_start` + `_on_start()`
- Multiple handlers: Via conftest discovery ↔ Via `app.hook_manager`

### Flask Extensions

```python
# Module-time (once per extension):
class MyExtension:
    def init_app(self, app):  # ← Layer 1: Template
        """Called once to register extension."""
        app.before_request(self._before_request)  # ← Layer 3: register handler

# Runtime (once per request):
def _before_request(self):  # ← Layer 1: Template (per request)
    """Called before each request."""
```

**Mapping to Funlab:**
- `init_app()` ↔ `plugin_after_init`
- Request hooks ↔ HTTP request handler (not plugin lifecycle)

---

## Part 8: Practical Implementation Guide

### When Adding New Code: Which Layer?

```python
# Q1: Is this the plugin's own behavior?
# If YES → Layer 1 (override _on_start, _on_stop, etc.)
class MyPlugin(EnhancedViewPlugin):
    def _on_start(self):
        # Plugin's core startup
        self._initialize_resources()

# Q2: Does another component depend on THIS specific plugin?
# If YES → Layer 2 (register instance hook on that plugin)
my_plugin = app.plugins['MyPlugin']
my_plugin.add_lifecycle_hook('after_start', on_my_plugin_ready)

# Q3: Is this app infrastructure that cares about ALL plugins?
# If YES → Layer 3 (register global hook with app)
app.hook_manager.register_hook(
    'plugin_after_start',
    callback=metrics.record_plugin_started,
    priority=100
)
```

### Current Usage vs Best Practice

| Layer | Best Practice | Funlab Today | Fix |
|-------|---|---|---|
| **Layer 1** | ✅ Use for core plugin logic | ✅ 3 services implement | **Nothing to do** |
| **Layer 2** | ✅ Use for per-plugin monitoring | ❌ Defined but unused | **Enable when needed** |
| **Layer 3** | ✅ Use for app infrastructure | ✅ Defined, 1 demo handler | **Enable for metrics/audit** |

---

## Part 9: Migration Path for Layer 2

### Current State
Layer 2 is fully implemented but has **no production usage**.

### Why Not Yet Used
1. **No requirement yet** — no code needed per-plugin monitoring
2. **Layer 3 sufficient** — app-level hooks handle current needs
3. **Waiting for use case** — dashboard, dependency tracking, etc.

### How to Enable (When Needed)

**When:** You need a component to monitor specific plugins

**Example: Plugin Dependency Manager**

```python
class PluginDependencyManager:
    """Ensures plugins start in dependency order at runtime."""

    def __init__(self, app):
        self.app = app
        self.dependencies = {
            'ServiceB': ['ServiceA'],  # B depends on A
            'ServiceC': ['ServiceA', 'ServiceB'],
        }

    def setup_hooks(self):
        """Register instance hooks on dependent plugins."""
        for dependent, dependencies in self.dependencies.items():
            plugin = self.app.plugins.get(dependent)
            if plugin:
                # Monitor this specific plugin
                plugin.add_lifecycle_hook(
                    'before_start',
                    self._ensure_dependencies_started(dependent)
                )

    def _ensure_dependencies_started(self, plugin_name):
        def check_dependencies():
            for dep in self.dependencies[plugin_name]:
                dep_plugin = self.app.plugins.get(dep)
                if not dep_plugin or dep_plugin.state != PluginLifecycleState.RUNNING:
                    raise RuntimeError(
                        f"{plugin_name} cannot start: {dep} not running"
                    )
        return check_dependencies

# Usage:
dep_mgr = PluginDependencyManager(app)
dep_mgr.setup_hooks()
```

---

## Summary: Three-Layer Checklist

| Layer | Best For | Usage | Status |
|-------|----------|-------|--------|
| **1: Template** | Plugin's own logic | Core behavior override | ✅ Active |
| **2: Instance** | Monitor specific plugin | Dynamic external observers | 🟡 Ready, awaiting use |
| **3: Global** | App infrastructure | Cross-cutting concerns | ✅ Active (init only) |

**Key Insight:** Layers 1 & 2 are **not redundant** — Layer 1 is for the plugin's core, Layer 2 is for external monitoring of that plugin.

---

## References

- Django: [Checking If Application Registry Is Ready](https://docs.djangoproject.com/en/stable/ref/apps/#django.apps.AppConfig.ready)
- Django Signals: [The Dispatch System](https://docs.djangoproject.com/en/stable/topics/signals/)
- pytest: [Hook Plugins](https://docs.pytest.org/en/latest/how-to/plugins.html)
- Flask: [Extension Pattern](https://flask.palletsprojects.com/en/latest/patterns/app_factories/)
