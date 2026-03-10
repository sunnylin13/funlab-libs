# Prewarm Framework - Plugin Migration Execution Plan

> **Branch**: `feature/prewarm-framework`
> **Updated**: 2026-03-10
> **Owner**: Platform / Core team

---

## 概要

本計畫以「增量、可回退」原則，逐步將各 plugin 的高成本初始化移入 `funlab.core.prewarm` 框架。
每個 Phase 結束後均需通過 [CHECKPOINTS.md](CHECKPOINTS.md) 驗證後才推進下一 Phase。

**參考 API**（見 README.md 2.1）：
```python
import funlab.core.prewarm as pw

pw.register(name, func, *, blocking=False, delay=0.0, skip_if_exists=False)
```

---

## Phase 0 OK - 框架建置（`funlab-libs`）

**狀態**：已完成 / 已 commit `86b073a`
**Branch**：`feature/prewarm-framework`

### 完成內容
- [x] 實作 `funlab/core/prewarm.py`（316 行；模組層級函式，無 Manager/Registry class）
- [x] 公開 API: `register()`, `unregister()`, `run()`, `status()`, `reset()`
- [x] `deferred_import()` 裝飾器 + `prewarm_task` 別名
- [x] `register_prewarm()` 便捷函式（backward compat，接受舊有 `priority`/`timeout`/`depends_on` 忽略之）
- [x] `blocking=True` 取代 `priority=CRITICAL`；`background=False` 映射至 `blocking=True`
- [x] `delay` 參數支援（錯開背景任務啟動時間）
- [x] `skip_if_exists` 防止多 plugin 重複登記
- [x] `_FlaskBase._run_prewarm()` 呼叫 `prewarm.run(app=self)`
- [x] `EnhancedViewPlugin.register_prewarm_tasks()` Template Method
- [x] `funlab/core/__init__.py` 更新 lazy 匯出
- [x] `tests/test_prewarm.py` 46 tests, 0 failures（新 API）
- [x] `docs/prewarm/` 文件更新

---

## 全系統重型模組掃描結果（2026-03-10）

| Plugin | Class / 繼承 | 重型模組/Pattern | 載入位置 | 首次耗時估計 | 優先 Phase |
|---|---|---|---|---|---|
| `finfun-core` |  (utils) | `exchange_calendars`（`fin_cale._ensure_calendar_registered()`）| 首次呼叫觸發 | 5083 s | **Phase 1** |
| `finfun-fundmgr` | `FundMgrView(EnhancedViewPlugin)` | TWSE calendar（`_init_calendar_worker` 手動 thread）| `__init__` 啟動 | 5083 s | Phase 1（遷移舊 thread）|
| `finfun-fundmgr` | `FundMgrView(EnhancedViewPlugin)` | `pandas`, `ffn`（`_get_pd()`, `_get_ffn()` lazy）| 首次 view 呼叫 | 38 s | **Phase 3** |
| `finfun-fundmgr` | `FundMgrView(EnhancedViewPlugin)` | Form choices（`load_all_managers_email`）| 首次 `portfolio()` 請求 | ~565 s（ORM init）| **Phase 3** |
| `finfun-broker-sino` |  (SDK) | `import shioaji as sj`（C 擴充，gRPC, protobuf）| `__init__.py` **模組層級** | 4562 s | **Phase 4** |
| `finfun-broker-yuanta` |  (SDK) | `from YuantaOneAPI import enumEnvironmentMode` | `__init__.py` **模組層級** | 2040 s | **Phase 4** |
| `finfun-broker-capital` |  (SDK) | Broker SDK（待確認）| `__init__.py` | 待測量 | Phase 4 |
| `finfun-broker-fubon` |  (SDK) | Broker SDK（待確認）| `__init__.py` | 待測量 | Phase 4 |
| `finfun-quotesvcs` | `QuoteService(EnhancedServicePlugin)` | `finfun.ttif.LoginState` + broker SDK（via plugin）| service.py 模組層級 | 1030 s | Phase 4 |
| `finfun-quantanlys` |  (no view/service plugin) | `numpy`, `pandas`, `scipy.stats`（`quanteval.py` 模組層級）| plugin import 時觸發 | 25 s | **Phase 5** |
| `finfun-core` |  (utils) | `numpy`, `pandas`, `scipy.stats`（`data_utils.py` 模組層級）| finfun-core import | 25 s | Phase 5 |
| `funlab-libs` |  (DbMgr) | SQLAlchemy engine + connection pool（`DbMgr.get_db_engine()`）| 首次 DB request | 13 s | **Phase 2** |
| `finfun-option` | `OptionView(EnhancedViewPlugin)` | 無重型 import（top-level 僅 flask/funlab 相關）|  | < 0.1 s |  已乾淨 |
| `funlab-auth` | `AuthPlugin(EnhancedSecurityPlugin)` | `authlib.integrations.flask_client`（OAuth）| view.py 模組層級 | 0.51 s | 可選 Phase 6 |
| `funlab-sched` | `SchedService(EnhancedServicePlugin)` | APScheduler（待確認）| service.py | 待測量 | Phase 6 |

---

## Phase 1 - `finfun-core` + `finfun-fundmgr`：TWSE Calendar 預熱

**狀態**： 待實作
**目標 Plugin**：`finfun-core`（登記任務）、`finfun-fundmgr`（移除舊 thread）
**預期效益**：消除首次 `/fundmgr/portfolio` TWSE calendar 初始化 5083 s 延遲

### 任務

**1. 在 `finfun-core` 登記任務**（建議放在 `finfun/utils/fin_cale.py` 底部，或獨立 `_prewarm.py`）：

```python
# finfun-core/finfun/utils/fin_cale.py（模組底部）
def _warmup_twse_calendar():
    """Pre-register TWSE exchange_calendars to populate sys.modules cache."""
    _ensure_calendar_registered()

try:
    from funlab.core import prewarm as _pw
    _pw.register(
        "finfun_core.twse_calendar",
        _warmup_twse_calendar,
        blocking=False,
        delay=2.0,           # 讓 DB engine warmup 先啟動
        skip_if_exists=True, # 防止多模組重複登記
    )
except ImportError:
    pass  # standalone usage without funlab-libs
```

**2. 移除 `finfun-fundmgr/finfun/fundmgr/view.py` 舊 thread**：

```python
# 移除這段（view.py 約 L125-L136）：
def _init_calendar_worker():
    ...
threading.Thread(target=_init_calendar_worker, ...).start()

# 改由 finfun-core 的 prewarm 任務處理，fundmgr 端不需再啟動 thread
```

### 查核點
- [ ] `prewarm.status()["finfun_core.twse_calendar"]["status"] == "done"` 啟動後 120s 內
- [ ] 首次請求 log `twse calendar init took` < 1.0 s
- [ ] `grep -r '_init_calendar_worker' finfun-fundmgr/` 回傳空

---

## Phase 2 - `funlab-libs`：SQLAlchemy DB Engine Warm-up

**狀態**： 待實作
**目標**：`funlab-libs`（`DbMgr` 或 app bootstrap 層）
**預期效益**：消除首次 DB request ORM engine 建立延遲（13 s）

### 任務

```python
# funlab-libs/funlab/core/appbase.py 或 dbmgr.py
def _warmup_db(app):
    from sqlalchemy import text
    engine = app.dbmgr.get_db_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

import funlab.core.prewarm as _pw
_pw.register(
    "funlab.db_engine_warmup",
    _warmup_db,       # run(app=...) 自動注入 app
    blocking=False,
    delay=0.0,
)
```

### 查核點
- [ ] `prewarm.status()["funlab.db_engine_warmup"]["status"] == "done"`
- [ ] 啟動後首次 DB query log 無 engine 建立訊息

---

## Phase 3 - `finfun-fundmgr`：pandas/ffn 預熱 + Form Choices 快取

**狀態**： 待實作
**目標 Plugin**：`FundMgrView(EnhancedViewPlugin)` in `finfun-fundmgr`
**預期效益**：消除首次 `portfolio()` 請求 373 s 綜合延遲

### 任務

在 `FundMgrView.register_prewarm_tasks()` 中登記：

```python
# finfun-fundmgr/finfun/fundmgr/view.py

class FundMgrView(EnhancedViewPlugin):

    def register_prewarm_tasks(self):
        import funlab.core.prewarm as pw

        # 3a. 預熱 pandas + ffn（避免首次 view 觸發 3-8s import）
        pw.register(
            "fundmgr.pandas_ffn",
            self._warmup_pandas_ffn,
            blocking=False,
            delay=5.0,
            skip_if_exists=True,
        )

        # 3b. Form choices 快取（需 DB 已就緒）
        pw.register(
            "fundmgr.form_choices_cache",
            self._warmup_form_choices,
            blocking=False,
            delay=10.0,  # 等 db_engine_warmup + ORM 完成
        )

    def _warmup_pandas_ffn(self):
        import pandas  # noqa: F401  populate sys.modules
        import ffn     # noqa: F401

    _managers_email_cache: list[str] = []

    def _warmup_form_choices(self, app=None):
        _app = app or self.app
        emails = _get_fundmgr_utils().load_all_managers_email(_app.dbmgr)
        self._managers_email_cache.clear()
        self._managers_email_cache.extend(emails)
```

在 `portfolio()` 路由中讀取快取：

```python
if current_user.role == 'supervisor':
    all_managers_email = (
        FundMgrView._managers_email_cache
        or _get_fundmgr_utils().load_all_managers_email(self.app.dbmgr)
    )
```

### 查核點
- [ ] `prewarm.status()["fundmgr.pandas_ffn"]["status"] == "done"` 啟動後 30s 內
- [ ] `prewarm.status()["fundmgr.form_choices_cache"]["status"] == "done"`
- [ ] 首次 `/fundmgr/portfolio` log 中 `form choices built` < 1.0 s

---

## Phase 4 - Broker SDK / Quote Agent Import 預熱

**狀態**： 待實作
**目標 Plugin**：`finfun-broker-sino`, `finfun-broker-yuanta`, `finfun-quotesvcs`
**預期效益**：消除 broker SDK C-extension 模組層級 import 4583 s 延遲

### 背景

目前 broker `__init__.py` 在模組載入時即執行重型 import：

| Plugin | 問題行 | 估計耗時 |
|---|---|---|
| `finfun-broker-sino/__init__.py:5` | `import shioaji as sj` | 4562 s |
| `finfun-broker-yuanta/__init__.py:14` | `from YuantaOneAPI import enumEnvironmentMode` | 2040 s |

**解法**：將模組層級 import 改為 lazy（函式內部），並由 prewarm 預熱：

```python
# finfun-broker-sino/finfun/broker_sino/__init__.py
# 移除模組層級：import shioaji as sj

_sj = None

def _get_sj():
    global _sj
    if _sj is None:
        import shioaji as sj
        _sj = sj
    return _sj

# 在模組底部登記 prewarm
try:
    from funlab.core import prewarm as _pw
    _pw.register(
        "broker_sino.shioaji_import",
        lambda: _get_sj(),
        blocking=False,
        delay=10.0,
        skip_if_exists=True,
    )
except ImportError:
    pass
```

對 `finfun-quotesvcs`（`QuoteService(EnhancedServicePlugin)`）：

```python
class QuoteService(EnhancedServicePlugin):

    def register_prewarm_tasks(self):
        import funlab.core.prewarm as pw
        pw.register(
            "quotesvcs.exchange_calendars",
            lambda: __import__("finfun.utils.fin_cale", fromlist=["_ensure_calendar_registered"])._ensure_calendar_registered(),
            blocking=False,
            delay=5.0,
            skip_if_exists=True,
        )
```

### 查核點
- [ ] `prewarm.status()["broker_sino.shioaji_import"]["status"] == "done"` 啟動後 120s 內
- [ ] 首次下單/報價 log 無 `import shioaji` 延遲
- [ ] `grep "^import shioaji" finfun-broker-sino/` 回傳空（模組層級 import 已移除）

---

## Phase 5 - `finfun-quantanlys`：numpy/pandas/scipy 模組層級 import 延遲

**狀態**： 待實作
**目標**：`finfun-quantanlys/finfun/quantanlys/quanteval.py`（無 plugin class，為純計算模組）
**預期效益**：減少 finfun-quantanlys 被其他 plugin import 時的加載成本 25 s

### 任務

`quanteval.py` 目前第 14-27 行有：
```python
import numpy as np
import pandas as pd
from scipy import stats
```

這些是**計算模組**，不在 web request path 上，但若被其他模組直接 import 時會引入延遲。
建議：
1. 把 `quanteval.py` 的 `numpy`/`pandas`/`scipy` 改為 function-level lazy（僅在用到的函式內 import）
2. 或使用 `TYPE_CHECKING` guard 限制靜態分析時的 import

### 查核點
- [ ] `python -c "import time; t=time.time(); from finfun.quantanlys import quanteval; print(time.time()-t)"` < 0.5 s

---

## Phase 6 - 清理收斂

**狀態**： 待實作（待所有前序 Phase 完成後）

### 任務
1. 確認所有手動 background thread 已移除
2. 確認 `prewarm.status()` 所有任務為 `done` 或 `failed`（無 `pending` 在啟動後 120s）
3. 在 `/health` API 加入 prewarm 狀態回報
4. 更新 `PLUGIN_DEVELOPMENT_GUIDE.md` 加入 prewarm 段落
5. `funlab-auth` 的 `authlib` OAuth import 可選擇性加入（0.51 s，低優先）

### 查核點
- [ ] `grep -r "_init_calendar_worker" finfun-fundmgr/` 空
- [ ] `grep -r "^import shioaji" finfun-broker-sino/` 空
- [ ] `grep -r "^from YuantaOneAPI" finfun-broker-yuanta/` 空
- [ ] 系統冷啟動首次請求延遲 < 5 s
