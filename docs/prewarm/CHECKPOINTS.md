# Prewarm Framework - Checkpoints & Validation (v2)

> 每個 Phase 完成後，必須通過本文件中對應的查核點，才可合入 main 或推進下一 Phase。
> **v2 (2026-03-13)**：對齊新策略 — 移除 `_lazy` 封裝，回歸標準 Python import + prewarm。

---

## 通用查核標準（全 Phase 適用）

| # | 查核項目 | 命令 / 方法 |
|---|---|---|
| G-1 | 所有現有測試不衰退 | `pytest funlab-libs/tests/ -v` |
| G-2 | prewarm 測試 100% 通過 | `pytest funlab-libs/tests/test_prewarm.py -v` |
| G-3 | 型別標注無明顯錯誤 | `mypy funlab/core/prewarm.py --ignore-missing-imports` |
| G-4 | 程式碼無 import cycle | `python -c "import funlab.core.prewarm"` |

---

## Phase 0 ✅ — 框架建置

| # | 查核項目 | 狀態 |
|---|---|---|
| P0-1 | `funlab/core/prewarm.py` 存在且可 import | ✅ |
| P0-2 | `prewarm.register(name, func)` 不拋錯 | ✅ |
| P0-3 | `prewarm.run()` blocking=True 同步執行 | ✅ |
| P0-4 | `prewarm.run()` blocking=False daemon Thread | ✅ |
| P0-5 | 任務失敗 status == "failed"，不影響其他 | ✅ |
| P0-6 | `run()` 二次呼叫 no-op | ✅ |
| P0-7 | `_FlaskBase._run_prewarm()` 整合 | ✅ |
| P0-8 | `skip_if_exists=True` 防重複 | ✅ |
| P0-9 | `tests/test_prewarm.py` 46/46 通過 | ✅ |

---

## Phase 1 ✅ — TWSE Calendar Prewarm

| # | 查核項目 | 狀態 |
|---|---|---|
| P1-1 | `register_prewarm_tasks()` 登記 `finfun_core.twse_calendar` | ✅ |
| P1-2 | 啟動 120s 內 `status == "done"` | 🔲 待執行驗證 |
| P1-3 | 首次 `/fundmgr/portfolio` calendar init < 1.0 s | 🔲 待執行驗證 |
| P1-4 | 舊 `_init_calendar_worker` thread 已移除 | ✅ |

---

## Phase 2A — `finfun-core` utils FL 化

| # | 查核項目 | 狀態 |
|---|---|---|
| P2A-1 | `data_utils.py` 無 TL `import pandas/numpy/scipy` | ✅ |
| P2A-2 | `fin_loader.py` 無 TL `import pandas` | ✅ |
| P2A-3 | `revenue_estimator.py` 無 TL `import numpy/pandas/scipy` | ✅ |
| P2A-4 | `import finfun.utils.data_utils` 耗時 < 0.5 s | ✅ |
| P2A-5 | 所有現有測試不衰退 | 🔲 |

**驗證指令**：
```powershell
python -c "import time; t=time.time(); from finfun.utils import data_utils; print(f'{time.time()-t:.3f}s')"
Select-String -Path "finfun-core/finfun/utils/data_utils.py" -Pattern "^import (numpy|pandas)|^from scipy"
```

---

## Phase 2B — `market_indices.py` FL 化

| # | 查核項目 | 狀態 |
|---|---|---|
| P2B-1 | `market_indices.py` 無 TL `import pandas` | ✅ |
| P2B-2 | `market_indices.py` 無 TL `import shioaji` | ✅ |
| P2B-3 | 相關測試不衰退 | 🔲 |

---

## Phase 3 — fundmgr `view.py` 重構

| # | 查核項目 | 狀態 |
|---|---|---|
| P3-1 | `view.py` 無 `_lazy` 函式定義 | ✅ |
| P3-2 | `view.py` 無 `from functools import cache as _cache` | ✅ |
| P3-3 | `view.py` 無 `from importlib import import_module as _import_module` | ✅ |
| P3-4 | `view.py` 無 `_get_pd`, `_get_np`, `_get_ffn` 等薄包裝 | ✅ (v1 已移除) |
| P3-5 | `_get_twse_calendar()` 使用標準 `import` (非 `_lazy`) | ✅ |
| P3-6 | `_get_broker_and_installed_brokers()` 使用標準 `import` | ✅ |
| P3-7 | `ffn_ext.py` 無 TL `import ffn/pandas/numpy/scipy` | ✅ |
| P3-8 | `fundmgr/utils.py` 無 TL `from finfun.utils.data_utils import` | ✅ |
| P3-9 | `prewarm.status()["fundmgr.scientific_stack"]` == "done" | 🔲 |
| P3-10 | portfolio/benchmark/ffn_stats/revenue/quantvalue 頁面功能正常 | 🔲 |

**驗證指令**：
```powershell
# 確認 _lazy 基礎設施已移除
Select-String -Path "finfun-fundmgr/finfun/fundmgr/view.py" -Pattern "_lazy|_cache|_import_module"

# 確認 ffn_ext.py 無 heavy TL import
Select-String -Path "finfun-fundmgr/finfun/fundmgr/ffn_ext.py" -Pattern "^import (ffn|pandas|numpy)|^from scipy"
```

---

## Phase 4 — Broker SDK lazy import

| # | 查核項目 | 狀態 |
|---|---|---|
| P4-1 | `finfun-broker-sino/__init__.py` 無 TL `import shioaji` | ✅ |
| P4-2 | `finfun-broker-yuanta/__init__.py` 無 TL `from YuantaOneAPI` | ✅ |
| P4-3 | `finfun-broker-capital/__init__.py` 無 TL `comtypes` | ✅ |
| P4-4 | `finfun-broker-fubon/__init__.py` 無 TL `fubon_neo` | ✅ |
| P4-5 | `finfun-ttif/quoteapi.py` 無 TL `import shioaji` | ✅ |
| P4-6 | broker import 耗時 < 0.5 s | ✅ |
| P4-7 | broker 交易/報價功能測試通過 | 🔲 |

**驗證指令**：
```powershell
Select-String -Path "finfun-broker-sino/finfun/broker_sino/__init__.py" -Pattern "^import shioaji"
python -c "import time; t=time.time(); from finfun.broker_sino import *; print(f'{time.time()-t:.3f}s')"
```

---

## Phase 5 — quantanlys + hedge FL 化

| # | 查核項目 | 狀態 |
|---|---|---|
| P5-1 | `quanteval.py` 無 TL `import numpy/pandas/scipy` | ✅ |
| P5-2 | `v2/calculators/base_calculator.py` 無 TL heavy import | ✅ |
| P5-3 | `finfun-hedge/__init__.py` 不級聯觸發 heavy import | ✅ |
| P5-4 | `import finfun.quantanlys.quanteval` 耗時 < 0.5 s | ✅ |
| P5-5 | quantanlys / hedge 測試不衰退 | 🔲 |

---

## Phase 6 — 清理收斂

| # | 查核項目 | 狀態 |
|---|---|---|
| P6-1 | `view.py` 完全無 `_lazy`/`_cache`/`_import_module` | ✅ |
| P6-2 | 各 broker 無 heavy TL import | ✅ |
| P6-3 | `prewarm.status()` 所有任務 done/failed（120s 內無 pending）| 🔲 |
| P6-4 | 系統冷啟動首次請求 < 5 s | 🔲 |
| P6-5 | `/health` 含 prewarm 狀態 | ✅ (200 OK, 6 plugins healthy, prewarm running) |
| P6-6 | `PLUGIN_DEVELOPMENT_GUIDE.md` 含 import 最佳實踐 | ✅ |

**端對端量測**：
```powershell
Measure-Command { Invoke-WebRequest -Uri "http://127.0.0.1:5000/fundmgr/portfolio" -UseBasicParsing }
```

---

## 回退標準

若任何 Phase 導致以下狀況，立即 `git revert`：

1. **App 啟動失敗**（非 prewarm 任務失敗）
2. **測試衰退**：非 prewarm 相關測試 PASS → FAIL
3. **記憶體增加 > 200 MB**
