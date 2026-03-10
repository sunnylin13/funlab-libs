# Prewarm Framework – Plugin Migration Execution Plan

> **Branch**: `feature/prewarm-framework`  
> **Updated**: 2026-03-10  
> **Owner**: Platform / Core team

---

## 概要

本計畫以「增量、可回退」原則，逐步將各 plugin 的高成本初始化移入 `funlab.core.prewarm` 框架。  
每個 Phase 結束後均需通過 [CHECKPOINTS.md](CHECKPOINTS.md) 驗證，確認系統無衰退後才推進下一 Phase。

**總 Phase 數：6**（Phase 0 = 框架建置，已完成）

---

## Phase 0 ✅ – 框架建置（`funlab-libs`）

**狀態**：已完成  
**Branch**：`feature/prewarm-framework`

### 完成內容
- [x] 實作 `funlab/core/prewarm.py`（`PrewarmTask`, `PrewarmRegistry`, `PrewarmManager`）
- [x] 在 `_FlaskBase.__init__` 加入 `_run_prewarm()` hook（`appbase.py`）
- [x] 更新 `funlab/core/__init__.py` 匯出 prewarm 公開 API
- [x] 建立 `docs/prewarm/` 文件目錄（README、EXECUTION_PLAN、CHECKPOINTS）
- [x] 建立測試 `tests/test_prewarm.py`

### 驗證
```bash
cd funlab-libs
python -m pytest tests/test_prewarm.py -v
```

---

## Phase 1 – `finfun-core`：TWSE Calendar 預熱

**狀態**：🔜 待實作  
**Branch**：從 `feature/prewarm-framework` 開 PR 或在同 branch 繼續  
**目標 Plugin**：`finfun-core`  
**預期效益**：消除首次 `/fundmgr/portfolio` TWSE calendar 初始化 50–83 s 延遲

### 任務
1. **新增任務登記**（`finfun-core/finfun/utils/fin_cale.py` 或 `finfun-core/finfun/core/__init__.py`）：
   ```python
   from funlab.core.prewarm import register_prewarm, PrewarmPriority

   def _warmup_twse_calendar():
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
2. **移除舊程式**（`finfun-fundmgr/finfun/fundmgr/view.py`）：
   - 刪除 `__init__` 中的 `_init_calendar_worker` background thread（已被框架取代）。
3. **驗證**：啟動後 `prewarm_manager.status()["twse_calendar"]["status"] == "success"`。

### 查核點
- [ ] `tests/test_prewarm_integration.py::test_twse_calendar_prewarm` 通過
- [ ] 首次請求 `twse calendar init took` log < 1.0 s（因為已預熱）
- [ ] 服務啟動後 120 s 內 log 出現 `Prewarm [SUCCESS] 'twse_calendar'`

---

## Phase 2 – `finfun-core`：SQLAlchemy DB Engine Warm-up

**狀態**：🔜 待實作  
**目標 Plugin**：`funlab-libs`（`DbMgr`）或 app bootstrap  
**預期效益**：消除首次 DB request ORM engine 建立延遲（1–3 s）

### 任務
1. 在 `funlab-libs` app `register_plugins` 完成後，透過 prewarm 登記 DB engine warm-up：
   ```python
   from funlab.core.prewarm import register_prewarm, PrewarmPriority

   def _warmup_db(app):
       if app.dbmgr:
           engine = app.dbmgr.get_db_engine()
           with engine.connect() as conn:
               conn.execute(text("SELECT 1"))

   register_prewarm(
       name="db_engine_warmup",
       func=_warmup_db,        # accepts app as argument
       priority=PrewarmPriority.HIGH,
       timeout=15.0,
       tags=["db", "funlab-libs"],
       description="Warm-up SQLAlchemy engine and connection pool",
   )
   ```
   > 備注：`PrewarmManager._resolve_func_call()` 會自動偵測函式是否接受 `app` 參數。
2. **驗證**：首次 DB request 無冷啟動卡頓。

### 查核點
- [ ] `test_prewarm::test_task_receiving_app_arg` 通過
- [ ] log 出現 `Prewarm [SUCCESS] 'db_engine_warmup'`
- [ ] 使用 `pytest --benchmark` 確認首次 DB query < 100 ms

---

## Phase 3 – `finfun-fundmgr`：Form Choices Cache

**狀態**：🔜 待實作  
**目標 Plugin**：`finfun-fundmgr`  
**預期效益**：消除首次 `portfolio()` form choices 65 s ORM+engine 初始化延遲

### 背景
`load_all_managers_email()` / `load_manager_accounts()` 在首次呼叫時會觸發：
- SQLAlchemy ORM class mapping 完成
- 實際執行 SELECT 查詢

### 任務
1. 在 `finfun-fundmgr/finfun/fundmgr/__init__.py` 或 plugin `__init__` 登記：
   ```python
   from funlab.core.prewarm import register_prewarm, PrewarmPriority

   # 使用 module-level 快取
   _managers_email_cache: list[str] = []

   def _warmup_manager_list(app):
       from finfun.fundmgr.utils import load_all_managers_email
       _managers_email_cache.clear()
       _managers_email_cache.extend(load_all_managers_email(app.dbmgr))

   register_prewarm(
       name="fundmgr_form_choices",
       func=_warmup_manager_list,
       priority=PrewarmPriority.NORMAL,
       timeout=30.0,
       depends_on=["db_engine_warmup"],   # 等 DB warm-up 完成
       tags=["cache", "finfun-fundmgr"],
   )
   ```
2. 在 `portfolio()` 讀取快取（cache-aside）：
   ```python
   if _managers_email_cache:
       emails = _managers_email_cache
   else:
       emails = load_all_managers_email(self.app.dbmgr)
   ```
3. 設定 TTL（建議 5 min）以確保資料不過時。
4. 移除舊 inline background thread（若有）。

### 查核點
- [ ] 首次請求 `form choices built` log < 1 s
- [ ] 第二次請求 `form choices built` < 0.2 s（已有快取）
- [ ] 測試：`test_prewarm_integration.py::test_fundmgr_form_choices_cache`

---

## Phase 4 – `finfun-quotesvcs`：Quote Agent Import 預熱

**狀態**：🔜 待實作  
**目標 Plugin**：`finfun-quotesvcs` / `finfun-broker-sino`  
**預期效益**：消除 QuoteService 重型 import 45–62 s 延遲（SinoStockQuoteAgent）

### 任務
1. 在 `finfun-quotesvcs` plugin `__init__` 登記：
   ```python
   register_prewarm(
       name="quote_agent_import",
       func=lambda: __import__('finfun.broker_sino.agent'),
       priority=PrewarmPriority.HIGH,
       timeout=90.0,
       tags=["quote", "finfun-quotesvcs"],
       description="Pre-import SinoStockQuoteAgent to avoid first-request delay",
   )
   ```
2. 確認 `finfun-fundmgr view.py` 中的 safe lookup `self.app.plugins.get('quote')` 已存在（已完成）。
3. 在 Quote plugin 初始化完成後確認 `quote_agent_import` status 為 `success`。

### 查核點
- [ ] `prewarm_manager.status()["quote_agent_import"]["status"] == "success"`
- [ ] 首次 portfolio 請求不出現 `QuoteService lazy load` 的 45+ s log
- [ ] 測試：`test_prewarm_integration.py::test_quote_agent_prewarm`

---

## Phase 5 – 其他 Plugin 登記（可選 / 依需要）

對以下 plugin 評估是否有值得預熱的重型模組：

| Plugin | 潛在預熱目標 | 優先級建議 |
|---|---|---|
| `finfun-option` | TA-Lib / numpy 初始化 | LOW |
| `finfun-quantanlys` | pandas / scipy 首次 import | LOW |
| `finfun-hedge` | 同上 | LOW |
| `funlab-auth` | OAuth client 初始化 | NORMAL |

登記模式與 Phase 1–4 相同；依重要性選擇性實作。

---

## Phase 6 – 清理與收斂

**狀態**：🔜 最終 Phase  
**目標**：確保系統無餘舊程式，prewarm 統一管理

### 任務清單
- [ ] 移除 `finfun-fundmgr/view.py` 中的舊 `_init_calendar_worker` thread（Phase 1 完成後）
- [ ] 移除 `finfun-fundmgr/view.py` 中所有 inline `threading.Thread(target=...)` warm-up 邏輯
- [ ] 搜尋並移除其他 plugin 中的舊 background warm-up threads（`grep -r 'Thread.*warm\|Thread.*init.*calendar\|Thread.*quote'`）
- [ ] 統一所有 plugin 的 prewarm 登記格式（使用 `@prewarm_task` 裝飾器或 `register_prewarm`）
- [ ] 更新 plugin 開發指南（`docs/PLUGIN_DEVELOPMENT_GUIDE.md`）加入 prewarm 登記說明
- [ ] CI 加入 prewarm integrity test（確認每次 build 所有登記任務可成功執行）

### 查核點
- [ ] `grep -r '_init_calendar_worker\|threading.Thread.*calendar' finfun-fundmgr/` 回傳空
- [ ] `prewarm_manager.status()` 所有任務 status 為 `success` 或 `skipped`（非 `failed`/`timeout`）
- [ ] 系統冷啟動首次請求延遲 < 5 s（扣除真正網路/DB 耗時）

---

## 時程與依賴

```
Phase 0 [DONE]
    │
    ├─► Phase 1 (twse_calendar)  ─────────────────── 可立即開始
    ├─► Phase 2 (db_engine)  ──────────────────────── 可立即開始
    │
    └─► Phase 3 (form cache)  ← depends on Phase 2
    └─► Phase 4 (quote agent) ← 可平行進行
    └─► Phase 5 (optional)    ← 可平行進行
    └─► Phase 6 (cleanup)     ← 等待 Phase 1–4 完成
```

---

## 風險與緩解

| 風險 | 緩解 |
|---|---|
| 某 task 超時拖慢啟動 | 每個任務有 `timeout`；記錄 WARNING 但不阻塞其他任務 |
| 預熱資料過期（cache stale）| 設定 TTL；cache-aside + fallback 到 DB |
| 測試環境不想預熱（慢）| `PREWARM_ENABLED = false` 在 test config |
| 循環依賴（plugin A depends B depends A）| `depends_on` 採 busy-wait + timeout；不做循環偵測（暫時） |
| 多次呼叫 `run()`（reload）| `PrewarmManager._run_called` 防重入保護 |
