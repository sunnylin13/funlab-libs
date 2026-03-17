# Import 策略最佳實踐

> **Module**: `funlab.core.prewarm`
> **Updated**: 2026-03-13
> **Scope**: Funlab / Finfun 全系統

---

## 1. 核心原則

> **用最簡單的 Python 慣用手法處理 import；只對確認昂貴的模組才啟用 prewarm。**
>
> 不應為了「看起來一致」而在每個 import 位置都加入額外封裝層。
> 額外封裝（如 `_lazy()` wrapper、全域 `_get_xxx()` 薄函式）會增加維護成本，
> 卻對輕量模組零效益。

---

## 2. Import 分類標準

### 2.1 如何判定「重型 import」

一個模組是否需要特殊處理，取決於兩個維度：

| 維度 | 輕量 (< 0.5 s) | 中量 (0.5 – 3 s) | 重量 (> 3 s) |
|------|----------------|-------------------|--------------|
| **首次 import 耗時** | stdlib, flask, sqlalchemy (types), funlab.* | authlib, apscheduler | pandas, numpy, scipy, ffn, exchange_calendars, shioaji, YuantaOneAPI, scrapy |
| **是否在啟動路徑上** | 是 → top-level OK | 評估是否可延遲 | **必須延遲** |

**量測方法**（可在開發環境執行）：
```python
import time
t = time.perf_counter()
import <module_name>
print(f"{time.perf_counter() - t:.3f}s")
```

### 2.2 三級分類決策樹

```
模組首次 import > 3s ?
  ├── 是 → 「重型」：function-level import + prewarm 背景預熱
  │         （pandas, numpy, scipy, ffn, exchange_calendars,
  │           shioaji, YuantaOneAPI, scrapy, comtypes/COM）
  │
  └── 否 → 模組首次 import 0.5 ~ 3s ?
              ├── 是 → 「中型」：視情況 function-level import
              │         若該模組在啟動必經路徑 → top-level OK
              │         若僅少數路由使用 → function-level import
              │         （authlib, apscheduler）
              │
              └── 否 → 「輕量」(< 0.5s)：top-level import
                        （flask, sqlalchemy types, funlab.*, stdlib）
```

---

## 3. 三種策略的使用時機

### 3.1 Top-level Import（預設策略）

**適用於**：啟動成本 < 0.5 s 且全程必需的模組。

```python
# ✅ 正確：輕量模組放在檔案開頭
from flask import render_template, request
from sqlalchemy import select, update
from funlab.core.menu import Menu, MenuItem
```

**優點**：結構清晰、錯誤早期暴露、IDE 支援完整。

### 3.2 Function-level Import（延遲策略）

**適用於**：
- 重型模組（> 3 s）— 延遲到實際使用時才付出成本
- 避免循環 import
- 僅少數路徑使用的模組

```python
# ✅ 正確：重型模組放在使用處
def portfolio():
    import pandas as pd
    import ffn
    # ... 使用 pd, ffn
```

**注意**：Python 內建快取 — 首次 `import pandas` 後，後續在其他函式內再
`import pandas` 只是從 `sys.modules` 取出，開銷趨近於零。
**不需要額外的 `@cache` wrapper 或 `_lazy()` 封裝。**

### 3.3 Prewarm 背景預熱（主動策略）

**適用於**：重型模組 + 會被多處使用 + 不想讓首次請求承擔成本。

```python
# ✅ 正確：在 plugin 的 register_prewarm_tasks() 中登記
class MyPlugin(EnhancedViewPlugin):
    def register_prewarm_tasks(self):
        import funlab.core.prewarm as pw
        pw.register(
            "myplugin.pandas_ffn",
            lambda: (__import__('pandas'), __import__('ffn')),
            blocking=False,
            delay=5.0,
        )

# 然後在路由中正常使用 function-level import
def my_route():
    import pandas as pd   # prewarm 已完成 → cache hit → 0 成本
    import ffn             # 同上
```

**prewarm + function-level import 的組合**達成：
- 啟動時不阻塞
- 背景預載入 `sys.modules`
- 首次請求幾乎無延遲
- **無需任何自訂 wrapper**

---

## 4. 反模式（應避免的做法）

### 4.1 ❌ 為每個 import 建立 `_get_xxx()` 薄包裝函式

```python
# ❌ 不建議：增加封裝層卻無實質收益
def _get_pd():              return _lazy('pandas')
def _get_np():              return _lazy('numpy')
def _get_ffn():             return _lazy('ffn')
def _get_fin_loader():      return _lazy('finfun.utils.fin_loader')
# ... 20+ 一行包裝函式

# 然後在呼叫點：
pd = _get_pd()
```

**問題**：
1. 每新增一個依賴就要加一個函式 — 維護成本
2. IDE 無法追蹤型別（`_get_pd()` 回傳 `Any`）
3. 對輕量模組毫無效益
4. 與 Python 內建 `sys.modules` 快取功能重疊

**正確做法**：直接在使用處 `import pandas as pd`。

### 4.2 ❌ 在 view 模組建立通用 `_lazy()` 快取工廠

```python
# ❌ 不建議：與 sys.modules 功能重疊
@functools.cache
def _lazy(module_path, attr=None):
    module = importlib.import_module(module_path)
    return getattr(module, attr) if attr else module
```

**問題**：
1. `importlib.import_module()` 本身就會使用 `sys.modules` 做快取
2. `@functools.cache` 加在外層是多餘的二次快取
3. 把 import 語句藏在字串參數中，犧牲了 IDE 的自動完成和型別推導
4. 增加了一層間接性，新加入者需要理解這個自訂架構

**例外**：
若模組有**副作用初始化邏輯**（例如 `_get_twse_calendar()` 需要 calendar 註冊 + fallback），
則封裝為具名 helper 是合理的，但這屬於「業務邏輯封裝」而非「import 快取」。

### 4.3 ❌ Heavy import 放在 top-level

```python
# ❌ 不建議：阻塞啟動
import pandas as pd        # 3-5s
import shioaji as sj       # 3-5s
from scipy import stats    # 2-3s
```

**正確做法**：移至 function-level + prewarm。

---

## 5. 各層級模組的適用策略速查表

| 層級 | 模組範例 | 建議策略 | 理由 |
|------|---------|---------|------|
| **stdlib** | `os`, `time`, `datetime`, `threading` | Top-level | 極快，無需延遲 |
| **Flask 生態** | `flask`, `flask_login`, `flask_restx` | Top-level | Plugin 框架核心，啟動必需 |
| **SQLAlchemy (types)** | `sqlalchemy.select`, `Column`, `and_` | Top-level | ORM 型別定義輕量 |
| **funlab 內部** | `funlab.core.menu`, `funlab.utils.dtts` | Top-level | 自家輕量模組 |
| **finfun 輕量** | `finfun.core.constants`, enums | Top-level | 純 Python，無 C-ext |
| **finfun entity** | `finfun.core.entity.*` | 已有 `__getattr__` lazy | 維持現有 entity lazy 機制 |
| **authlib** | `authlib.integrations.flask_client` | Top-level 或 FL | 0.5s，若僅 auth plugin 用可接受 TL |
| **apscheduler** | `apscheduler.schedulers.background` | Top-level (in sched) | sched service 啟動必需 |
| **pandas / numpy** | `pandas`, `numpy` | **FL + prewarm** | 3-5s，多處使用 |
| **scipy** | `scipy.stats` | **FL + prewarm** | 2-3s |
| **ffn** | `ffn` | **FL + prewarm** | 3-5s（cascades pandas+numpy） |
| **exchange_calendars** | `exchange_calendars` | **FL + prewarm** | 50-83s（含 TWSE calendar 註冊） |
| **shioaji** | `shioaji` | **FL + prewarm** | 3-5s，C-extension |
| **YuantaOneAPI** | `YuantaOneAPI.*` | **FL + prewarm** | 2-4s，.NET CLR |
| **scrapy** | `scrapy` | **FL** | Heavy 但僅 finfetch 任務用 |
| **comtypes** | `comtypes.gen.SKCOMLib` | **FL + prewarm** | COM DLL 載入 |

---

## 6. Prewarm 登記的所有權原則

> **Plugin（消費者）負責登記**自己需要的預熱任務。
> **Library（提供者）保持被動**，不在 import 時產生副作用。

```python
# ✅ 正確：plugin 在自己的 register_prewarm_tasks() 中登記
class FundMgrView(EnhancedViewPlugin):
    def register_prewarm_tasks(self):
        import funlab.core.prewarm as pw
        pw.register("fundmgr.pandas_ffn", ...)

# ❌ 錯誤：library 模組在 import 時自行登記
# finfun/utils/data_utils.py
import funlab.core.prewarm as pw  # 不應在此處
pw.register("data_utils.pandas", ...)  # library 不應自行登記
```

**多 plugin 共用資源**：各自登記 + `skip_if_exists=True`，先到先得。

---

## 7. 重構 Checklist

當你在任何模組中看到以下模式時，按此 checklist 重構：

- [ ] **`_get_xxx()` 薄包裝** → 刪除，改為呼叫點直接 `import xxx`
- [ ] **`_lazy(module_path)` 呼叫** → 改為呼叫點 `import` 或 `from ... import`
- [ ] **有業務邏輯的 helper**（如 `_get_twse_calendar()`）→ 保留，但重命名為語意更明確的名稱（如 `_init_twse_calendar()`）
- [ ] **Heavy top-level import** → 移至 function-level + 在 `register_prewarm_tasks()` 登記
- [ ] **驗證**：`python -c "import time; t=time.time(); import <your_module>; print(time.time()-t)"` < 目標秒數
