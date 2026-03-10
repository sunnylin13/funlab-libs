# Prewarm Framework

> **Branch**: `feature/prewarm-framework`
> **Module**: `funlab.core.prewarm`
> **Status**:  Phase 0 完成  見 [EXECUTION_PLAN.md](EXECUTION_PLAN.md)

---

## 1. 問題背景

Funlab / Finfun 系統在首次 HTTP 請求時會觸發多項一次性初始化：

| 資源 | 觸發位置 | 首次耗時（實測）|
|---|---|---|
| TWSE exchange_calendars 註冊 | `finfun.utils.fin_cale._ensure_calendar_registered()` | 5083 s |
| QuoteService / Broker SDK import | `finfun-broker-sino` / `yuanta` 模組層級 | 2062 s |
| SQLAlchemy engine + connection pool | `DbMgr.get_db_engine()` 首次呼叫 | 13 s |
| Form choices（ORM 首次 SELECT）| `load_all_managers_email()` | ~565 s（含 ORM 初始化）|
| pandas / numpy / scipy / ffn import | `finfun-quantanlys`, `finfun-fundmgr` | 28 s |

**目標**：把上述成本移出請求路徑，由統一框架在啟動期間非同步預熱，消除使用者端「首次請求長時間停頓」問題。

---

## 2. 架構設計（簡化後）

```

  App Bootstrap  (_FlaskBase.__init__)

  1. register_routes()
  2. register_plugins()   each plugin calls
       prewarm.register(name, func, blocking=False)
  3. register_menu()
  4. dbmgr.create_registry_tables()
  5. _run_prewarm()   prewarm.run(app)
        blocking=True tasks   (同步，阻塞直到完成)
        blocking=False tasks  (daemon Thread，非阻塞)

```

### 2.1 公開 API

| 函式 / 屬性 | 職責 |
|---|---|
| `register(name, func, *, blocking, delay, skip_if_exists, replace)` | 登記一個預熱任務 |
| `unregister(name)` | 取消登記（主要供測試使用）|
| `run(app=None)` | 執行所有已登記的任務；只能執行一次（由 app bootstrap 呼叫）|
| `status()  dict` | 回傳每個任務的執行狀態 |
| `reset()` | 清除所有登記 + 重置執行旗標（**僅供測試**）|
| `register_prewarm(name, func, **kwargs)` | 便捷別名；接受舊版參數（`priority`, `timeout`, `depends_on` 等）並忽略 |
| `deferred_import(name, *, blocking, delay)` | 裝飾器形式的 `register()` |
| `prewarm_task` | `deferred_import` 的別名 |

### 2.2 關鍵參數

| 參數 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `blocking` | `bool` | `False` | `True` = 同步執行（阻塞 `run()` 回傳）；`False` = daemon Thread |
| `delay` | `float` | `0.0` | 啟動 Thread 後等待 N 秒再執行（避免與其他任務競用啟動資源）|
| `skip_if_exists` | `bool` | `False` | 若同名已登記，靜默跳過（多 plugin 登記同一共用資源時使用）|
| `replace` | `bool` | `False` | 若同名已登記，強制覆蓋 |

### 2.3 執行流程

```
register_plugins 完成


prewarm.run(app=self)

     blocking=True tasks  依登記順序同步執行  完成後繼續

     blocking=False tasks  各自啟動 daemon Thread

               Thread: task_A (delay=0)
               Thread: task_B (delay=30)  # 延後 30s，讓 task_A 先完成
               Thread: task_C (delay=0)
```

> **注意**：`depends_on` 已移除。若需先後依賴，使用 `delay` 錯開啟動時間，
> 或在任務 callable 內部等待前置條件。

---

## 3. Plugin 端使用方式

### 3.1 在 `register_prewarm_tasks()` 中登記

每個繼承 `EnhancedViewPlugin` / `EnhancedServicePlugin` 的 plugin，
應複寫 `register_prewarm_tasks()` 進行登記：

```python
from funlab.core.enhanced_plugin import EnhancedViewPlugin
from funlab.core import prewarm

class MyPlugin(EnhancedViewPlugin):

    def register_prewarm_tasks(self):
        prewarm.register(
            "myplugin.heavy_import",
            self._warmup_heavy_import,
            blocking=False,
            delay=5.0,          # 等 app 其他初始化完成再執行
            skip_if_exists=True, # 避免重複登記
        )

    def _warmup_heavy_import(self):
        import heavy_module   # 預先載入，使 sys.modules 快取
```

### 3.2 裝飾器形式（模組層級任務）

```python
from funlab.core.prewarm import deferred_import

@deferred_import("finfun_core.twse_calendar")
def _warmup_twse_calendar():
    from finfun.utils.fin_cale import _ensure_calendar_registered
    _ensure_calendar_registered()
```

### 3.3 多 plugin 共用資源：用 `skip_if_exists=True`

```python
# PluginA 和 PluginB 都需要 exchange_calendars，只需執行一次
prewarm.register(
    "finfun_core.twse_calendar",
    _warmup_twse_calendar,
    blocking=False,
    skip_if_exists=True,   # 先到先得，後到者靜默忽略
)
```

---

## 4. App 端整合

`_FlaskBase._run_prewarm()` 已自動呼叫，無需額外設定。

查詢狀態（供 `/health` 或 logging）：

```python
import funlab.core.prewarm as pw
summary = pw.status()
# Returns: { "task_name": { "status": "done"|"failed"|"pending", "elapsed": 1.23, "error": None } }
```

---

## 5. 設計決策紀錄

| 決策 | 理由 |
|---|---|
| 放在 `funlab-libs` 而非 `finfun-core` | 所有 plugin 的依賴根；避免循環依賴 |
| 模組層級 dict + 模組層級函式（無 Registry/Manager class）| Plugin 在 import 時即可登記；不需傳遞 app 物件；class 帶來不必要複雜度 |
| `blocking=True` 取代 `priority=CRITICAL` | 語意更直接：「我需要在 app 完全就緒前完成」|
| `delay` 取代 `depends_on` 依賴圖 | 依賴圖需要 DAG 解析；`delay` 用簡單錯開時間達到同樣效果 |
| Hook（HookManager） Prewarm | Hook = observer/事件廣播；Prewarm = background task execution，互補非重疊 |
| `skip_if_exists=True` 處理重複登記 | 多 plugin 共用同一重型資源（如 exchange_calendars）時，先到先得，不拋例外 |

---

## 6. 參考文件

- [EXECUTION_PLAN.md](EXECUTION_PLAN.md)  各 plugin 移入排程與查核點
- [CHECKPOINTS.md](CHECKPOINTS.md)  每個里程碑的驗證標準
