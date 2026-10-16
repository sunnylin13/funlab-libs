# LOGGING：log 管線分類與歸檔權單一化規範（正本）

> 本檔是 fund13 全系統 log 治理的**規範正本**（A 類終態文）。
> 依據：KB `dev/designs/ARCH-LOGGING-log管線分類與歸檔權單一化架構設計.md`（核准 2026-10-16）。
> 實作：`funlab/utils/log.py` 的 `setup_logging()` / `LogConfig` / `LogType`。
> 適用範圍：所有 funlab-*／finfun-* 應用碼與部署守則。

## 1. 四條原則（設計 §3.1）

1. **應用碼唯一出口＝stdout**。任何應用程序禁止自行開 FileHandler 歸檔；寫檔權唯一屬於
   supervisor（服務＝systemd／排程＝cron 外層重導）。判別句：**程序自己不知道自己是服務
   還是腳本，所以程序不該決定落盤策略。**
2. **歸檔權＝supervisor，平台中立契約**：
   - Linux：journald（`StandardOutput=journal`＋`SystemMaxUse`），輪替／容量由 systemd 原生負責。
   - Windows（無 systemd）：WinSW 或 NSSM 的日誌輪替（service wrapper 層），契約同——應用碼不改一行。
   - 無 supervisor 的 ad-hoc 路徑（手跑腳本、一次性任務）：`setup_logging()` 提供
     `LogType.ROTATING_FILE`（TimedRotatingFileHandler，明確指定路徑＋backupCount），
     這是「程序自己寫檔」的**唯一准許形態**，用於真的沒有人接管 stdout 的場合。
3. **每個程序入口必須呼叫 `setup_logging(LogConfig(...))`**：run.py、CLI entrypoint、
   cron 入口腳本（-m 模組的 `__main__`）。等級由 config.toml `[LOGGING] level` 控制，默認 INFO。
   第三方库降噪走 logger 層級表（見 §3），不走全局 basicConfig。
4. **審計事實永不入 log**：帳務／成交事實的唯一事實來源是 DB 事件表（既有 ADR-015 紀律），
   log 只承載「程序發生了什麼」，不承載「帳上發生了什麼」。

## 2. 進程拓撲（設計 §3.3）

```
應用碼(142 處 get_logger / 17 處 getLogger / 第三方库)
   └─ 唯一出口 stdout（stderr 合併同源）
        ├─ fund13-web.service ──→ journald（SystemMaxUse=200M）
        ├─ cron 排程 ──────────→ cron 外層 >> .deploy/logs/cron_*.log
        ├─ ad-hoc 手跑 ────────→ 終端，或显式 ROTATING_FILE
        └─ fubon_neo/shioaji SDK（不可控）→ QLOGDIR 隔離目錄
```

## 3. 噪音治理規範（設計 §4）

原則：**ERROR 必須代表「需要人看的事」**；會自我重試收斂的失敗不是 ERROR。

噪音源處置清單（實測 30 日內筆數）：

| # | 噪音源 | 處置 | 落點 |
|---|--------|------|------|
| N1 | `finfun.quotesvcs.pool.provider_entry` [Pool] sino 探測失敗（1811 筆 ERROR） | 探測失敗屬「預期內可自癒」：pool 內降為 WARNING；同 provider 同訊息加 10 分鐘抑制窗（首筆全量、窗內計數、窗末匯總一行「重複 N 次」）；連續探測失敗跨越升級閾值（默認 30 分鐘）才升回 ERROR 一次 | finfun-quotesvcs pool/provider_entry.py |
| N2 | `is_connected: 代表合約快照探測失敗（殭屍）`（1790 筆 WARNING） | 併入 N1 同一抑制窗機制 | finfun-broker-sino quote_adapter.py |
| N3 | scrapy `core.scraper`＋`downloadermiddlewares.retry`（830 筆 ERROR） | logger 層級降噪：setup_logging 的第三方降噪表設 scrapy 相關 logger 至維持必要等級、retry 匯總每任務一行 | 入口層 logger 表（不改 scrapy 码） |
| N4 | apscheduler executor default（181 筆，任務拋出時全 traceback） | 保留 ERROR（任務失敗＝需人看），但 traceback 去重：同任務同日僅首筆全量 | funlab-sched 任務包裝層 |

規範條文：

- 禁止在迴圈／探測路徑中無抑制地逐次 `log.error`；可自癒失敗用 WARNING＋計數。
- 抑制窗匯總行必含「重複 N 次、時間範圍」，不靜默丟棄。
- 降噪一律走 `setup_logging()` 的 logger 層級表（`noise_levels` 參數，數據驅動的 dict：
  logger 前綴→level），禁止散點 `logging.getLogger('x').setLevel()` 與 `basicConfig`。

## 4. API 用法（funlab-libs 實作面）

### 4.1 入口接線（原則 3）

```python
from funlab.utils.log import setup_logging, LogConfig, LogType, LogFmtType

setup_logging(
    LogConfig(level=logging.INFO, logtype=LogType.STDOUT, fmt=LogFmtType.SHORT),
    noise_levels={"scrapy.core": logging.WARNING},  # 第三方降噪表，可省略
)
```

### 4.2 ROTATING_FILE（無 supervisor 手跑場合的唯一准許寫檔形態）

```python
setup_logging(LogConfig(
    logtype=LogType.ROTATING_FILE,
    filename="~/logs/myscript.log",  # 必填；缺失降級 STDOUT＋stderr 警告
    backup_count=7,                  # 保留 7 份備份
    max_bytes=None,                  # None＝每日午夜輪替；非 None＝按大小輪替
))
```

- `max_bytes=None`：`TimedRotatingFileHandler(when='midnight', backupCount=backup_count)`。
- `max_bytes` 非 None：`RotatingFileHandler(maxBytes=max_bytes, backupCount=backup_count)`。
- 兩者 `encoding='utf-8'`。
- 目錄不可寫：降級 stderr＋一行警告，**不 raise**（log 不可拖垮交易路徑，設計 §7）。

### 4.3 FILE／BOTH 已棄用

`LogType.FILE`／`LogType.BOTH` 保留枚舉值但標記 deprecated：`get_logger()` 與
`setup_logging()` 收到時一律降級為 STDOUT 並向 stderr 印一行 DeprecationWarning
（改用 ROTATING_FILE 或由 supervisor 歸檔）。一個 release 後刪除枚舉。

## 5. 部署守則（歸檔權端）

- Linux 服務：unit 內 `StandardOutput=journal`／`StandardError=journal`；
  journald 設 `SystemMaxUse=200M`。禁止 `append:` 直寫磁碟檔。
- cron 排程：外層重導 `>> .deploy/logs/cron_*.log`，配 logrotate（daily、rotate 14、copytruncate）。
- Windows 對位：WinSW／NSSM service wrapper 日誌輪替；無 supervisor 手跑：ROTATING_FILE。（條文，不实作）
- 不可控 SDK（fubon_neo／shioaji）：`QLOGDIR` 隔離目錄＋短保留，屬外部手術，細節見設計 §5。
