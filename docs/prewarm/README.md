# Prewarm Framework

> **Branch**: `feature/prewarm-framework`
> **Module**: `funlab.core.prewarm`
> **Status**: Phase 0–1 完成；見 [EXECUTION_PLAN.md](EXECUTION_PLAN.md)
> **Import 策略**: 見 [IMPORT_BEST_PRACTICES.md](IMPORT_BEST_PRACTICES.md)

---

## 1. 問題背景

Funlab / Finfun 系統在首次 HTTP 請求時會觸發多項一次性初始化：

| 資源 | 觸發位置 | 首次耗時（實測）|
|---|---|---|
| TWSE exchange_calendars 註冊 | `finfun.utils.fin_cale._ensure_calendar_registered()` | 50–83 s |
| QuoteService / Broker SDK import | `finfun-broker-sino` / `yuanta` 模組層級 | 20–62 s |
| SQLAlchemy engine + connection pool | `DbMgr.get_db_engine()` 首次呼叫 | 1–3 s |
| Form choices（ORM 首次 SELECT）| `load_all_managers_email()` | ~5–65 s（含 ORM 初始化）|
| pandas / numpy / scipy / ffn import | `finfun-quantanlys`, `finfun-fundmgr` | 2–8 s |

**目標**：把上述成本移出請求路徑，使用標準 Python import 模式 + prewarm 背景預熱，
消除使用者端「首次請求長時間停頓」問題。

---

## 2. 核心設計原則

### 2.0 Import 策略（v2 — 2026-03-13）

> **不使用自訂 wrapper**。回歸標準 Python import 語法。

| 模組類型 | 策略 | 範例 |
|---------|------|------|
| 輕量 (< 0.5s) | **Top-level import** | `from flask import request` |
| 重型 (> 3s) 且在請求路徑 | **Function-level import + prewarm** | `def route(): import pandas as pd` |
| 重型但僅任務/腳本 | **Function-level import** | spider 中 `import scrapy` |
| 有副作用初始化 | **具名 helper** | `_get_twse_calendar()` |

**反模式**（已從 codebase 移除）：
- `_lazy()` + `@functools.cache` 工廠
- `_get_pd()`, `_get_np()` 等薄包裝函式

**理由**：Python `sys.modules` 本身就是 import 快取，`@cache` 包裝重複且犧牲 IDE 支援。
詳見 [IMPORT_BEST_PRACTICES.md](IMPORT_BEST_PRACTICES.md)。

### 2.1 所有權原則

> **Plugin（消費者）負責登記**自己需要的預熱任務。
> **Library（提供者）保持被動**，不在 import 時產生副作用。

| 角色 | 同一資源被多個 plugin 需要時 |
|---|---|
| library | 僅提供呼叫端（e.g. `_ensure_calendar_registered()`）|
| 每個 plugin | 各自登記，加 `skip_if_exists=True`；第一個注冊者生效。|

### 2.2 公開 API

| 函式 / 屬性 | 職責 |
|---|---|
| `register(name, func, *, blocking, delay, skip_if_exists, replace)` | 登記一個預熱任務 |
| `unregister(name)` | 取消登記（主要供測試使用）|
| `run(app=None)` | 執行所有已登記的任務；只能執行一次 |
| `status() -> dict` | 回傳每個任務的執行狀態 |
| `reset()` | 清除所有登記 + 重置執行旗標（**僅供測試**）|
| `register_prewarm(name, func, **kwargs)` | 便捷別名（backward compat）|
| `deferred_import(name, *, blocking, delay)` | 裝飾器形式的 `register()` |

### 2.3 關鍵參數

| 參數 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `blocking` | `bool` | `False` | `True` = 同步執行；`False` = daemon Thread |
| `delay` | `float` | `0.0` | Thread 啟動後等待 N 秒再執行 |
| `skip_if_exists` | `bool` | `False` | 同名已登記則靜默跳過 |
| `replace` | `bool` | `False` | 強制覆蓋（僅測試/hot-reload）|

### 2.4 執行流程

```
App Bootstrap (_FlaskBase.__init__)
  1. register_routes()
  2. register_plugins()   → each plugin calls register_prewarm_tasks()
  3. register_menu()
  4. dbmgr.create_registry_tables()
  5. _run_prewarm()       → prewarm.run(app=self)
       - blocking=True tasks → 同步完成
       - blocking=False tasks → daemon Thread
```

---

## 3. Plugin 端使用方式

### 3.1 在 `register_prewarm_tasks()` 中登記

```python
class MyPlugin(Plugin):

    def register_prewarm_tasks(self):
        import funlab.core.prewarm as pw

        def _warmup():
            import pandas    # noqa: F401
            import numpy     # noqa: F401

        pw.register(
            "myplugin.scientific_stack",
            _warmup,
            blocking=False,
            delay=5.0,
            skip_if_exists=True,
        )
```

然後在路由中使用標準 function-level import：
```python
def my_route():
    import pandas as pd   # prewarm 已完成 → sys.modules hit → ~0 ms
    # ...
```

### 3.2 裝飾器形式

```python
from funlab.core.prewarm import deferred_import

@deferred_import("finfun_core.twse_calendar")
def _warmup_twse_calendar():
    from finfun.utils.fin_cale import _ensure_calendar_registered
    _ensure_calendar_registered()
```

### 3.3 多 plugin 共用資源

```python
pw.register(
    "finfun_core.twse_calendar",
    _warmup_twse_calendar,
    skip_if_exists=True,   # 先到先得
)
```

---

## 4. 查詢狀態

```python
import funlab.core.prewarm as pw
summary = pw.status()
# { "task_name": { "status": "done"|"failed"|"pending", "elapsed": 1.23, "error": None } }
```

---

## 5. 設計決策紀錄

| 決策 | 理由 |
|---|---|
| 放在 `funlab-libs` | 所有 plugin 的依賴根；避免循環依賴 |
| 模組層級 dict + 函式（無 class）| Plugin import 時即可登記；不需傳遞 app |
| `blocking=True` 取代 `priority=CRITICAL` | 語意直接 |
| `delay` 取代 `depends_on` | 簡單錯開時間，無需 DAG 解析 |
| 移除 `_lazy` wrapper（v2）| `sys.modules` 已提供快取；wrapper 增加維護成本且降低 IDE 支援 |
| function-level import + prewarm | 最小封裝 + 最大效益的平衡點 |

---

## 6. 參考文件

- [IMPORT_BEST_PRACTICES.md](IMPORT_BEST_PRACTICES.md) — Import 分類標準與最佳實踐
- [EXECUTION_PLAN.md](EXECUTION_PLAN.md) — 各 plugin 移入排程與查核點
- [CHECKPOINTS.md](CHECKPOINTS.md) — 每個里程碑的驗證標準
