# Plugin Lifecycle Documentation Index

**Complete guide to Funlab's three-layer plugin lifecycle architecture.**

---

## Quick Navigation

### 🔴 Start Here: Your Questions Answered Directly
→ **[PLUGIN_LIFECYCLE_FAQ.md](PLUGIN_LIFECYCLE_FAQ.md)**
- Are Layer 1 and Layer 2 the same purpose?
- Is Layer 3 only for initialization?
- Are these architecture choices best practice?

### � **NEW:** Real-World Applications: WHERE are hooks used?
→ **[../PLUGIN_LIFECYCLE_PRACTICAL_APPLICATIONS_QUICKSTART.md](../PLUGIN_LIFECYCLE_PRACTICAL_APPLICATIONS_QUICKSTART.md)**
- 🌐 Request Handlers in appbase.py (API logging, security audit, monitoring)
- 💾 Database Models in model_hook.py (versioning, indexing, validation, cache sync)
- 🎨 UI Templates in base.html (CSS/JS injection, analytics, notifications, SEO)
- 📋 How to implement: 4-step guide + priority recommendations

### �🟠 Visual Overview: See the Architecture
→ **[PLUGIN_LIFECYCLE_VISUAL.md](PLUGIN_LIFECYCLE_VISUAL.md)**
- Execution flow diagrams
- Decision matrices
- State machine visualization
- Quick reference cards

### 🟡 Deep Dive: Understand Each Layer
→ **[PLUGIN_LIFECYCLE_ARCHITECTURE.md](PLUGIN_LIFECYCLE_ARCHITECTURE.md)**
- Industry best practices (Django, pytest, Flask)
- When to use each layer
- Template Method vs Observer patterns
- Module-time vs runtime hooks

### 🟢 Practical Code: Real Examples
→ **[PLUGIN_LIFECYCLE_EXAMPLES.md](PLUGIN_LIFECYCLE_EXAMPLES.md)**
- Layer 1 examples (service plugins, database setup)
- Layer 2 examples (dependency managers, real-time dashboards)
- Layer 3 examples (audit logging, metrics, cascade startup)
- Testing strategies

### 🔵 Real-World Applications: Integration Patterns
→ **[PLUGIN_LIFECYCLE_PRACTICAL_APPLICATIONS.md](PLUGIN_LIFECYCLE_PRACTICAL_APPLICATIONS.md)**
- **Request Handler Integration** - HTTP lifecycle hooks for logging, monitoring, security audit
- **Database Model Operations** - Model hooks for versioning, search indexing, business rule validation
- **Template/UI Rendering** - View hooks for dynamic CSS/JS injection, analytics, SEO optimization
- **Complete end-to-end scenario** - E-commerce order processing across all layers

---

## The Three Layers at a Glance

```
LAYER 1: TEMPLATE METHOD
├─ Mechanism: Subclass override
├─ Scope: Plugin-specific behavior
├─ When: Plugin's core logic
├─ Status: ✓ In use (3 services)
└─ Methods: _on_start(), _on_stop(), _on_reload(), _on_unload()

LAYER 2: INSTANCE HOOKS
├─ Mechanism: Dynamic callback registration
├─ Scope: Monitor specific plugin
├─ When: External code tracks ONE plugin
├─ Status: ⚠ Defined but unused
└─ API: plugin.add_lifecycle_hook(event, callback)

LAYER 3: GLOBAL HOOKS
├─ Mechanism: App-level event broadcasting
├─ Scope: App infrastructure
├─ When: Audit, metrics, health checks, cascades
├─ Status: ✓ In use (init hooks)
├─ Hook Types:
│  ├─ module-time: plugin_after_init (once per app)
│  └─ runtime: plugin_{before,after}_{start,stop,reload}
└─ API: app.hook_manager.register_hook(hook_name, callback)
```

---

## Document Overview

### [PLUGIN_LIFECYCLE_FAQ.md](PLUGIN_LIFECYCLE_FAQ.md)
**Best For:** Answering specific questions about design choices

**Covers:**
- Question 1: Layer 1 vs Layer 2 — are they the same purpose?
- Question 2: Is Layer 3 only for one-time initialization?
- Question 3: Is Layer 3 class-level or app-level?
- Industry best practice verdict

**Key Insight:** The three layers are **complementary, not redundant**

---

### [PLUGIN_LIFECYCLE_VISUAL.md](PLUGIN_LIFECYCLE_VISUAL.md)
**Best For:** Understanding the big picture visually

**Covers:**
- Flow diagrams (what happens during start/stop/reload)
- State machine visualization
- Decision matrix (when to use each layer)
- Layer comparison table
- Real-world scenario mappings
- Quick reference card

**Key Insight:** Each layer handles a different concern

---

### [PLUGIN_LIFECYCLE_ARCHITECTURE.md](PLUGIN_LIFECYCLE_ARCHITECTURE.md)
**Best For:** Comprehensive understanding + industry context

**Covers:**
- Part 1: Three-layer overview
- Part 2: Layer 1 (Template Method)
- Part 3: Layer 2 (Instance Hooks) — **currently unused**
- Part 4: Layer 3 (Global Hooks)
- Part 5: Layer 2 vs Layer 3 distinction
- Part 6: Module-time initialization hooks
- Part 7: Comparison with Django, pytest, Flask
- Part 8: Implementation guidelines
- Part 9: Migration path for Layer 2

**Key Insight:** Django, pytest, Flask all use this same layered pattern

---

### [PLUGIN_LIFECYCLE_EXAMPLES.md](PLUGIN_LIFECYCLE_EXAMPLES.md)
**Best For:** Concrete code implementation

**Covers:**
- **Layer 1 Examples:**
  - Simple service plugin
  - Database setup plugin
  - OAuth security plugin with reload logic

- **Layer 2 Examples:**
  - Plugin dependency manager
  - Real-time dashboard monitor
  - Plugin-to-plugin communication

- **Layer 3 Examples:**
  - Audit logging system
  - Metrics collection (Prometheus)
  - Cascade startup orchestrator
  - Health check integration

- **Testing strategies** for each layer
- **Migration guide** (Layer 3 → Layer 2 when appropriate)

**Key Insight:** Each pattern solves a specific real-world problem

---

### [PLUGIN_LIFECYCLE_PRACTICAL_APPLICATIONS.md](PLUGIN_LIFECYCLE_PRACTICAL_APPLICATIONS.md)
**Best For:** Understanding real-world integration patterns in Funlab

**Covers:**
- **Scenario 1: HTTP Request Handler Integration**
  - API request logging
  - Security audit tracking
  - Performance monitoring
  - Request context initialization

- **Scenario 2: Database Model Operations**
  - Automatic versioning/change tracking
  - Full-text search index updates
  - Business rule validation
  - Cache synchronization

- **Scenario 3: Template/UI Rendering**
  - Dynamic CSS/JavaScript injection
  - Page content enhancement (analytics dashboard)
  - User notification system
  - SEO optimization (meta tags, structured data)

- **Scenario 4: Complete E-commerce Order Management**
  - End-to-end integration across all layers
  - Request validation → Model validation → UI display lifecycle

**Key Insight:** `call_hook()` is embedded throughout Funlab's architecture (appbase.py, model_hook.py, base.html templates)

---

## Which Document Should I Read?

### "I want quick answers to my questions"
→ Read **[PLUGIN_LIFECYCLE_FAQ.md](PLUGIN_LIFECYCLE_FAQ.md)** (10 min read)

### "I want to understand the architecture visually"
→ Read **[PLUGIN_LIFECYCLE_VISUAL.md](PLUGIN_LIFECYCLE_VISUAL.md)** (15 min read)

### "I want to understand why this design is correct"
→ Read **[PLUGIN_LIFECYCLE_ARCHITECTURE.md](PLUGIN_LIFECYCLE_ARCHITECTURE.md)** (25 min read)

### "I want to see how to implement each layer"
→ Read **[PLUGIN_LIFECYCLE_EXAMPLES.md](PLUGIN_LIFECYCLE_EXAMPLES.md)** (30 min read)

### "I want to see WHERE hooks are used in Funlab"
→ Read **[PLUGIN_LIFECYCLE_PRACTICAL_APPLICATIONS.md](PLUGIN_LIFECYCLE_PRACTICAL_APPLICATIONS.md)** (40 min read)
- HTTP request handler hooks (appbase.py)
- Database model hooks (model_hook.py)
- Template rendering hooks (base.html)
- Real-world integration patterns

### "I want the complete picture"
→ Read all documents in order: FAQ → Visual → Architecture → Examples → Practical Applications (150 min)

---

## Key Concepts Explained

### Three Layers Are NOT Redundant

```
Layer 1: "What does this plugin do?"
Layer 2: "Who cares when this plugin does something?"
Layer 3: "What's the app watching overall?"

All three fulfill different purposes.
```

### Layer 2 Is Ready But Unused

```
Current Status: Fully implemented but no production code uses it
When to Enable: When you need per-plugin monitoring
Example: Dashboard tracking individual plugin states
Cost to Adopt: Low (already there, just register hooks)
```

### Layer 3 Has Two Flavors

```
plugin_after_init: One-time, during app startup
                   (plugin discovery, registration)

plugin_{before,after}_{start,stop,reload}: Repeated throughout app life
                                           (metrics, audit, cascades)
```

---

## Common Scenarios & Solutions

### Scenario 1: "Service needs to load data on startup"
**Use Layer 1**
```python
class DataService(EnhancedServicePlugin):
    def _on_start(self):
        self.load_data()
```
→ See [Examples: Service Plugin](PLUGIN_LIFECYCLE_EXAMPLES.md#example-1-simple-service-plugin)

### Scenario 2: "One plugin depends on another plugin"
**Use Layer 2**
```python
option_service.add_lifecycle_hook(
    'after_start',
    quote_service.notify_option_ready
)
```
→ See [Examples: Plugin Dependency Manager](PLUGIN_LIFECYCLE_EXAMPLES.md#example-1-plugin-dependency-manager)

### Scenario 3: "Log all plugin events for audit purposes"
**Use Layer 3**
```python
app.hook_manager.register_hook(
    'plugin_after_start',
    audit_logger.log_start
)
```
→ See [Examples: Audit Logging](PLUGIN_LIFECYCLE_EXAMPLES.md#example-1-audit-logging-system)

### Scenario 4: "Collect metrics on all plugin starts"
**Use Layer 3**
```python
app.hook_manager.register_hook(
    'plugin_after_start',
    metrics.record_startup
)
```
→ See [Examples: Metrics Collection](PLUGIN_LIFECYCLE_EXAMPLES.md#example-2-metrics-collection)

### Scenario 5: "Automatically start dependent plugins in order"
**Use Layer 3**
```python
app.hook_manager.register_hook(
    'plugin_after_start',
    orchestrator.cascade_starts
)
```
→ See [Examples: Cascade Startup](PLUGIN_LIFECYCLE_EXAMPLES.md#example-3-cascadedependent-startup)

---

## Current Codebase Status

### What's Implemented
- ✅ **Layer 1**: Fully implemented, 3 services use it
- ✅ **Layer 2**: Fully implemented, waiting for use cases
- ✅ **Layer 3**: Fully implemented, initialization hooks active

### What's Used
- ✓ Layer 1: `_on_start()`, `_on_stop()` in QuoteService, SSEService, SchedService
- ✓ Layer 3: `plugin_after_init` hooks during plugin discovery
- ⚠ Layer 2: Defined but no production code uses it yet
- 🔴 Layer 3: Runtime hooks (`plugin_before_start`, etc.) ready but not actively used

### What's Ready for Adoption
- **Layer 2**: When you need plugin dependency management or per-plugin monitoring
- **Layer 3**: Runtime hooks for metrics, audit, cascade startup logic

---

## Architecture Alignment with Best Practices

| **Framework** | **Pattern** | **Funlab Equivalent** |
|---|---|---|
| Django | `AppConfig.ready()` + Signals | Layer 1 + Layer 3 |
| pytest | Hook functions | Layer 3 broadcast |
| Flask | Extension + init_app | Layer 1 + Layer 2/3 |
| celery | Task registration + callbacks | Layer 1 + Layer 2 |

**Verdict:** ✅ **Funlab's approach is aligned with industry best practices**

---

## Performance & Threading Notes

All layers respect these guarantees:

1. **Thread-safe** — Lifecycle transitions use RLock
2. **Non-blocking** — Hooks should complete quickly
3. **Error-handling** — Exceptions logged, don't block other hooks
4. **No deadlock** — Hooks can't re-trigger same lifecycle
5. **Ordered execution** — Layer 3 respects priority parameter

See [Architecture: Lifecycle Safety](PLUGIN_LIFECYCLE_ARCHITECTURE.md#part-2-layer-1--template-method-pattern) for details.

---

## Glossary

| Term | Definition |
|------|-----------|
| **Hook** | Callback function registered to execute at a lifecycle point |
| **Layer 1** | Template Method pattern (subclass override) |
| **Layer 2** | Instance Hooks pattern (per-plugin observers) |
| **Layer 3** | Global Hooks pattern (app-level broadcast) |
| **Lifecycle State** | Plugin's current state (INITIALIZING, READY, RUNNING, STOPPED, ERROR) |
| **Template Method** | Protected method subclass overrides (`_on_start`, etc.) |
| **Observer Pattern** | External code registers to be notified of events |
| **Broadcast Pattern** | One event triggers all registered listeners |
| **Module-time** | Hooks that execute once during app startup |
| **Runtime** | Hooks that execute repeatedly during app lifetime |

---

## FAQ (Quick Answers)

**Q: Should I use Layer 1, 2, or 3?**
A: Use Layer 1 for plugin's own behavior, Layer 2 for monitoring specific plugins, Layer 3 for app infrastructure.

**Q: Are Layer 1 and 2 redundant?**
A: No. Layer 1 defines what the plugin does; Layer 2 lets external code react to it.

**Q: Is Layer 3 only for initialization?**
A: No. `plugin_after_init` is one-time, but `plugin_before_start` and others repeat.

**Q: Which layer should I implement?**
A: Start with Layer 1 if you're building a plugin. Layer 2 and 3 are for infrastructure code.

**Q: Can I use multiple layers?**
A: Yes! They're complementary. Use as many as needed.

**Q: Why doesn't anyone use Layer 2?**
A: It was designed for future use. It's there when you need per-plugin monitoring.

---

## Further Reading

- [Enhanced Plugin Class Documentation](../../funlab/core/enhanced_plugin.py) — Full API reference
- [Hook Manager Implementation](../../funlab/core/hook.py) — How hooks are invoked
- [Django App Registry](https://docs.djangoproject.com/en/stable/ref/apps/) — Similar pattern
- [pytest Hook System](https://docs.pytest.org/en/latest/how-to/plugins.html) — Reference implementation

---

## Questions or Contributions?

If you have:
- 🔶 Questions about the architecture
- 🔵 Ideas for implementing Layer 2
- 🟢 Examples of real use cases
- 🟡 Suggestions for improvements

Please document them and share!

---

**Document generated:** 2025-02-24
**Related code:** [enhanced_plugin.py](../../funlab/core/enhanced_plugin.py)
**Architecture review:** Three-layer hook system analysis
