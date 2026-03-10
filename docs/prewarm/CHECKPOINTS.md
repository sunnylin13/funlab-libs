# Prewarm Framework – Checkpoints & Validation

> 每個 Phase 完成後，必須通過本文件中對應的查核點後，才可合入 main 或推進下一 Phase。

---

## 通用查核標準（全 Phase 適用）

| # | 查核項目 | 命令 / 方法 |
|---|---|---|
| G-1 | 所有現有測試不衰退 | `pytest funlab-libs/tests/ -v` |
| G-2 | prewarm 測試 100% 通過 | `pytest funlab-libs/tests/test_prewarm.py -v` |
| G-3 | 型別標注無明顯錯誤 | `mypy funlab/core/prewarm.py --ignore-missing-imports` |
| G-4 | 程式碼無 import cycle | `python -c "from funlab.core.prewarm import prewarm_registry"` |
| G-5 | `PREWARM_ENABLED=False` 可正常停用 | 見 `test_prewarm_disabled` |

---

## Phase 0 – 框架建置

| # | 查核項目 | 狀態 |
|---|---|---|
| P0-1 | `funlab/core/prewarm.py` 存在且可 import | ✅ |
| P0-2 | `prewarm_registry.register()` 不拋錯 | ✅ |
| P0-3 | `prewarm_manager.run(background=False)` 同步執行全部任務 | ✅ |
| P0-4 | CRITICAL task 在 background=True 時自動降為 blocking | ✅ |
| P0-5 | Timeout 超時任務 status == "timeout" | ✅ |
| P0-6 | `depends_on` 依賴任務失敗時，下游任務 status == "skipped" | ✅ |
| P0-7 | `_FlaskBase._run_prewarm()` 已加入 appbase.py | ✅ |
| P0-8 | `funlab/core/__init__.__getattr__` 可懶載 prewarm 匯出 | ✅ |
| P0-9 | docs/prewarm/ 目錄與三份文件已建立 | ✅ |
| P0-10 | `tests/test_prewarm.py` 全數通過 | ✅ 48/48 |

**執行驗證：**
```bash
cd d:\08.dev\fundlife\funlab-libs
python -m pytest tests/test_prewarm.py -v --tb=short
```

---

## Phase 1 – TWSE Calendar Prewarm

| # | 查核項目 | 狀態 |
|---|---|---|
| P1-1 | `register_prewarm("twse_calendar", ...)` 在 finfun-core 中登記 | 🔲 |
| P1-2 | 啟動後 120 s 內 log 含 `Prewarm [SUCCESS] 'twse_calendar'` | 🔲 |
| P1-3 | 首次 `/fundmgr/portfolio` log 中 `twse calendar init took` < 1.0 s | 🔲 |
| P1-4 | 舊 `_init_calendar_worker` thread 已從 fundmgr/view.py 移除 | 🔲 |
| P1-5 | 整合測試 `test_twse_calendar_prewarm` 通過 | 🔲 |

**手動驗證方式：**
```python
# 啟動 app 後，在 Flask shell 執行
from funlab.core.prewarm import prewarm_manager
import time; time.sleep(30)  # 等待預熱完成
assert prewarm_manager.status()["twse_calendar"]["status"] == "success"
```

---

## Phase 2 – DB Engine Warm-up

| # | 查核項目 | 狀態 |
|---|---|---|
| P2-1 | `register_prewarm("db_engine_warmup", ...)` 已登記 | 🔲 |
| P2-2 | log 含 `Prewarm [SUCCESS] 'db_engine_warmup'` | 🔲 |
| P2-3 | 首次 DB query 後 log 無 `create engine` 類初始化訊息 | 🔲 |
| P2-4 | 接受 `app` 參數的任務可正常被 `_resolve_func_call` 呼叫 | 🔲 |

---

## Phase 3 – FundMgr Form Choices Cache

| # | 查核項目 | 狀態 |
|---|---|---|
| P3-1 | `register_prewarm("fundmgr_form_choices", depends_on=["db_engine_warmup"])` 已登記 | 🔲 |
| P3-2 | 首次 `portfolio()` 請求 `form choices built` log < 1.0 s | 🔲 |
| P3-3 | 快取 TTL 邏輯存在（>5 min 後自動刷新） | 🔲 |
| P3-4 | 整合測試 `test_fundmgr_form_choices_cache` 通過 | 🔲 |

---

## Phase 4 – Quote Agent Import Prewarm

| # | 查核項目 | 狀態 |
|---|---|---|
| P4-1 | `register_prewarm("quote_agent_import", ...)` 已登記 | 🔲 |
| P4-2 | 首次 portfolio 請求無 45+ s QuoteService 懶載 log | 🔲 |
| P4-3 | `prewarm_manager.status()["quote_agent_import"]["status"] == "success"` | 🔲 |

---

## Phase 6 – 清理收斂

| # | 查核項目 | 狀態 |
|---|---|---|
| P6-1 | `grep -r '_init_calendar_worker' finfun-fundmgr/` 回傳空 | 🔲 |
| P6-2 | `grep -r 'Thread.*warm\|Thread.*calendar\|Thread.*quote' finfun-*/` 回傳空（或只有 prewarm 框架本身）| 🔲 |
| P6-3 | `prewarm_manager.status()` 所有 status 為 `success` 或 `skipped` | 🔲 |
| P6-4 | 系統冷啟動首次請求延遲（端對端測量）< 5 s | 🔲 |
| P6-5 | `docs/PLUGIN_DEVELOPMENT_GUIDE.md` 已加入 prewarm 段落 | 🔲 |
| P6-6 | CI pipeline 含 prewarm integrity test | 🔲 |

**端對端延遲量測方式：**
```bash
# 停止服務，清除所有 Python bytecode，重新啟動
find . -name "*.pyc" -delete
# 啟動 app（計時）
time python finfun/run.py &
sleep 5
# 發送首次請求（計時）
curl -w "@curl-format.txt" -o /dev/null -s http://127.0.0.1:5000/fundmgr/portfolio
```

---

## 回退標準

若任何 Phase 導致以下情況，必須立即回退（`git revert` 或切回 main）：

1. **啟動失敗**：app 無法啟動（非 prewarm 任務失敗，而是 app crash）
2. **CRITICAL task timeout 並阻塞啟動**：超過 `PREWARM_CRITICAL_MAX_WAIT`（預設 120 s）
3. **測試衰退**：非 prewarm 相關測試從 PASS 變 FAIL
4. **記憶體大幅增加**（> 200 MB 相較 baseline）

---

## 觀測工具

### 快速狀態檢查（Flask shell）
```python
from funlab.core.prewarm import prewarm_manager
import json
print(json.dumps(prewarm_manager.status(), indent=2))
```

### 預熱進度 log pattern
```
Prewarm [START  ] 'task_name'
Prewarm [SUCCESS] 'task_name'  elapsed=12.345s
Prewarm [FAILED ] 'task_name'  elapsed=3.210s   ← exception message
Prewarm [TIMEOUT] 'task_name'  elapsed=60.001s
Prewarm [SKIPPED] 'task_name'  elapsed=0.000s   ← dependency failed
```

### 測試執行
```bash
# 僅 prewarm 相關測試
pytest tests/test_prewarm.py -v --tb=short

# 全部測試（包含 regression）
pytest tests/ -v

# 含 coverage 報告
pytest tests/test_prewarm.py --cov=funlab.core.prewarm --cov-report=term-missing
```
