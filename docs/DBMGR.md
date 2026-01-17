# DbMgr 使用筆記與多執行緒設計

## 快速開始

重構後的 DbMgr 具備以下行為：
- 單一 SQLAlchemy Engine 以 lazy 方式建立並在多執行緒間共用。
- scoped_session 維護 thread-local Session，`session_context()` 會在離開區塊時自動 `commit`，遇到例外則 `rollback`，最後清理當前執行緒的 Session。
- Session 建立時採用 `expire_on_commit=False` 與 `autoflush=False`，避免在提交後物件立即過期或過早 flush。
- `create_registry_tables(registry)` 以 registry.metadata 建立資料表；`create_entity_table("pkg.module.ClassName")` 仍可逐一建立。
- `release()` 會清除 scoped_session registry 並處置 Engine。

## 多執行緒安全性

**重要澄清**：新設計利用 `scoped_session` 的內部 `threading.local()` 機制。
- ✅ 每個執行緒獲得獨立的 Session（自動隔離，不是共用）
- ✅ 無內存洩漏風險（threading.local() 自動清理）
- ✅ 零鎖競爭（初始化後無需加鎖）
- ✅ 符合 SQLAlchemy 官方最佳實踐

詳細分析見 [DBMGR_THREAD_SAFETY_ANALYSIS.md](DBMGR_THREAD_SAFETY_ANALYSIS.md)。

## 基本使用方式
```python
from funlab.core.dbmgr import DbMgr
from funlab.core.config import Config
from sqlalchemy import select

config = Config({"url": "sqlite:///./app.db"})
dbmgr = DbMgr(config)

with dbmgr.session_context() as session:
    session.add(User(name="alpha"))

with dbmgr.session_context() as session:
    users = session.execute(select(User)).scalars().all()
```

## 多執行緒測試

運行多執行緒安全性測試：
```bash
poetry run pytest tests/test_dbmgr_multithreaded.py -v
```

測試涵蓋：
- 執行緒隔離與資料獨立性
- 並發插入無競態條件
- 高併發壓力（50 執行緒 × 10 事務）
- 異常 rollback 不影響其他執行緒

## 單執行緒測試

```bash
poetry run pytest tests/test_dbmgr.py -v
```

測試重點：
- 成功提交後的資料可在下一個 Session 讀取。
- 發生例外時會 rollback，資料不會落盤。
- `remove_thread_sessions()` 會清掉當前執行緒的 Session 實例。
