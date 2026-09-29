# Prewarm 開發使用指南

> `funlab.core.prewarm`：啟動後的一次性背景任務註冊表。怎麼用、必須遵守的規則在本檔；
> 實作機制（鎖、線程、簽名注入等）以 `funlab/core/prewarm.py` 的 docstring／註記為準。
> 觸發點：`appbase._run_prewarm()`（startup plugin 註冊完成後、進入服務前）；
> 整體開關：`app.config['PREWARM_ENABLED']`（預設 True）。

## 1. 心智模型與邊界

Prewarm 只有 `register / run / status` 三個概念。它**不是**排程器（重複執行找
funlab-sched）、**不是**事件總線（通知找 HookManager）、**沒有**任務依賴圖。
框架只定義機制，任務一律由 plugin 在 `register_prewarm_tasks()` 自宣告。

| 場景 | 用什麼 |
|---|---|
| 重 import（pandas/ffn/exchange_calendars）在首請求前暖好 | ✅ prewarm |
| 一次性初始化（日曆註冊、DB engine 暖機、快取預建） | ✅ prewarm |
| 定期重複執行 | ❌ funlab-sched |
| 「事件發生時通知我」 | ❌ HookManager |
| 任務依賴順序（先 A 後 B） | ❌ 不支援（`depends_on` 是殭屍參數） |
| 券商登入、長外部 I/O | ❌ 框架不做 timeout/併發上限；`category="service_connect"` 只准短連線檢查，逾時記 ERROR log |
| 可重複觸發／需 Event 同步的連線池初始化 | ❌ 自管執行緒＋Event（例：quotesvcs Pool） |

## 2. 標準寫法（照抄範本）

```python
from funlab.core.prewarm import register_prewarm

class XxxPlugin(ServicePlugin):
    def register_prewarm_tasks(self) -> None:
        register_prewarm(
            "xxx.some_warmup",        # 慣例 "{plugin}.{task}"，全域唯一
            self._warmup_something,
            blocking=False,           # 只有「服務前必須完成」才 True（此時 delay 無效）
            delay=2.0,                # 僅背景任務生效：啟動後延後 N 秒，避開尖峰
            skip_if_exists=True,      # 共享資源：先到者赢，永不 ValueError
            owner="xxx",
            budget_sec=60.0,          # SLO：逾時只標記 budget_exceeded＋log，不中止
        )

    @staticmethod
    def _warmup_something() -> None:
        import some_heavy_module  # noqa: F401   # 一律函數內 import
```

倉內真實範本：`finfun-fundmgr/view.py`、`finfun-quotesvcs/service.py`
（後者示範 `resource_key` 用法）。

三條鐵律：

1. **任務函數內部才 import 重模組**，頂部 import 等於預熱白做。
2. **任務必須冪等、可容忍失敗**。失敗只記 `status='failed'`＋warning log，不重試、
   不中止啟動；其消費者必須本來就會自行 lazy init 兜底（例：`fin_cale` 每個公開
   函數都先呼叫 `register()`——prewarm 只是提前做，不是替你做）。
3. **plugin 必須 `load_mode = "startup"`**（pyproject.toml
   `[tool.funlab_plugin_metadata.*]`）。lazy plugin 實例化時早已錯過 `run()`，
   任務永久 `pending`（註冊時記 WARNING，且 `status()` 中標 `late: true`，
   不影響 `/health` 判定）。

## 3. 規則與紅線（違反會出事，均為語意合同）

- **共享資源用「相同 name + `skip_if_exists=True`」**：先到者赢發生在註冊期，
  第一個註冊者的 `blocking` 設定整個生效。`resource_key` 是第二道去重（run() 期），
  同 key 多任務時 **blocking 註冊者優先執行**、其餘標 `skipped_shared`；
  建議只用在全部同資源任務都是背景的情境。
- **任務主體不得 `except: pass` 吞錯**：框架已保證函數拋出的例外（含 TypeError）原樣記 `failed`、
  絕不重跑；但失敗後不會重試，副作用型任務（寫檔／入帳）仍要自己冪等。主體自用
  `except: pass` 吞掉例外會讓 `status()` 假 `done`，框架 `failed` 態形同虛設——
  要包容錯就只包最小範圍並在處理後 `raise` 向傳。
- **`blocking=True` 不寫 `delay`**：delay 對 blocking 任務無效（被忽略並記 debug）。
- **blocking 任務串行**且全部進啟動路徑，總耗時＝各任務之和：控制數量、配
  `budget_sec` 觀測。
- **背景任務是 daemon 線程**：進程退出時未跑完的直接消失，任務不要持有需釋放的
  外部資源。
- **`budget_sec` 只標記不中止**：它觀測 SLO，不執行 SLO。
- **殭屍參數勿用**：`priority / timeout / tags / depends_on / description` 傳了無效
  （相容佔位；唯 `background=False` 換算成 `blocking=True`）。新代碼只用正式參數。

## 4. API 速查

| API | 用途 |
|---|---|
| `register(name, func, *, blocking, delay, skip_if_exists, replace, category, resource_key, owner, budget_sec)` | 註冊；重複 name 未給 skip/replace → `ValueError` |
| `register_prewarm(...)` | plugin 主用別名（多接受殭屍參數） |
| `@deferred_import(name, ...)` / `@prewarm_task(...)` | 裝飾器形式，回傳原函數 |
| `run(app=None)` | app bootstrap 呼叫一次；二次 no-op；func 宣告必選位置參數則注入 `app`。順序：resource_key 去重（blocking 優先）→ blocking 依序同步跑 → 其餘逐條起 daemon 線程 |
| `status()` | 每任務：status / category / resource_key / owner / late / elapsed / queue_delay / budget_sec / budget_exceeded / error |
| `unregister(name)` / `reset()` | 移除／清空（reset 僅供測試） |

status 值：`pending`（未跑）、`running`、`done`、`failed`（看 `error`）、
`skipped_shared`（資源去重輸家）。

## 5. 測試規範

`_entries` 是模組級全域狀態，同 process 多 app 會互汙——測試一律 autouse
fixture 前後 `reset()`（照抄 `tests/test_prewarm.py::clean_registry`）。

## 6. 可觀察性

- 每任務完成 log 一行（名稱、status、elapsed、queue_delay、budget）；
  啟動後 `grep 'Deferred import' <app log>` 看全貌。
- `/health` 的 `prewarm` 欄即 `status()`（明細僅回環來源或 HEALTH_DETAIL 放行）。
  任一**非 late** 任務仍 `pending` → 整體 `degraded`/503；`late: true`（run() 後
  註冊、永不執行）不計入。上線前確認 prewarm 段無長期 `pending`。

## 7. 快速驗證

```bash
cd funlab-libs && python -m pytest -q tests/test_prewarm.py   # 框架測試基線
curl -s http://127.0.0.1:5000/health | python -m json.tool    # 看 prewarm 段
```
