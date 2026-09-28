# DbMgr 使用指南

`funlab/core/dbmgr.py:DbMgr` — SQLAlchemy Engine/Session 的執行緒安全管理器。

## 現況速覽

- 單一 Engine 延遲建立（double-checked locking），全部執行緒共用。
- Session 经 `scoped_session`（內部 `threading.local()`）取得：**每個執行緒獨立一個 Session**。
- Session 選項：`expire_on_commit=False`、`autoflush=False`。
- `session_context()`：正常離開 commit、例外 rollback、最外層離開時 `remove_session()`。
- `create_registry_tables(registry)` / `create_entity_table("pkg.module.Class")` 建表。
- `release()`：清 scoped_session、`engine.dispose()`；`flush_on_shutdown()`：依 db_type 做
  CHECKPOINT / FLUSH / WAL checkpoint。
- `mask_db_url(url)`：安全 log 用（見 `IMPROVEMENT_PLAN.md` LIB-02 的提議函數，目前尚未實作）。

## ⚠️ 現況限制：`session_context()` 不可巢狀使用

`funlab/core/dbmgr.py:DbMgr.session_context` 目前（截至本文件撰寫）**不是 re-entrant 的**：

- 同一執行緒巢狀使用時，`scoped_session` 回傳同一個 Session；內層 `with` 結束會
  **commit 整個外層交易並 `remove_session()`**。外層之後再 `raise`，已提交的寫入不會消失。
- 實跑驗證（tmp sqlite）：外層 insert → 內層正常結束 → 外層 raise，結果列**留下**（rows=[1,2]，
  正確應為 []）。
- 同理：外層 session 物件在內層結束後 `in_transaction()` 為 False，繼續用它寫入會落在
  「外層以為還活著」的錯覺上。

**現行必須遵守的規則**：

1. 一個請求/任務呼叫鏈中，`with dbmgr.session_context()` 只准出现在最外層一次。
2. 內部函式需要 session 時，**把 session 當參數傳入**，不要在函式內再包一層
   `session_context()`：

```python
def helper(session):          # ✅ 接受呼叫端傳入的 session
    session.add(...)

def handler(dbmgr):
    with dbmgr.session_context() as s:
        helper(s)
```

3. 若確需獨立子交易（SAVEPOINT），用 `session.begin_nested()` 並自行管理，
   不要在 `session_context` 內巢狀 `session_context`。
4. 跨執行緒各用各的 `session_context()` 是安全的（thread-local 隔離），
   有既有測試覆蓋（`tests/test_dbmgr_multithreaded.py`）。

治本修正（threading.local 深度計數 + `nested=True` SAVEPOINT 選項，含完整程式碼與測試）
見 `IMPROVEMENT_PLAN.md` **LIB-01**；修復合併前，本節規則有效。

## 基本用法

```python
from funlab.core.dbmgr import DbMgr
from funlab.core.config import Config
from sqlalchemy import select

dbmgr = DbMgr(Config({"url": "sqlite:///./app.db"}))

with dbmgr.session_context() as session:
    session.add(User(name="alpha"))          # 離開時自動 commit

with dbmgr.session_context() as session:     # 新的最外層 context = 新 Session
    users = session.execute(select(User)).scalars().all()
```

`DbMgr` 也可直接吃 dict（內部包成 `Config`），且 `url` 鍵大小寫不拘
（`get('url', case_insensitive=True)`）。

## 生命週期要點

- `appbase` 在 `teardown_appcontext` 呼叫 `remove_session()` 作跨請求兜底清理
  （`funlab/core/appbase.py:_FlaskBase.register_request_handler`）。
- 直接呼叫 `get_db_session()` 而不進 `session_context()` 時，自行負責
  commit/rollback + `remove_session()`，否則該執行緒的 thread-local Session 會掛著未收尾。
- 應用關閉：`appbase._cleanup_on_exit → dbmgr.flush_on_shutdown() + dbmgr.release()`。

## 測試

```bash
cd funlab-libs && python -m pytest -q tests/test_dbmgr.py tests/test_dbmgr_multithreaded.py
```

涵蓋：提交/回滾語意、執行緒隔離、併發插入、50×10 壓力、單執行緒 rollback 不影響他緒。
