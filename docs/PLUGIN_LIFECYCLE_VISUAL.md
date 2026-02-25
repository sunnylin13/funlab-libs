# Plugin Lifecycle: Visual Architecture & Decision Guide

## The Three-Layer Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────┐
│                        PLUGIN LIFECYCLE                         │
│                     Three-Layer Architecture                     │
└─────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────┐
                    │   Plugin Instantiation   │
                    │   (app startup)          │
                    └────────────┬─────────────┘
                                 │
                ╔════════════════╩════════════════╗
                ║ Layer 3: GLOBAL HOOK            ║
                ║ plugin_after_init               ║
                ║ (broadcast to all listeners)    ║
                ╚════════════════╩════════════════╝
                                 │
                    ┌────────────┼──────────────┐
                    │            │              │
            ┌───────┴──┐  ┌──────┴──┐  ┌──────┴──┐
            │ Plugin 1 │  │ Plugin 2 │  │ Plugin N │
            └────┬─────┘  └────┬─────┘  └────┬─────┘
                 │             │             │
                 └──Method injection (Layer 1)
                    Can override:
                    · _on_start()
                    · _on_stop()
                    · _on_reload()
                    · _on_unload()
```

---

## Execution Flow: plugin.start()

```
                    app.plugins['Quote'].start()
                              │
                ╔═════════════╩═════════════╗
                ║ Layer 3: GLOBAL HOOK      ║
                ║ plugin_before_start       ║
                ║ (broadcast to all)        ║
                ╚═════════════╩═════════════╝
                              │
                ┌─────────────┴─────────────┐
                │ Layer 2: INSTANCE HOOKS   │
                │ before_start (on Quote)   │  ← External observers
                │ - listener1()             │     monitoring this
                │ - listener2()             │     specific plugin
                │ - listener3()             │
                └─────────────┬─────────────┘
                              │
                ╔═════════════╩═════════════╗
                ║ Layer 1: TEMPLATE METHOD  ║
                ║ Quote._on_start()         ║  ← Quote's own logic
                ║ (plugin's core logic)     ║
                ╚═════════════╩═════════════╝
                              │
                ┌─────────────┌─────────────┐
                │  STATE UPDATE: RUNNING    │
                └─────────────┬─────────────┘
                              │
                ┌─────────────┴─────────────┐
                │ Layer 2: INSTANCE HOOKS   │
                │ after_start (on Quote)    │
                │ - listener1()             │
                │ - listener2()             │
                │ - listener3()             │
                └─────────────┬─────────────┘
                              │
                ╔═════════════╩═════════════╗
                ║  Layer 3: GLOBAL HOOK     ║
                ║ plugin_after_start        ║
                ║ (broadcast to all)        ║
                ╚═════════════╩═════════════╝
                              │
                         DONE ✓
```

---

## Layer Comparison: Function & Purpose

```
╔════════════════════════════════════════════════════════════════╗
║ LAYER 1: TEMPLATE METHOD                                       ║
║ Pattern: Subclass Inheritance                                  ║
╠════════════════════════════════════════════════════════════════╣
║                                                                ║
║  class QuoteService(EnhancedServicePlugin):                   ║
║      def _on_start(self):                                     ║
║          # Plugin's own startup logic                         ║
║          self.load_apis()                                     ║
║          self.connect_to_feed()                               ║
║                                                                ║
║  Purpose: Define what THIS plugin does                        ║
║  Coupling: Internal (plugin's own behavior)                   ║
║  Frequency: Once per plugin lifecycle transition              ║
║  Binding time: Class definition                               ║
║  Cardinality: One per plugin per method                       ║
║  Current usage: ✓ 3 services use _on_start/_on_stop          ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝


╔════════════════════════════════════════════════════════════════╗
║ LAYER 2: INSTANCE HOOKS (Observer Pattern)                     ║
║ Pattern: Dynamic Callback Registration                         ║
╠════════════════════════════════════════════════════════════════╣
║                                                                ║
║  # External code monitors THIS specific plugin:               ║
║  quote = app.plugins['Quote']                                 ║
║  quote.add_lifecycle_hook('before_start',                     ║
║      lambda: print("Quote is starting!")                      ║
║  )                                                             ║
║                                                                ║
║  Purpose: Let external code monitor THIS plugin               ║
║  Coupling: External (observers depend on plugin)              ║
║  Frequency: Once per hook registration                        ║
║  Binding time: Runtime (during app initialization)            ║
║  Cardinality: Multiple per plugin per event                   ║
║  Current usage: ⚠ Defined, but UNUSED in production           ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝


╔════════════════════════════════════════════════════════════════╗
║ LAYER 3: GLOBAL HOOKS (Broadcast Pattern)                      ║
║ Pattern: App-Level Event Broadcasting                          ║
╠════════════════════════════════════════════════════════════════╣
║                                                                ║
║  # App infrastructure monitors ALL plugins:                   ║
║  app.hook_manager.register_hook(                              ║
║      'plugin_after_start',                                    ║
║      callback=metrics.increment_counter,                      ║
║      priority=100                                             ║
║  )                                                             ║
║  # Now EVERY plugin start is counted                          ║
║                                                                ║
║  Purpose: App infrastructure watches ALL plugins              ║
║  Coupling: Loose (infrastructure doesn't know about           ║
║            specific plugins)                                  ║
║  Frequency: Once per hook registration                        ║
║  Binding time: Runtime (during app initialization)            ║
║  Cardinality: Multiple per event (all listeners execute)      ║
║  Current usage: ✓ Used for init hooks                         ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
```

---

## Decision Matrix: When to Use Each Layer

```
                    LAYER 1          LAYER 2            LAYER 3
                 TEMPLATE         INSTANCE HOOKS      GLOBAL HOOKS
              METHOD

WHO WRITES    Plugin author     External code      Infrastructure code
              (subclass)        (outside plugin)    (App-level)

WHO CARES     The plugin       Specific plugin    All plugins equally
ABOUT         itself           observers

HOW MANY      One              Multiple           Multiple
HANDLERS

WHEN          Always           Dynamic            Dynamic
INVOKED       defined          registration       registration

SCOPE         Plugin-local     Plugin instance    App-wide broadcast

COUPLING      Internal         Medium             Loose

USE WHEN:
  Plugin must init resources         ✓
  External code monitors 1 SPECIFIC
    plugin                                    ✓
  App infrastructure tracks ALL
    plugins uniformly                                    ✓
  Logic is central to plugin         ✓
  Logic is optional/pluggable                  ✓         ✓
  Multiple unrelated systems need
    to react                                  ✓         ✓
  Want type safety & IDE support     ✓
  Want flexibility & runtime config          ✓         ✓
  Plugin doesn't exist yet (boot)                       ✓
```

---

## Real-World Examples Mapped to Layers

```
Scenario 1: Quote Service Loads Market Data
┌────────────────────────────────────────────────────────────┐
│  app.plugins['Quote'].start()                              │
├────────────────────────────────────────────────────────────┤
│  LAYER 1: QuoteService._on_start()                         │
│  → Connects to data feeds                                  │
│  → Initializes API credentials                             │
│  → Starts background data consumer                         │
└────────────────────────────────────────────────────────────┘


Scenario 2: Option View Depends on Quote Service Data
┌────────────────────────────────────────────────────────────┐
│  quote = app.plugins['Quote']                              │
│  quote.add_lifecycle_hook('after_start', )                 │
│      lambda: option_view.subscribe_to_quotes()             │
├────────────────────────────────────────────────────────────┤
│  LAYER 2: Instance hook on Quote service                   │
│  → Notifies Option when Quote is ready                     │
│  → Option knows to subscribe to data feed                  │
│  → Tight coupling: Option monitors Quote                   │
└────────────────────────────────────────────────────────────┘


Scenario 3: Metrics System Counts All Plugin Starts
┌────────────────────────────────────────────────────────────┐
│  app.hook_manager.register_hook(                           │
│      'plugin_after_start',                                 │
│      callback=metrics.increment('plugins_started')         │
│  )                                                         │
├────────────────────────────────────────────────────────────┤
│  LAYER 3: Global hook                                      │
│  → Fires for every plugin start (Quote, Option, etc.)      │
│  → Metrics doesn't care which plugin                       │
│  → Loose coupling: Metrics is app infrastructure           │
└────────────────────────────────────────────────────────────┘
```

---

## Lifecycle State Machine

```
                    PLUGIN INSTANTIATION
                           │
                ┌──────────┴──────────┐
                │                     │
            INITIALIZING          (Prepare internal state)
                │
        ┌───────┴────────┐
        │                │
      READY             (Can be started)
        │
    [user calls start()]
        │
        ├─→ STARTING     (Transitioning to RUNNING)
        │       │
        │   (_on_start() executing)
        │       │
        │   → RUNNING    (Ready to handle requests)
        │       │
        │   [user calls stop()]
        │       │
        ├─→ STOPPING     (Transitioning to STOPPED)
        │       │
        │   (_on_stop() executing)
        │       │
        │   → STOPPED    (Stopped but can restart)
        │
        └─→ ERROR        (Exception during start/stop)
                │
            (Can still call start/stop again)

        [user calls reload()]
              │
            STOPPED → STARTING → RUNNING
              (stop())   (_on_reload() between)
```

---

## Hook Points in the Lifecycle

```
                        start()
                          │
            ┌─────────────────────────────┐
            │                             │
        Layer 3              Layer 2          Layer 1
      GLOBAL HOOKS    INSTANCE HOOKS    TEMPLATE METHOD
            │               │                 │
       ┌────┴────┐      ┌────┴────┐     ┌────┴────┐
       │ plugin_ │      │ before_ │     │ _on_    │
       │ before_│      │ start() │     │ start() │
       │ start  │      │ hooks   │     │         │
       └────┬────┘      └────┬────┘     └────┬────┘
            │               │               │
            └───────────────┼───────────────┘
                            │
                     STATE: RUNNING
                            │
            ┌───────────────┼───────────────┐
            │               │               │
       ┌────┴────┐      ┌────┴────┐     (no template)
       │ plugin_ │      │ after_  │
       │ after_  │      │ start() │
       │ start   │      │ hooks   │
       └────┬────┘      └────┬────┘
            │               │
            └───────────────┴───────────────┘
                            │
                         DONE ✓

Similar structure for stop(), reload(), unload()
```

---

## Common Patterns

### Pattern 1: Service Initialization (Layer 1)

```
Plugin Must Load Resources
           │
      _on_start()
           │
    ├─ Load config
    ├─ Create connections
    ├─ Populate caches
    └─ Start background threads
           │
         READY
```

### Pattern 2: Plugin Dependencies (Layer 2)

```
Plugin B Depends on Plugin A
           │
    Add instance hook on A:
    A.add_lifecycle_hook('after_start', B.init_from_A)
           │
    When A starts:
    ├─ A._on_start()
    ├─ B.init_from_A() triggered    ← Layer 2
    └─ B can now use A's APIs
```

### Pattern 3: App Infrastructure (Layer 3)

```
Metrics System Tracks All Plugins
           │
    register_hook('plugin_after_start', metrics_increment)
           │
    For each plugin start:
    ├─ Plugin1 starts → metrics += 1
    ├─ Plugin2 starts → metrics += 1
    └─ Plugin3 starts → metrics += 1
           │
         Metrics dashboard shows count
```

---

## Best Practice Checklist

- [ ] Layer 1 methods are reserved for plugin's own core behavior
- [ ] Layer 2 is used only when external code monitors specific plugin
- [ ] Layer 3 is used only for app-level infrastructure
- [ ] No cross-plugin dependencies hardcoded in _on_start()
- [ ] Error handling in all three layers is consistent
- [ ] Hooks don't block (long operations use background tasks)
- [ ] Layer 3 hooks have reasonable priority numbers (10-900 range)
- [ ] Documentation clarifies which layer each hook belongs to
- [ ] Tests exist for each layer independently
- [ ] Logging statements identify which layer is executing

---

## Quick Reference Card

```
┌─────────────────────────────────────────────────────────────────┐
│                    QUICK REFERENCE                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│ LAYER 1: "What does THIS plugin do?"                            │
│ ├─ Subclass override method                                     │
│ ├─ Type-safe, one per plugin per event                          │
│ ├─ Bind at class definition                                     │
│ └─ Used by: 3 services (_on_start, _on_stop)                   │
│                                                                  │
│ LAYER 2: "Who cares about THIS specific plugin?"                │
│ ├─ Instance hook, dynamic registration                          │
│ ├─ Flexible, multiple handlers per event                        │
│ ├─ Bind at runtime                                              │
│ └─ Used by: NONE (currently) — ready for adoption               │
│                                                                  │
│ LAYER 3: "What's happening in the plugin system?"               │
│ ├─ Global hook, broadcast to all listeners                      │
│ ├─ Loose coupling, infrastructure concerns                      │
│ ├─ Bind at runtime                                              │
│ └─ Used by: init hooks, ready for metrics/audit/cascade         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Recommended Reading Order

1. **Start here**: This document (visual overview)
2. **Deep dive**: [PLUGIN_LIFECYCLE_ARCHITECTURE.md](PLUGIN_LIFECYCLE_ARCHITECTURE.md)
   - Industry best practices
   - When each layer is appropriate
   - Module-time vs runtime hooks
3. **See examples**: [PLUGIN_LIFECYCLE_EXAMPLES.md](PLUGIN_LIFECYCLE_EXAMPLES.md)
   - Real code examples for each layer
   - How to implement common patterns
   - Testing strategies
4. **Reference**: Enhanced Plugin class docstring
   - Full method signatures
   - Error handling
   - Thread safety
