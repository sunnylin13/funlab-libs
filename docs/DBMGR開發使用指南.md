# DbMgr 開發使用指南

`funlab/core/dbmgr.py:DbMgr` — SQLAlchemy Engine/Session 的執行緒安全管理器。
本指南只講「怎麼用」與「必須遵守的規則」；實作細節以 `dbmgr.py` 的程式碼說明為準。

## 快速上手

```python
from funlab.core.dbmgr import DbMgr
from funlab.core.config import Config
from sqlalchemy import select

dbmgr = DbMgr(Config({"url": "sqlite:///./app.db"}))   # 也可直接吃 dict；url 鍵大小寫不拘

with dbmgr.session_context() as session:
    session.add(User(name="alpha"))                    # 最外層正常離開時自動 commit

with dbmgr.session_context() as session:
    users = session.execute(select(User)).scalars().all()
```

建表：`create_registry_tables(registry)`（整個 metadata，依 registry 去重）或
`create_entity_table("pkg.module.Class")`（單表）。

## 交易語意（session_context）

同一執行緒巢狀使用是**安全**的（LIB-01 修復後，threading.local 深度計數）：

| 位置 | 行為 |
|---|---|
| 最外層（depth 0→1） | 正常離開 commit、例外 rollback、離開時 `remove_session()` |
| 內層（預設） | 只轉發同一個 Session，不提交、不移除；例外原樣上拋，由最外層決定回滾整個交易 |
| 內層 `nested=True` | 走 SAVEPOINT（`begin_nested()`）：內層回滾只撤銷存點後的寫入，不影響外層 |

```python
with dbmgr.session_context() as outer:                 # 外層放棄 → 內外層寫入一起消失
    outer.add(A())
    with dbmgr.session_context() as inner:
        inner.add(B())
    raise RuntimeError("abort")                        # A、B 都不入庫

with dbmgr.session_context() as outer:                 # SAVEPOINT：只丟存點後的
    outer.add(A())
    try:
        with dbmgr.session_context(nested=True) as sp:
            sp.add(B())                                # 這段失敗可局部撤銷
            raise ValueError("子區塊失敗")
    except ValueError:
        pass
    outer.add(C())                                     # A、C 入庫，B 撤銷
```

使用守則：

1. 內部函式需要 session 時，優先**把 session 當參數傳入**；`session_context()` 巢狀
   只是防呆，不是鼓勵到處開子 context。
2. 需要「失敗可局部撤銷、外層照樣提交」的子區塊，才用 `session_context(nested=True)`。
3. 跨執行緒各自開 `session_context()` 安全（thread-local 隔離），有既有測試覆蓋。

## 生命週期要點

- Web 請求：`appbase` 在 `teardown_appcontext` 自動 `remove_session()` 兜底收尾。
- 直接呼叫 `get_db_session()` 而不進 `session_context()` 時，自行負責
  commit/rollback + `remove_session()`，否則該執行緒的 Session 掛著未收尾。
- 應用關閉：`appbase._cleanup_on_exit` 依序呼叫 `flush_on_shutdown()`（PG: CHECKPOINT +
  pg_switch_wal；MySQL: FLUSH TABLES/LOGS；SQLite: WAL checkpoint）與 `release()`
  （清 scoped_session、`engine.dispose()`）。

## 日誌安全

log 資料庫位置一律經 `mask_db_url(url)` 遮罩密碼（`appbase` 啟動 log 已採用），
禁止直接把原始 DB URL 寫入日誌。

## 測試

```bash
cd funlab-libs && python -m pytest -q tests/test_dbmgr*.py tests/test_db_url_mask.py
```

涵蓋：提交/回滾語意、巢狀與 SAVEPOINT 回歸、執行緒隔離、併發插入、50×10 壓力、
registry 去重、URL 遮罩。
