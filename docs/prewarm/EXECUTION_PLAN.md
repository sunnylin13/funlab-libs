# Prewarm Framework — Plugin Migration Execution Plan (v2)

> **Branch**: `feature/prewarm-framework`
> **Updated**: 2026-03-13 (v2 — 移除 `_lazy` 封裝層，回歸標準 Python import)
> **Owner**: Platform / Core team
> **參考**: [IMPORT_BEST_PRACTICES.md](IMPORT_BEST_PRACTICES.md) | [README.md](README.md) | [CHECKPOINTS.md](CHECKPOINTS.md)

---

## 設計變更說明（v1 → v2）

### 移除的內容
- **`_lazy()` + `@functools.cache` 工廠**：與 Python 內建 `sys.modules` 快取重疊，增加維護成本
- **`_get_xxx()` 薄包裝函式**：無實質收益的間接層（`_get_pd()`, `_get_np()`, `_get_ffn()` 等）
- **在 view.py 中集中管理所有 lazy import 的模式**：每個模組自行管理 import，不需要中央指揮

### 保留的內容
- **`finfun.core.entity.__getattr__`**：14 個 entity 模組的 lazy loading，因 entity 相互依賴且啟動時需 ORM mapping，此機制有效避免級聯載入
- **`_get_twse_calendar()`**：含副作用初始化邏輯（calendar 註冊 + fallback 名稱），屬業務邏輯封裝而非 import 快取
- **`_get_broker_and_installed_brokers()`**：回傳 2-tuple，無法用單一 import 表達
- **`prewarm` 框架**：背景預熱重型模組，核心不變

### 新的策略
| 模組類型 | 策略 |
|---------|------|
| 輕量 (< 0.5s) | **Top-level import** — 標準 Python |
| 重型 (> 3s) 且在請求路徑上 | **Function-level import + prewarm 預熱** |
| 重型但僅任務 / 腳本使用 | **Function-level import**（不需 prewarm） |
| 有副作用初始化的 | 保留為**具名 helper** |

---

## 全系統重型模組掃描結果（2026-03-13 更新）

### 啟動路徑影響（影響首次 HTTP 請求）

| Plugin | 檔案 | 重型模組 | 載入位置 | 估計耗時 | Phase |
|--------|------|---------|---------|---------|-------|
| `finfun-core` | `utils/data_utils.py` | **pandas + numpy + scipy** | TL | ~5 s | **Phase 2A** |
| `finfun-core` | `utils/fin_loader.py` | **pandas**（+ 間接 data_utils） | TL | ~2 s | **Phase 2A** |
| `finfun-core` | `utils/financial/revenue_estimator.py` | **numpy + pandas + scipy** | TL | ~5 s | **Phase 2A** |
| `finfun-core` | `core/constants/market_indices.py` | **pandas + shioaji** | TL | ~5 s | **Phase 2B** |
| `finfun-fundmgr` | `fundmgr/utils.py` | `from finfun.utils.data_utils import …` | TL | 級聯 ~5 s | **Phase 2A** |
| `finfun-fundmgr` | `fundmgr/ffn_ext.py` | **ffn + pandas + numpy + scipy** | TL | ~8 s | **Phase 3** |
| `finfun-fundmgr` | `fundmgr/view.py` | `_lazy()` 封裝層（待移除） | 首次呼叫 | — | **Phase 3** |
| `finfun-broker-sino` | `__init__.py` + 3 files | **shioaji** (C-ext) | TL | ~4.5 s | **Phase 4** |
| `finfun-broker-yuanta` | `__init__.py` + 4 files | **YuantaOneAPI** (.NET CLR) | TL | ~3 s | **Phase 4** |
| `finfun-broker-capital` | `__init__.py` | **comtypes** (COM DLL) | TL | 待測 | **Phase 4** |
| `finfun-broker-fubon` | `__init__.py` | **fubon_neo SDK** | TL | 待測 | **Phase 4** |
| `finfun-ttif` | `quoteapi.py` | **shioaji** | TL | ~4 s | **Phase 4** |
| `finfun-quotesvcs` | `service.py` | shioaji + exchange_calendars | FL (already done) | — | ✅ |
| `finfun-quantanlys` | `quanteval.py`, `v2/*` | **numpy + pandas + scipy** | TL | ~5 s | **Phase 5** |

### 非啟動路徑（僅任務 / 腳本 / 已處理）

| Plugin | 檔案 | 重型模組 | 現況 | 建議 |
|--------|------|---------|------|------|
| `finfun-finfetch` | `runner.py`, `spiders/*.py` | scrapy + exchange_calendars | `task.py` 已 FL | 維持現狀 |
| `finfun-hedge` | `__init__.py` 級聯 | pandas + numpy + shioaji + scipy | TL | **Phase 5** 一併處理 |
| `funlab-auth` | `view.py` | authlib (0.5 s) | TL | **可選** — 列為 Phase 6 |
| `funlab-sched` | `service.py` | apscheduler (~1 s) | TL | **可選** — sched 啟動必需，可接受 TL |
| `finfun-core` | `utils/fin_cale.py` | exchange_calendars | FL guard | ✅ 已完成 |
| `finfun-core` | `core/entity/__init__.py` | 14 個 entity 模組 | `__getattr__` lazy | ✅ 保留 |

---

## Phase 0 ✅ — 框架建置（`funlab-libs`）

**狀態**：已完成 / commit `86b073a`

- [x] `funlab/core/prewarm.py` 實作完成
- [x] 公開 API: `register()`, `unregister()`, `run()`, `status()`, `reset()`
- [x] `deferred_import()` 裝飾器 + `prewarm_task` 別名
- [x] `_FlaskBase._run_prewarm()` 呼叫 `prewarm.run(app=self)`
- [x] `EnhancedViewPlugin.register_prewarm_tasks()` Template Method
- [x] `tests/test_prewarm.py` 46 tests, 0 failures

---

## Phase 1 ✅ — TWSE Calendar 預熱

**狀態**：已完成 / finfun-core `3bfd24c`, finfun-fundmgr `c487997`

在 `FundMgrView.register_prewarm_tasks()` 中以 prewarm 登記 `_ensure_calendar_registered()`，
消除首次 `/fundmgr/portfolio` 的 50–83 s 延遲。

---

## Phase 2A — `finfun-core` utils：pandas/numpy/scipy 移出 top-level

**狀態**：🔲 待實作
**目標**：`data_utils.py`, `fin_loader.py`, `revenue_estimator.py`
**預期效益**：消除 `import finfun.core.utils.data_utils` 時觸發的 ~5 s 級聯 import

### 方案

將 `numpy`, `pandas`, `scipy.stats` 從 top-level 移至各函式內部 import。
這些模組將由 prewarm 預熱到 `sys.modules`，function-level import 僅是查表操作。

**`data_utils.py` 改動示意**：
```python
# 改動前（top-level）
import numpy as np
import pandas as pd
from scipy import stats

# 改動後（function-level）
# 移除 top-level import numpy/pandas/scipy

def find_missing_periods(entities, fromdt, todt, freq):
    import pandas as pd   # sys.modules cache hit
    # ...

def calc_something(data):
    import numpy as np
    from scipy import stats
    # ...
```

**`fin_loader.py`**：移除 top-level `import pandas as pd`，在各函式內 import。

**`revenue_estimator.py`**：同上模式。

### Prewarm 登記

由 `FundMgrView.register_prewarm_tasks()` 負責預熱（作為最大消費者）：

```python
def register_prewarm_tasks(self):
    import funlab.core.prewarm as pw

    # Phase 2A: 預熱 pandas/numpy/scipy — 確保 sys.modules 有快取
    def _warmup_scientific_stack():
        import pandas    # noqa: F401
        import numpy     # noqa: F401
        from scipy import stats  # noqa: F401

    pw.register(
        "fundmgr.scientific_stack",
        _warmup_scientific_stack,
        blocking=False,
        delay=3.0,
        skip_if_exists=True,
    )
```

### 查核點
- [ ] `data_utils.py`, `fin_loader.py`, `revenue_estimator.py` 無 top-level `import pandas/numpy/scipy`
- [ ] `python -c "import time; t=time.time(); from finfun.utils import data_utils; print(f'{time.time()-t:.3f}s')"` < 0.5 s
- [ ] 所有現有測試不衰退
- [ ] `prewarm.status()["fundmgr.scientific_stack"]["status"] == "done"` 啟動後 30 s 內

---

## Phase 2B — `finfun-core` constants：market_indices.py

**狀態**：🔲 待實作
**目標**：`finfun-core/finfun/core/constants/market_indices.py`
**問題**：top-level `import pandas` + `import shioaji`

### 方案
移除 top-level `import pandas` 及 `import shioaji`，改為各使用函式內 import。

### 查核點
- [ ] `market_indices.py` 無 top-level `import pandas` 或 `import shioaji`
- [ ] 相關測試不衰退

---

## Phase 3 — `finfun-fundmgr/view.py` 重構：移除 `_lazy` 封裝

**狀態**：🔲 待實作
**目標**：消除 `view.py` 中所有自訂 import 機制，回歸標準 Python
**預期效益**：~50 行程式碼刪除 + 維護成本降低 + IDE 型別推導恢復

### 3.1 刪除 `_lazy()` 工廠與相關基礎設施

移除以下程式碼區塊（約 L28–55）：
```python
# 刪除整個區塊：
from functools import cache as _cache
from importlib import import_module as _import_module

@_cache
def _lazy(module_path: str, attr: str | None = None):
    ...
```

### 3.2 保留的 helper 函式

以下函式**保留**但重構為使用標準 import：

```python
def _get_twse_calendar():
    """業務邏輯：TWSE calendar 註冊 + fallback 名稱解析"""
    import exchange_calendars as xcals
    try:
        from finfun.utils.fin_cale import _ensure_calendar_registered
        _ensure_calendar_registered()
    except Exception:
        pass
    for cal_name in ('MYXTAI', 'XTAI'):
        try:
            return xcals.get_calendar(cal_name)
        except Exception:
            continue
    raise RuntimeError('TWSE calendar unavailable (MYXTAI/XTAI)')

def _get_broker_and_installed_brokers():
    """回傳 2-tuple：(Broker, get_installed_brokers)"""
    from finfun.core import Broker, get_installed_brokers
    return Broker, get_installed_brokers

def get_returnindex_choices():
    from finfun.core.constants import ReturnIndex
    return [(idx.symbol, idx.name) for idx in ReturnIndex]
```

### 3.3 呼叫點遷移對照表

view.py 中所有 `_lazy(...)` 呼叫改為標準 import：

| 原始呼叫 | 替換為 |
|---------|--------|
| `pd = _lazy('pandas')` | `import pandas as pd` |
| `np = _lazy('numpy')` | `import numpy as np` |
| `ffn = _lazy('ffn')` | `import ffn` |
| `_lazy('finfun.utils.fin_loader')` | `from finfun.utils import fin_loader` |
| `_lazy('finfun.ttif.dataclass.product')` | `from finfun.ttif.dataclass import product` |
| `_lazy('finfun.ttif', 'AccountType')` | `from finfun.ttif import AccountType` |
| `_lazy('finfun.core.entity.account')` | `from finfun.core.entity import account` |
| `_lazy('finfun.core.entity', 'entities_registry')` | `from finfun.core.entity import entities_registry` |
| `_lazy('finfun.fundmgr.utils')` | `from finfun.fundmgr import utils as fundmgr_utils` |
| `_lazy('funlab.auth.utils', 'load_user')` | `from funlab.auth.utils import load_user` |
| `_lazy('funlab.utils.db', 'upsert_entity')` | `from funlab.utils.db import upsert_entity` |
| `_lazy('finfun.core.constants', 'ReturnIndex')` | `from finfun.core.constants import ReturnIndex` |
| `_lazy('funlab.core.jinja_filters', 'common_formatter')` | `from funlab.core.jinja_filters import common_formatter` |
| `_lazy('funlab.auth.user', 'UserEntity')` | `from funlab.auth.user import UserEntity` |
| `_lazy('funlab.utils.dtts')` | `from funlab.utils import dtts` |
| `_lazy('finfun.core.entity.manager', 'ManagerEntity')` | `from finfun.core.entity.manager import ManagerEntity` |
| `_lazy('finfun.core.entity.revenue', 'RevenueEntity')` | `from finfun.core.entity.revenue import RevenueEntity` |
| `_lazy('finfun.core.entity.finquant', 'StockQuantValueEntity')` | `from finfun.core.entity.finquant import StockQuantValueEntity` |

**注意**：以上 import 全部放在**使用處的函式內部**（function-level），
而非搬回 top-level。因為這些模組可能間接依賴 pandas/numpy 等重型模組。

### 3.4 `ffn_ext.py` 重構

移除 top-level `import ffn`, `pandas`, `numpy`, `scipy`，改為各函式內 import。

### 3.5 `utils.py` (fundmgr) 重構

移除 top-level `from finfun.utils.data_utils import ...`（觸發 pandas+numpy+scipy 級聯），
改為各函式內 import。

### 3.6 Prewarm 登記（整合 Phase 2A）

```python
class FundMgrView(EnhancedViewPlugin):
    def register_prewarm_tasks(self):
        import funlab.core.prewarm as pw

        # TWSE calendar（Phase 1，已完成）
        def _warmup_twse_calendar():
            from finfun.utils.fin_cale import _ensure_calendar_registered
            _ensure_calendar_registered()

        pw.register(
            "finfun_core.twse_calendar",
            _warmup_twse_calendar,
            blocking=False,
            delay=2.0,
            skip_if_exists=True,
        )

        # Scientific stack（Phase 2A/3）
        def _warmup_scientific_stack():
            import pandas    # noqa: F401
            import numpy     # noqa: F401
            import ffn        # noqa: F401
            from scipy import stats  # noqa: F401

        pw.register(
            "fundmgr.scientific_stack",
            _warmup_scientific_stack,
            blocking=False,
            delay=5.0,
            skip_if_exists=True,
        )
```

### 查核點
- [ ] `view.py` 無 `_lazy` 函式定義、無 `_cache` / `_import_module` import
- [ ] `view.py` 無 `_get_pd`, `_get_np`, `_get_ffn` 等薄包裝
- [ ] `_get_twse_calendar()`, `_get_broker_and_installed_brokers()` 使用標準 `import` 語法
- [ ] `ffn_ext.py` 無 top-level `import ffn/pandas/numpy/scipy`
- [ ] `fundmgr/utils.py` 無 top-level `from finfun.utils.data_utils import ...`
- [ ] 所有功能測試通過（portfolio, benchmark, ffn_stats, revenue, quantvalue 頁面）
- [ ] `prewarm.status()` 顯示 `scientific_stack` 為 `done`

---

## Phase 4 — Broker SDK lazy import + prewarm

**狀態**：🔲 待實作
**目標**：`finfun-broker-sino`, `finfun-broker-yuanta`, `finfun-broker-capital`, `finfun-broker-fubon`, `finfun-ttif/quoteapi.py`
**預期效益**：消除 broker SDK top-level import ~4–5 s × 4 brokers

### 方案

每個 broker 的 `__init__.py` 將 SDK import 改為 function-level：

**`finfun-broker-sino/__init__.py`**：
```python
# 改動前
import shioaji as sj                    # L5 — 3-5s C-extension
from shioaji.constant import ...        # L13-15

# 改動後：移除所有 top-level shioaji import
def _get_shioaji():
    """Lazy shioaji accessor — 首次呼叫 import，後續 sys.modules cache hit"""
    import shioaji as sj
    return sj
```

> **備註**：broker `__init__.py` 中 `sj` 被當作模組級 constant 多處引用，
> 此處保留 `_get_shioaji()` helper 是合理的（封裝「模組公用 SDK 物件」）。

**prewarm 登記**（在 quotesvcs 中）：
```python
class QuoteService(EnhancedServicePlugin):
    def register_prewarm_tasks(self):
        import funlab.core.prewarm as pw

        def _warmup_broker_sdks():
            try:
                import shioaji          # noqa: F401
            except ImportError:
                pass

        pw.register(
            "quotesvcs.broker_sdks",
            _warmup_broker_sdks,
            blocking=False,
            delay=10.0,
            skip_if_exists=True,
        )
```

### 各 broker 改動範圍

| Broker | 需改動的檔案 | Top-level import 數量 |
|--------|-------------|---------------------|
| sino | `__init__.py`, `dialet.py`, `utif/sino_trading_broker.py`, `utif/converters.py` | 6 |
| yuanta | `__init__.py`, `function.py`, `apiproxy.py`, `apiproxy2.py`, `dialet.py` | ~15 |
| capital | `__init__.py` | 1 |
| fubon | `__init__.py` | 1 |
| ttif | `quoteapi.py` | 2 |

### 查核點
- [ ] `grep -rn "^import shioaji" finfun-broker-sino/` 無結果
- [ ] `grep -rn "^from YuantaOneAPI" finfun-broker-yuanta/` 無結果
- [ ] `python -c "import time; t=time.time(); from finfun.broker_sino import *; print(f'{time.time()-t:.3f}s')"` < 0.5 s
- [ ] broker 交易 / 報價功能測試通過

---

## Phase 5 — `finfun-quantanlys` + `finfun-hedge`：scientific imports

**狀態**：🔲 待實作
**目標**：`quanteval.py`, `v2/calculators/base_calculator.py`, `v2/outputs/db_output.py`, `v2/loaders/financial_loader.py`, `finfun-hedge/__init__.py`

### 方案

同 Phase 2A 模式 — 將 `numpy`, `pandas`, `scipy` 從 top-level 移至 function-level。

**`finfun-hedge/__init__.py`**：
將子模組匯入改為 `__getattr__` lazy pattern（類似 entity `__init__.py`），
避免級聯觸發 pandas + numpy + shioaji + scipy。

### 查核點
- [ ] `python -c "import time; t=time.time(); from finfun.quantanlys import quanteval; print(f'{time.time()-t:.3f}s')"` < 0.5 s
- [ ] quantanlys / hedge 現有測試不衰退

---

## Phase 6 — 清理收斂與文件

**狀態**：🔲 待實作

### 任務
1. 確認所有舊的手動 background thread 已移除
2. `prewarm.status()` 所有任務 `done` 或 `failed`（啟動後 120 s 內無 `pending`）
3. `/health` endpoint 加入 prewarm 狀態回報
4. 更新 `PLUGIN_DEVELOPMENT_GUIDE.md` 加入 import 最佳實踐段落
5. **可選**：`funlab-auth/view.py` authlib (0.5 s)
6. **可選**：`funlab-sched/service.py` apscheduler (~1 s)

### 查核點
- [ ] `view.py` 無 `_lazy`, `_cache`, `_import_module` 基礎設施
- [ ] 各 broker `__init__.py` 無 heavy top-level import
- [ ] 系統冷啟動首次請求 end-to-end 延遲 < 5 s
- [ ] 所有文件更新完成

---

## 執行順序與依賴關係

```
Phase 0 ✅  框架建置
Phase 1 ✅  TWSE Calendar prewarm
    │
    ├── Phase 2A  finfun-core utils (pandas/numpy/scipy FL)
    │       │
    │       └── Phase 3  fundmgr view.py 重構 (移除 _lazy, FL import)
    │               │
    │               └── Phase 3 含 ffn_ext.py + fundmgr/utils.py
    │
    ├── Phase 2B  market_indices.py (pandas/shioaji FL)
    │
    ├── Phase 4  Broker SDK lazy import (可與 2A/3 並行)
    │
    ├── Phase 5  quantanlys + hedge (可與 4 並行)
    │
    └── Phase 6  清理收斂（所有前序 Phase 完成後）
```

**關鍵依賴**：Phase 3（view.py 重構）依賴 Phase 2A（data_utils/fin_loader FL 化），
因為 view.py 會間接 import 這些模組。

---

## 回退標準

若任何 Phase 導致以下狀況，立即 `git revert`：

1. **App 啟動失敗**（非 prewarm 任務失敗）
2. **測試衰退**：非 prewarm 相關測試 PASS → FAIL
3. **記憶體增加 > 200 MB**
