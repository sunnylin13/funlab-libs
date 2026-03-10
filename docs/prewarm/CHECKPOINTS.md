# Prewarm Framework - Checkpoints & Validation

> 每個 Phase 完成後，必須通過本文件中對應的查核點，才可合入 main 或推進下一 Phase。

---

## 通用查核標準（全 Phase 適用）

| # | 查核項目 | 命令 / 方法 |
|---|---|---|
| G-1 | 所有現有測試不衰退 | `pytest funlab-libs/tests/ -v` |
| G-2 | prewarm 測試 100% 通過 | `pytest funlab-libs/tests/test_prewarm.py -v` |
| G-3 | 型別標注無明顯錯誤 | `mypy funlab/core/prewarm.py --ignore-missing-imports` |
| G-4 | 程式碼無 import cycle | `python -c "import funlab.core.prewarm"` |

---

## Phase 0 - 框架建置 OK

| # | 查核項目 | 狀態 |
|---|---|---|
| P0-1 | `funlab/core/prewarm.py` 存在且可 import | OK |
| P0-2 | `prewarm.register(name, func)` 不拋錯 | OK |
| P0-3 | `prewarm.run()` blocking=True 任務同步執行 | OK |
| P0-4 | `prewarm.run()` blocking=False 任務以 daemon Thread 執行 | OK |
| P0-5 | 任務執行失敗時 status == "failed"，不影響其他任務 | OK |
| P0-6 | `run()` 呼叫兩次，任務只執行一次 | OK |
| P0-7 | `_FlaskBase._run_prewarm()` 呼叫 `prewarm.run(app=self)` | OK |
| P0-8 | `funlab/core/__init__.__getattr__` 可懶載 prewarm 匯出 | OK |
| P0-9 | `skip_if_exists=True` 防止重複登記 | OK |
| P0-10 | 接受 app 參數的任務可自動注入 app | OK |
| P0-11 | `tests/test_prewarm.py` 全數通過 | OK 46/46 |
| P0-12 | `docs/prewarm/` 文件符合現行 API | OK (2026-03-10 更新) |

**執行驗證：**
```powershell
cd D:\08.dev\fundlife\funlab-libs
python -m pytest tests/test_prewarm.py -v --tb=short
```

---

## Phase 1 - TWSE Calendar Prewarm

| # | 查核項目 | 狀態 |
|---|---|---|
| P1-1 | `prewarm.register("finfun_core.twse_calendar", ...)` 在 finfun-core 中登記 | TBD |
| P1-2 | 啟動後 120 s 內 `prewarm.status()["finfun_core.twse_calendar"]["status"] == "done"` | TBD |
| P1-3 | 首次 `/fundmgr/portfolio` log 中 `twse calendar init took` < 1.0 s | TBD |
| P1-4 | 舊 `_init_calendar_worker` thread 已從 fundmgr/view.py 移除 | TBD |

**手動驗證（Flask shell）：**
```python
import funlab.core.prewarm as pw, time
time.sleep(30)
print(pw.status()["finfun_core.twse_calendar"])
```

---

## Phase 2 - DB Engine Warm-up

| # | 查核項目 | 狀態 |
|---|---|---|
| P2-1 | `prewarm.register("funlab.db_engine_warmup", ...)` 已登記 | TBD |
| P2-2 | `prewarm.status()["funlab.db_engine_warmup"]["status"] == "done"` | TBD |
| P2-3 | 啟動後首次 DB query log 無 engine 建立初始化訊息 | TBD |

---

## Phase 3 - FundMgr pandas/ffn + Form Choices

| # | 查核項目 | 狀態 |
|---|---|---|
| P3-1 | `FundMgrView.register_prewarm_tasks()` 已實作並登記 pandas/ffn + form_choices 任務 | TBD |
| P3-2 | `prewarm.status()["fundmgr.pandas_ffn"]["status"] == "done"` 啟動後 30s 內 | TBD |
| P3-3 | `prewarm.status()["fundmgr.form_choices_cache"]["status"] == "done"` | TBD |
| P3-4 | 首次 `portfolio()` 請求 log `form choices built` < 1.0 s | TBD |
| P3-5 | 快取有效（第二次請求 form choices < 0.1 s）| TBD |

---

## Phase 4 - Broker SDK / Quote Service Import

| # | 查核項目 | 狀態 |
|---|---|---|
| P4-1 | `finfun-broker-sino/__init__.py` 無模組層級 `import shioaji` | TBD |
| P4-2 | `finfun-broker-yuanta/__init__.py` 無模組層級 `from YuantaOneAPI import` | TBD |
| P4-3 | `prewarm.status()["broker_sino.shioaji_import"]["status"] == "done"` 啟動後 120s | TBD |
| P4-4 | 首次下單/報價操作 log 無 45+ s broker SDK import 延遲 | TBD |
| P4-5 | `QuoteService.register_prewarm_tasks()` 已登記相關任務 | TBD |

---

## Phase 5 - quantanlys numpy/pandas/scipy 延遲 import

| # | 查核項目 | 狀態 |
|---|---|---|
| P5-1 | `quanteval.py` 無模組層級 `import numpy`、`import pandas`、`from scipy` | TBD |
| P5-2 | `python -c "import time; t=time.time(); from finfun.quantanlys import quanteval; print(time.time()-t)"` < 0.5 s | TBD |
| P5-3 | quantanlys 現有測試不衰退 | TBD |

---

## Phase 6 - 清理收斂

| # | 查核項目 | 狀態 |
|---|---|---|
| P6-1 | `grep -r "_init_calendar_worker" finfun-fundmgr/` 空 | TBD |
| P6-2 | `grep -r "^import shioaji" finfun-broker-sino/` 空 | TBD |
| P6-3 | `grep -r "^from YuantaOneAPI" finfun-broker-yuanta/` 空 | TBD |
| P6-4 | `prewarm.status()` 所有任務 `done` 或 `failed`（無 `pending`）在啟動後 120s | TBD |
| P6-5 | 系統冷啟動首次請求 end-to-end 延遲 < 5 s | TBD |
| P6-6 | `/health` endpoint 含 prewarm 狀態 | TBD |

**端對端量測：**
```powershell
# 啟動 app 後，計時首次請求
Measure-Command { Invoke-WebRequest -Uri "http://127.0.0.1:5000/fundmgr/portfolio" -UseBasicParsing }
```

---

## 回退標準

若任何 Phase 導致以下狀況，立即回退（`git revert` 或切回 main）：

1. **App 啟動失敗**（非 prewarm 任務失敗，而是 app crash）
2. **測試衰退**：非 prewarm 相關測試從 PASS 變 FAIL
3. **記憶體大幅增加** > 200 MB（相較 baseline）

---

## 觀測工具

### 快速狀態檢查
```python
import funlab.core.prewarm as pw, json, time
time.sleep(5)
print(json.dumps(pw.status(), indent=2))
```

### 預熱進度 log pattern
```
Prewarm [START  ] 'task_name'
Prewarm [DONE   ] 'task_name'  elapsed=12.345s
Prewarm [FAILED ] 'task_name'  elapsed=3.210s  error=...
```

### 測試指令
```powershell
# prewarm 單元測試
pytest tests/test_prewarm.py -v --tb=short

# 全部測試（regression）
pytest tests/ -v

# 含 coverage
pytest tests/test_prewarm.py --cov=funlab.core.prewarm --cov-report=term-missing
```
