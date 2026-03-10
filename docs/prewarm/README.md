# Prewarm Framework

> **Branch**: `feature/prewarm-framework`  
> **Module**: `funlab.core.prewarm`  
> **Status**: 🚧 Implementation in progress – see [EXECUTION_PLAN.md](EXECUTION_PLAN.md)

---

## 1. 問題背景

Funlab / Finfun 系統在首次 HTTP 請求時會觸發多項一次性初始化：

| 資源 | 觸發位置 | 首次耗時（實測）|
|---|---|---|
| TWSE exchange_calendars 註冊 | `finfun.utils.fin_cale._ensure_calendar_registered()` | 50–83 s |
| QuoteService (SinoStockQuoteAgent) import | `finfun-broker-sino` 載入 | 45–62 s |
| SQLAlchemy engine + connection pool | `DbMgr.get_db_engine()` 首次呼叫 | 1–3 s |
| Form choices build（ORM 首次 SELECT）| `load_all_managers_email()` | ~65 s（含 ORM 初始化）|

**目標**：把上述成本移出請求路徑，由統一框架在啟動期間非同步預熱，消除使用者端「首次請求長時間停頓」問題。

---

## 2. 架構設計

```
┌─────────────────────────────────────────────────────────┐
│  App Bootstrap  (_FlaskBase.__init__)                    │
│                                                          │
│  1. register_routes()                                    │
│  2. register_plugins()  ──► each plugin calls            │
│       prewarm_registry.register(name, func, priority)    │
│  3. register_menu()                                      │
│  4. dbmgr.create_registry_tables()                       │
│  5. _run_prewarm()  ──► prewarm_manager.run(app)         │
│       ├── CRITICAL tasks  (blocking, serial)             │
│       └── HIGH/NORMAL/LOW tasks (ThreadPoolExecutor)     │
└─────────────────────────────────────────────────────────┘
```

### 2.1 核心類別

| 類別 / 函式 | 職責 |
|---|---|
| `PrewarmTask` | 描述單一任務（名稱、callable、優先級、timeout、依賴…）|
| `PrewarmRegistry` | Thread-safe 全域任務倉庫；plugin 在 `__init__` 登記 |
| `PrewarmManager` | 取出任務、管理執行順序、記錄 metrics |
| `register_prewarm()` | 便捷函式；等同 `prewarm_registry.register()` |
| `@prewarm_task(...)` | 裝飾器形式；同上 |
| `prewarm_registry` | 模組層級 singleton |
| `prewarm_manager`  | 模組層級 singleton |

### 2.2 優先級

```python
class PrewarmPriority(IntEnum):
    CRITICAL = 40   # 阻塞式；app 啟動前必須完成（如 DB migration）
    HIGH     = 30   # 背景；立即啟動（如 calendar 登記、DB pool warm-up）
    NORMAL   = 20   # 背景；HIGH 任務後啟動（如 ORM form cache）
    LOW      = 10   # 背景；延後啟動（如預先載入歷史資料快取）
```

### 2.3 執行流程

```
register_plugins 完成
    │
    ▼
_run_prewarm()
    │
    ├─► CRITICAL tasks ──► 同步執行（阻塞）──► 完成後繼續
    │
    └─► HIGH/NORMAL/LOW tasks ──► ThreadPoolExecutor (max_workers=4)
              │
              ├► Thread: task_A (HIGH)
              ├► Thread: task_B (HIGH, depends_on=[task_A])   # 等 task_A 完成
              └► Thread: task_C (NORMAL)
```

---

## 3. 快速開始（Plugin 端）

```python
# 在 plugin __init__.py 或 view.py __init__ 中登記任務
from funlab.core.prewarm import register_prewarm, PrewarmPriority

def _warmup_twse_calendar():
    """Pre-register TWSE exchange_calendars to avoid first-request delay."""
    from finfun.utils.fin_cale import _ensure_calendar_registered
    _ensure_calendar_registered()

register_prewarm(
    name="twse_calendar",
    func=_warmup_twse_calendar,
    priority=PrewarmPriority.HIGH,
    timeout=120.0,
    tags=["calendar", "finfun-core"],
    description="Pre-register TWSE exchange_calendars",
)
```

或使用裝飾器形式：

```python
from funlab.core.prewarm import prewarm_task, PrewarmPriority

@prewarm_task("twse_calendar", priority=PrewarmPriority.HIGH, timeout=120)
def _warmup_twse_calendar():
    from finfun.utils.fin_cale import _ensure_calendar_registered
    _ensure_calendar_registered()
```

---

## 4. App 端整合

`_FlaskBase._run_prewarm()` 已自動呼叫，無需額外設定。

若需停用（例如 test environment）：

```toml
# config.toml
[MyApp]
PREWARM_ENABLED = false
```

若需等待所有任務完成（例如在 health-check endpoint）：

```python
from funlab.core.prewarm import prewarm_manager
ok = prewarm_manager.wait(timeout=180)
```

查詢狀態（供 `/health` 或 logging）：

```python
summary = prewarm_manager.status()
# Returns: { "twse_calendar": { "status": "success", "elapsed": 12.3, ... }, ... }
```

---

## 5. 設計決策紀錄

| 決策 | 理由 |
|---|---|
| 放在 `funlab-libs` 而非 `finfun-core` | funlab-libs 是所有 plugin 的依賴根；放在此處確保所有 plugin 均可登記，不形成循環依賴 |
| 模組層級 singleton（`prewarm_registry` / `prewarm_manager`）| Plugin 在 import 時即可登記；不需傳遞 app 物件 |
| CRITICAL 優先阻塞，其餘背景 | 最大化啟動速度；只有真正必要的才阻塞 |
| Timeout per task | 防止單一任務 hang 住整個啟動流程 |
| `depends_on` 依賴鏈 | 允許 task_B 等待 task_A（例如 DB engine ready 後才能做 ORM query） |
| `PREWARM_ENABLED` flag | 支援 dev/test 環境停用，不改程式碼 |

---

## 6. 參考文件

- [EXECUTION_PLAN.md](EXECUTION_PLAN.md) – 各 plugin 移入排程與查核點
- [CHECKPOINTS.md](CHECKPOINTS.md) – 每個里程碑的驗證標準
- [../PLUGIN_LIFECYCLE_ARCHITECTURE.md](../PLUGIN_LIFECYCLE_ARCHITECTURE.md) – Plugin 生命週期架構
