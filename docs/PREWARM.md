# Prewarm — 延遲載入（deferred import）框架現行 API

實作：`funlab/core/prewarm.py`（模組級函數 + 一個 dict，無 scheduler class）。
觸發點：`funlab/core/appbase.py:_FlaskBase._run_prewarm`（plugin 註冊完成後、進入服務前）。

## 設計原則

- **框架不含任務定義**：每個 plugin 在 `Plugin.register_prewarm_tasks()`
  （`funlab/core/plugin.py`）註冊自己的任務。
- 每個非阻塞任務一條 daemon `threading.Thread`（I/O-bound 場景，刻意不引入
  ThreadPoolExecutor/priority/depends-on 圖）。
- 與 Hook 機制的分界：hook 是「事件廣播」（發生 X 時通知我），prewarm 是
  「一次性任務執行」（把 Y 在背景做一次）。

## API

### `register(name, func, *, blocking=False, delay=0.0, skip_if_exists=False, replace=False, category="import", resource_key=None, owner=None, budget_sec=None)`

| 參數 | 意義 |
|---|---|
| `name` | 全域唯一 id，慣例 `"{plugin}.{task}"`；重複且未給 `skip_if_exists`/`replace` → `ValueError` |
| `func` | 零參數 callable；若宣告一個必選位置參數則注入 `app`（`_call` 用 signature 判斷） |
| `blocking=True` | 在 `run()` 內同步執行完才返回（app 開始服務前完成） |
| `delay` | `run()` 後睡 N 秒再啟動（避開啟動尖峰） |
| `skip_if_exists` | 名稱已註冊→靜默略過（共享資源：先到者赢） |
| `replace` | 覆蓋既有註冊（測試/熱重載用） |
| `category` | `"import"` / `"service_connect"` / `"cache_build"`（僅observability） |
| `resource_key` | 資源級去重：同 key 只跑第一個，其餘標 `skipped_shared` |
| `owner` | 註冊者 plugin 名（observability） |
| `budget_sec` | SLO 預算；逾時 `budget_exceeded=True` + warning log |

### `register_prewarm(...)`

`register` 的便利別名（plugin 作者主用 API）。額外接受
`priority/timeout/background/tags/depends_on/description` 等**舊參數但無效**
（相容佔位；`background=False` 會被換算成 `blocking=True`）。

### `deferred_import(name, *, ...)` / `prewarm_task`

裝飾器形式：`@deferred_import("x.y", blocking=True)` 包一個函數即完成註冊，
回傳原函數。`prewarm_task` 是其舊別名。

### `run(app=None)`

由 app bootstrap 呼叫一次（`_run_prewarm`；`app.config['PREWARM_ENABLED']=False`
可整體停用）。二次呼叫為 no-op（`_run_called` 守衛）。順序：先依 `resource_key`
去重 → blocking 任務依序同步跑 → 其餘逐條起 daemon 執行緒。

### `status()` → dict / `unregister(name)` / `reset()`

`status()` 每任務回 `status(pending|running|done|failed|skipped_shared)`、
`category`、`resource_key`、`owner`、`elapsed`、`queue_delay`、`budget_sec`、
`budget_exceeded`、`error`。`unregister` 冪等移除；`reset()` 清空全部並重設
run 守衛（**僅供測試**）。

## plugin 端用法（標準模式）

```python
from funlab.core.prewarm import register_prewarm

class MyPlugin(ServicePlugin):
    def register_prewarm_tasks(self) -> None:
        register_prewarm(
            "myplugin.twse_calendar",
            self._warmup_calendar,
            skip_if_exists=True,            # 共享資源：先到者赢
            resource_key="twse_calendar",   # 跨 plugin 資源級去重
            owner="myplugin",
            delay=2.0,
            budget_sec=120.0,
        )

    @staticmethod
    def _warmup_calendar() -> None:
        from finfun.utils.fin_cale import _ensure_calendar_registered
        _ensure_calendar_registered()
```

## 必知的現行限制

1. **`run()` 之後註冊的任務永遠 `pending`、不會執行**。lazy plugin 首次被
   `get_plugin()` 觸發實例化時才跑 `register_prewarm_tasks()`，早已錯過 `run()`——
   保證要跑的预热請把 plugin 設 `load_mode="startup"`。（改善提案：加入警告，
   見 `IMPROVEMENT_PLAN.md` LIB-16。）
2. `_entries` 是模組級全域狀態：同 process 多 app（測試場景）會互相污染，
   測試請用 fixture 前後 `reset()`（見 `tests/test_prewarm.py::clean_registry`）。
3. `status()` 快照不持鎖保護執行中寫入（GIL 下 dict 讀取安全，僅供展示用途）。
4. 背景任務**不應**做長外部 I/O（券商登入等）：`category="service_connect"` 只是
   標記，框架不做 timeout/併發上限（`budget_sec` 只標記不中止）。

## 可觀察性

- 每任務完成會 log：名稱、status、elapsed、queue_delay、budget。
- 舊啟動分析報告（docs/prewarm/）已刪除；其結論已沉澱進本 API
  （resource_key 去重、category/owner、budget SLO）。
- `/health` 端點的 `prewarm` 欄位即 `status()` 輸出（由 funlab-flaskr 提供）。

## 測試

```bash
cd funlab-libs && python -m pytest -q tests/test_prewarm.py
```
