# funlab-libs 改善方案（IMPROVEMENT_PLAN）

> 本文件是唯一事實來源：每一項都由原始碼核實（引用格式 `相對路徑:符號名`，約略行號僅輔助），
> 且標記「實跑驗證」的條目都有探針輸出佐證（探針只寫入 `$TMPDIR`/tmp sqlite，不碰正式庫）。
> 執行環境：`cd funlab-libs && source ~/.venv/fund13/bin/activate`（Python 3.12）。
> 測試基線（2026-09-27 實跑，docs/prewarm 歷史腳本已刪除後）：`python -m pytest -q` → **119 passed**。
> 剩餘動作僅 LIB-06 的 `testpaths` 設定（防止日後 docs/ 再放腳本污染收集）。
> **實施狀態（2026-09-28 對帳）**：LIB-01～LIB-18 已全部合併進 main（A1/A2/A6→Wave1；LIB-02/04/05/06/07/10/11/14/15→B6 commit 1e79490；LIB-08/09/12/13/16/17/18→C1 commit 9a15534；qa2-supp 補測 d606cf7），並已部署正式服務。測試基線現況 **184 passed**。本文各節 (a) 段描述的是【修復前】缺陷，(d)(e) 段為已探納的修法式（與 main 實碼一致，例 LIB-01 的 threading.local 深度計數見 dbmgr.py）。

## 總覽表

| 編號 | 優先級 | 檔案 | 一句話摘要 | 工作量 |
|---|---|---|---|---|
| LIB-01 | **P0** | `funlab/core/dbmgr.py` | `session_context()` 巢狀使用把外層交易提前 commit+關閉，金融資料「回滾不掉了」 | 中（含多執行緒測試） |
| LIB-03 | **P0** | `funlab/core/jinja_filters.py` | `%q` 季別算錯：3 月報 Q2、12 月報 Q5，報表直接錯資料 | 小 |
| LIB-02 | P1 | `funlab/core/appbase.py` | DB URL 遮罩只遮密碼前 9 碼，長密碼外洩進 log | 小 |
| LIB-04 | P1 | `funlab/utils/form.py` | PEP 604 `int \| None` 不被辨識為 Optional，排程表單驗證行為錯 | 小 |
| LIB-05 | P1 | `pyproject.toml` | 7 個實際 import 的套件未宣告相依，乾淨環境裝不起來 | 小 |
| LIB-06 | P1 | `pyproject.toml` + `docs/` | pytest 誤收 docs 下歷史測試腳本造成 1 error；需 testpaths 隔離 | 小 |
| LIB-07 | P1 | `funlab/core/__init__.py` | `DataclassJSONEncoder.default` 誤用 `fields(self)` → 必拋 TypeError（實跑驗證） | 小 |
| LIB-10 | P1 | `funlab/core/dbmgr.py` | `create_entity_table` bare `except:` 丟失例外鏈，排障無從下手 | 小 |
| LIB-11 | P1 | `funlab/core/hook.py` | HookManager dispatch 與 register 無鎖共用 list；callback 內再註冊會污染當次 dispatch（實跑驗證） | 小 |
| LIB-14 | P1 | `funlab/core/plugin_manager.py` | plugin 元資料快取無效期/無存活驗證：已移除 plugin 的幽靈中繼資料從快復活並照 load_mode 載入（實跑驗證） | 中 |
| LIB-15 | P1 | `funlab/core/plugin_manager.py` | `unload_plugin()` 對未載入 plugin 觸發 `NameError` 並被 `except: pass` 吞掉 | 小 |
| LIB-08 | P2 | `funlab/core/menu.py` | `has_menuitem()` 遞迴分支提前 return，空子選單後面的真實項目被判定為不存在（實跑驗證） | 小 |
| LIB-09 | P2 | `funlab/core/config.py` | `update_with_ext()` 不帶 section 時 `setattr(self, None, …)` 必拋 TypeError（實跑驗證） | 小 |
| LIB-12 | P2 | `funlab/core/appbase.py` | `PollingNotificationProvider._dismissed_global` 無界成長（記憶體洩漏，實跑驗證） | 小 |
| LIB-13 | P2 | `funlab/utils/perf_track.py` | `PerformanceTracker` 共享計數無鎖（本次探針未觀察到丟失，屬防禦性加固） | 小 |
| LIB-16 | P2 | `funlab/core/prewarm.py` | `run()` 之後註冊的任務永遠 pending、無任何警告（實跑驗證） | 小 |
| LIB-17 | P2 | `funlab/core/dbmgr.py` | `create_registry_tables()` 以 `id(registry)` 去重，id 可被 GC 後重用（實跑：50 次建立 46 次 id 重用） | 小 |
| LIB-18 | P2 | `funlab/core/jinja_filters.py` | `slope2angle` 宣告 `->float` 實回 str（實跑驗證），型別註解說謊 | 小 |

執行順序建議：LIB-01 → LIB-03 → LIB-02/LIB-04 → LIB-06/LIB-05（先把測試基線清乾淨）→ 其餘 P1 → P2。
每項完成都要跑該項驗證指令 **且** 全套 `python -m pytest -q` 零新增失敗。

---

## LIB-01（P0）`session_context()` 巢狀使用破壞外層交易

**(a) 問題與影響**
`funlab/core/dbmgr.py:DbMgr.session_context`（約 L148-170）每次進入都 `get_db_session()`，
`scoped_session` 在同一執行緒回傳**同一個** Session；內層 `with` 結束時無條件
`session.commit()` + `remove_session()`，把外層尚未完成、且必須與內層同進退的寫入**提前提交**，
隨後外層 `raise` 只能回滾一個已被提交/關閉的 session。金融場景下這等於「該消失的帳留下來了」。
**實跑驗證**（探針：tmp sqlite + registry 模型）：外層先 insert、內層正常結束後外層 raise，
結果 `rows = [1, 2]`（正確應為 `[]`）；內層結束後 `outer.in_transaction()` 為 False。
finfun-* 各 repo 約 60 處呼叫 `session_context`，巢狀情境需盤點。

**(b) 優先級**：P0（帳務正確性）。

**(c) 目標檔與函式**：`funlab/core/dbmgr.py:DbMgr.__init__`、`DbMgr.session_context`。

**(d) 完整修正後程式碼**

`__init__` 整個替換（只新增 `_session_state` 一行，其餘原樣）：

```python
    def __init__(
        self,
        conf: Config | dict,
        *,
        engine: Optional[Engine] = None,
        engine_options: Optional[Dict[str, Any]] = None,
        session_options: Optional[Dict[str, Any]] = None,
    ) -> None:
        if isinstance(conf, dict):
            self.config = Config(conf)
        else:
            self.config = conf

        if not self.config.get('url', case_insensitive=True):
            raise Exception("No database 'url' data in provided Config object.")

        self._engine: Optional[Engine] = engine
        self._engine_options = engine_options or {}
        self._session_options = session_options or {}
        self._scoped_session: Optional[scoped_session] = None
        # Per-thread session_context nesting depth (threading.local 執行緒隔離)。
        self._session_state = threading.local()
        # RLock prevents deadlock when session factory creation calls get_db_engine().
        self.__lock = threading.RLock()
```

`session_context` 整個替換：

```python
    @contextlib.contextmanager
    def session_context(self, nested: bool = False) -> Generator[Session, None, None]:
        """Re-entrancy-safe database session context manager.

        巢狀語意（同一執行緒）：
        - 最外層（depth 0→1）：負責 commit / rollback / remove_session。
        - 內層（depth >= 2，``nested=False``）：只轉發同一個 session，完全不提交、
          不移除；內層例外原樣上拋，由最外層決定回滾整個交易。
        - 內層 ``nested=True``：使用 SAVEPOINT（``Session.begin_nested()``），
          內層回滾只撤銷存點以後的寫入，不影響外層。

        Raises:
            Exception: 任何在 context 內抛出的例外都會原樣上拋。
        """
        state = self._session_state
        depth = getattr(state, 'depth', 0)
        outermost = depth == 0
        session = self.get_db_session()

        if not outermost and nested:
            savepoint = session.begin_nested()
            try:
                yield session
                savepoint.commit()
            except Exception:
                savepoint.rollback()
                raise
            return

        state.depth = depth + 1
        try:
            yield session
            if outermost:
                session.commit()
        except Exception:
            if outermost:
                session.rollback()
            # When an exception occurs, the outermost context owns rollback;
            # re-raise so the caller can handle it.
            raise
        finally:
            state.depth = getattr(state, 'depth', 1) - 1
            if state.depth == 0:
                # source: https://stackoverflow.com/questions/21078696/why-is-my-scoped-session-raising-an-attributeerror-session-object-has-no-attr
                self.remove_session()
```

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_dbmgr_nested.py`：

```python
"""LIB-01 回歸：session_context 巢狀使用不得提交/關閉外層交易。"""
import threading

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import registry

from funlab.core.config import Config
from funlab.core.dbmgr import DbMgr

mapper_registry = registry()


@mapper_registry.mapped
class Widget:
    __tablename__ = "widgets_nested"
    id = sa.Column(sa.Integer, primary_key=True)
    name = sa.Column(sa.String, nullable=False)


def _build_dbmgr(tmp_path) -> DbMgr:
    dbmgr = DbMgr(Config({"url": f"sqlite:///{tmp_path / 'n.db'}"}))
    dbmgr.create_registry_tables(mapper_registry)
    return dbmgr


def _names(dbmgr):
    with dbmgr.session_context() as s:
        return sorted(r.name for r in s.execute(sa.select(Widget)).scalars().all())


def test_inner_exit_does_not_commit_outer(tmp_path):
    dbmgr = _build_dbmgr(tmp_path)
    with pytest.raises(RuntimeError):
        with dbmgr.session_context() as outer:
            outer.add(Widget(id=1, name="out"))
            with dbmgr.session_context() as inner:
                inner.add(Widget(id=2, name="in"))
            outer.add(Widget(id=3, name="out2"))
            raise RuntimeError("outer abort")
    assert _names(dbmgr) == []


def test_outer_success_commits_both_levels(tmp_path):
    dbmgr = _build_dbmgr(tmp_path)
    with dbmgr.session_context() as outer:
        outer.add(Widget(id=1, name="out"))
        with dbmgr.session_context() as inner:
            inner.add(Widget(id=2, name="in"))
    assert _names(dbmgr) == ["in", "out"]


def test_nested_savepoint_rollback_keeps_outer(tmp_path):
    dbmgr = _build_dbmgr(tmp_path)
    with dbmgr.session_context() as outer:
        outer.add(Widget(id=1, name="kept"))
        with pytest.raises(ValueError):
            with dbmgr.session_context(nested=True) as inner:
                inner.add(Widget(id=2, name="lost"))
                raise ValueError("inner abort")
        outer.add(Widget(id=3, name="kept2"))
    assert _names(dbmgr) == ["kept", "kept2"]


def test_nested_thread_depth_isolation(tmp_path):
    dbmgr = _build_dbmgr(tmp_path)
    failures = []

    def worker(i):
        try:
            with dbmgr.session_context() as outer:
                outer.add(Widget(id=i * 100, name=f"out-{i}"))
                with dbmgr.session_context() as inner:
                    inner.add(Widget(id=i * 100 + 1, name=f"in-{i}"))
                if i % 2 == 0:
                    raise RuntimeError("abort even")
        except RuntimeError:
            pass

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _names(dbmgr) == sorted(
        [f"out-{i}" for i in (1, 3, 5)] + [f"in-{i}" for i in (1, 3, 5)]
    )
```

**(f) 驗證指令與預期結果**

```bash
cd funlab-libs && python -m pytest -q tests/test_dbmgr_nested.py tests/test_dbmgr.py tests/test_dbmgr_multithreaded.py
```
預期：4 個新測試 + 既有 dbmgr 測試全數 passed、0 failed。再跑全套
`python -m pytest -q`：除 LIB-06 的 docs error 外零新增失敗（修完 LIB-06 後應為全綠）。

**(g) 相容性/風險與不要做的事**
- 簽名純新增 keyword `nested=False`，60 處既有呼叫點零修改、非巢狀行為不變。
- 內層不帶 `nested=True` 出錯時**不再** rollback 整個 session（舊行為會連外層一起回滾掉再提交殘局，本來就是錯的）；若依賴舊「內層出錯也回滾」語意的呼叫點，改傳 `nested=True`。
- `appbase.register_request_handler` 的 `teardown_appcontext → remove_session()` 保留不動：它是跨請求兜底清理；depth 屬於 threading.local，正常請求結束應為 0。
- **不要**把 `remove_session()` 從 `finally` 拿掉、**不要**改用全域計數器（會跨執行緒互相干扰）、**不要**順手改 `get_db_session()` 給非 context 呼叫者的行為。

---

## LIB-02（P1）DB URL 密碼遮罩只遮 9 個字元

**(a) 問題與影響**：`funlab/core/appbase.py:_FlaskBase._init_configuration`（約 L349-354）用
`dburl[:i-9] + '*' + dburl[i:]` 遮罩，只把 `@` 前 9 字元換成一個 `*`。
**實跑驗證**：`postgresql+psycopg://fund:VeryLongSecret@…` 遮罩後為
`postgresql+psycopg://fund:VeryL*@…`——長密碼前段照樣進 log。

**(b) 優先級**：P1（憑證洩漏面）。

**(c) 目標檔與函式**：`funlab/core/dbmgr.py`（新增模組函數 `mask_db_url`）、
`funlab/core/appbase.py:_FlaskBase._init_configuration`。

**(d) 完整修正後程式碼**

`funlab/core/dbmgr.py` 在 `class NoDBUrlDefined` 之後新增模組級函數：

```python
def mask_db_url(url: str) -> str:
    """回傳把密碼取代為 '***' 的 URL，供安全 log 使用。

    優先使用 SQLAlchemy 的 render_as_string(hide_password=True)；
    無法解析的字串退回手動遮罩 userinfo 段（``//user:pass@`` → ``//user:***@``），
    再不行原樣回傳（此時字串本來就不含 URL 結構）。
    """
    try:
        from sqlalchemy.engine import make_url
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        at = url.find('@')
        if at > 0:
            head_cut = url.find('//')
            head = url[:head_cut + 2] if head_cut >= 0 else ''
            userinfo = url[len(head):at]
            if ':' in userinfo:
                user = userinfo.split(':', 1)[0]
                return f'{head}{user}:***{url[at:]}'
        return url
```

`funlab/core/appbase.py` 第 22 行 import 改為：

```python
from funlab.core.dbmgr import DbMgr, mask_db_url
```

`_init_configuration` 整個替換：

```python
    def _init_configuration(self, configfile: str, envfile: str):
        """
        Initializes the configuration for the application.

        Args:
            configfile (str): The path to the configuration file.
            envfile (str): The path to the environment file.

        Returns:
            None
        """
        if configfile:
            self._config: Config = Config(configfile, env_file_or_values=envfile)
        else:
            self._config: Config = Config({})

        app_config = self.get_config('config.toml', section=self.__class__.__name__,
                                     ext_config=self._config.get_section_config(section=self.__class__.__name__))
        if (logging_level := app_config.get('LOGGING_LEVEL')):
            self.mylogger = log.get_logger(self.__class__.__name__,
                                           level=logging.getLevelNamesMapping()[logging_level])
        else:
            self.mylogger = log.get_logger(self.__class__.__name__, level=logging.INFO)
        # flask's config, different from self._config
        self.config.from_mapping(app_config.as_dict())
        # Track whether SECRET_KEY had to be randomly generated.  A random key
        # makes session/CSRF tokens invalid across restarts and breaks
        # multi-worker deployments; the web layer (funlab-flaskr CSRFProtect)
        # logs a startup WARNING when this flag is set (ADR-016 D2, risk R3).
        self.secret_key_is_random: bool = False
        if not self.config['SECRET_KEY']:
            secret_key = os.urandom(24).hex()
            self.config.update({'SECRET_KEY': secret_key} )  # Fernet.generate_key().decode(), })
            self.secret_key_is_random = True

        self.dbmgr: DbMgr = None
        if db_config := self.app_config.get('DATABASE', None):
            self.dbmgr = DbMgr(db_config)
            dburl = mask_db_url(self.dbmgr.get_db_url())
            self.mylogger.info(f'Database:{dburl}')

        # self.cache = Cache(app=self, config=self._config.CACHE)
        app_cache.init_app(app=self, config=self._config.CACHE)
        self.cache:Cache = app_cache  # Cache(self, config=self._config.get('CACHE', {'CACHE_TYPE': 'SimpleCache'}))  # add Flask-Caching support

        if 'ENV' in self._config:
            del self._config.ENV

        if 'DATABASE' in self._config:
            del self._config.DATABASE
```

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_db_url_mask.py`：

```python
"""LIB-02：mask_db_url 必須遮掉完整密碼且保留可讀部分。"""
from funlab.core.dbmgr import mask_db_url


def test_masks_full_long_password():
    url = "postgresql+psycopg://fund:VeryLongSecretPw@127.0.0.1:5432/fund13"
    out = mask_db_url(url)
    assert "VeryLong" not in out
    assert out == "postgresql+psycopg://fund:***@127.0.0.1:5432/fund13"


def test_no_password_unchanged():
    url = "sqlite:////tmp/app.db"
    assert mask_db_url(url) == url


def test_non_url_string_unchanged():
    assert mask_db_url("not a url") == "not a url"
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_db_url_mask.py tests/test_appbase_http_passthrough.py`
→ 全 passed（後者是 appbase 現況回歸，確認沒改壞）。

**(g) 風險與不要做的事**：`make_url` 對某些自訂 dialect 可能不認得 scheme → 走 fallback 手動遮罩，不會拋錯。
**不要**把遮罩後 URL 再拿去建連線（只可用於 log）；**不要**動 `DbMgr.get_db_url()` 的回傳值本身。

---

## LIB-03（P0）Jinja `%q` 季別錯誤

**(a) 問題與影響**：`funlab/core/jinja_filters.py:timestamp_natation`（約 L22）用
`ddate.month // 3 + 1`：3 月 → 2、12 月 → 5。
**實跑驗證**（本地時間戳探針）：month→%q = `{3:'2', 6:'3', 9:'4', 12:'5'}`，正確應為 `{3:'1', 6:'2', 9:'3', 12:'4'}`（季制＝Q1:1-3月/Q2:4-6月/Q3:7-9月/Q4:10-12月，與 `dtts.quarter_of_date` 恆等）。
所有用 `%q` 的季報標題/彙總標籤直接錯資料。

**(b) 優先級**：P0（產出錯誤資料；本專案鐵律「寧可無法計算，不可計算錯資料」）。

**(c) 目標檔與函式**：`funlab/core/jinja_filters.py:timestamp_natation`。

**(d) 完整修正後程式碼**

```python
def timestamp_natation(timestamp:float, formatstr:str='%Y-%m-%d %H:%M:%S')->str:
    """
    Converts a float timestamp() value to a formatted notation string in python.
    And, provide extra notation %q, this present the quarter number, e.g., %q got 2 for 2023-04-01.
    No %Q needed and supported.

    Args:
        timestamp (float): The timestamp float value to convert.
        formatstr (str, optional): The format string for the notation. Defaults to '%Y-%m-%d %H:%M:%S'.

    Returns:
        str: The formatted notation string.
    """
    ddate = dtts.utc_timestamp2local_datetime(timestamp)
    formatstr = formatstr.replace('%q', f'{(ddate.month - 1) // 3 + 1}')
    notation = ddate.strftime(formatstr)
    return notation
```

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_jinja_filters.py`（LIB-18 的測試也放這裡）：

```python
"""LIB-03 / LIB-18：jinja filters 正確性。"""
from datetime import datetime

from funlab.core.jinja_filters import slope2angle, timestamp_natation
from funlab.utils.dtts import local_datetime2utc_timestamp


def _ts(y, m, d=15):
    return local_datetime2utc_timestamp(datetime(y, m, d))


def test_quarter_filter_month_3_is_q1():
    assert timestamp_natation(_ts(2024, 3), '%q') == '1'


def test_quarter_filter_month_6_is_q2():
    assert timestamp_natation(_ts(2024, 6), '%q') == '2'


def test_quarter_filter_month_9_is_q3():
    assert timestamp_natation(_ts(2024, 9), '%q') == '3'


def test_quarter_filter_month_12_is_q4():
    assert timestamp_natation(_ts(2024, 12), '%q') == '4'


def test_quarter_filter_month_4_is_q2():
    # 原文件既有的宣告例子：2023-04-01 → Q2
    assert timestamp_natation(_ts(2023, 4, 1), '%q') == '2'


def test_quarter_mixed_with_normal_format():
    assert timestamp_natation(_ts(2024, 12), '%Y-Q%q') == '2024-Q4'


def test_slope2angle_returns_string_of_degrees():
    assert slope2angle(1.0) == '45.000'


def test_slope2angle_none_is_na():
    assert slope2angle(None) == 'NA'
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_jinja_filters.py` → 8 passed。

**(g) 風險與不要做的事**：修正後 1/4/7/10 月結果不變（原本就對），只有 3-4、6-7、9-10、12-1 邊界改變——
這是修正不是破壞。**不要**順手改 `timestamp_natation` 的預設格式字串；**不要**改
`funlab/utils/dtts.py:quarter_of_date`（它用 `math.ceil(month/3)`，本来就正确，探針確認無誤）。

---

## LIB-04（P1）`create_form_from_dataclass` 不認 PEP 604 Union

**(a) 問題與影響**：`funlab/utils/form.py:create_form_from_dataclass`（約 L65-72）只用
`field_type.__origin__ is Union` 判 Optional。`int | None`（`types.UnionType`）判不出来 →
不會加 `Optional` validator，欄位被表單驗證視為必填；funlab-sched 的任務參數 dataclass
（`funlab-sched/funlab/sched/task.py:…create_form_from_dataclass`）正依賴此行為。
**實跑驗證**（test_request_context 內建表單）：`int | None` 欄 validators=`[]`；
`typing.Optional[int]` 欄 validators=`['Optional']`。且 PEP 604 欄位还会被
`TYPE_MAPPING.get(field_type, StringField)` 誤映射成 StringField（型別不是 `int`）。

**(b) 優先級**：P1。

**(c) 目標檔與函式**：`funlab/utils/form.py:create_form_from_dataclass`。

**(d) 完整修正後程式碼**

```python
# 從 dataclass 生成 WTForm
def create_form_from_dataclass(dataclass_type):
    import types
    import typing
    from flask_wtf import FlaskForm
    from wtforms import DateTimeField, StringField, IntegerField, FloatField, BooleanField, DateField, MonthField, HiddenField, SelectField
    from wtforms.validators import Optional as OptionalValidator

    TYPE_MAPPING = {
        str: StringField,
        int: IntegerField,
        float: FloatField,
        bool: BooleanField,
        datetime.date: DateField,
        datetime.datetime: DateTimeField
    }

    # String-based field type mapping for lazy imports
    STRING_TYPE_MAPPING = {
        'StringField': StringField,
        'IntegerField': IntegerField,
        'FloatField': FloatField,
        'BooleanField': BooleanField,
        'DateField': DateField,
        'DateTimeField': DateTimeField,
        'MonthField': MonthField,
        'HiddenField': HiddenField,
        'SelectField': SelectField,
    }

    form_fields = {}
    type_hints = get_type_hints(dataclass_type)
    for field in fields(dataclass_type):
        field_metadata = field.metadata
        field_name = field.name
        field_type = type_hints[field_name]
        # 處理 Optional 類型：typing.Optional[X] 與 PEP 604 的 X | None 都要辨識
        is_optional = False
        origin = typing.get_origin(field_type)
        if origin is Union or origin is types.UnionType:
            args = typing.get_args(field_type)
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) < len(args):
                is_optional = True
                if len(non_none) == 1:
                    field_type = non_none[0]

        # Get form field class from metadata or type hints
        form_field_class = field_metadata.get('type', TYPE_MAPPING.get(field_type, StringField))

        # Support string-based type references for lazy imports
        if isinstance(form_field_class, str):
            form_field_class = STRING_TYPE_MAPPING.get(form_field_class, StringField)

        field_kwargs = {}
        for key, value in field_metadata.items():
            if key=='default':
                if callable(value):
                    value = _defer_default(value, dataclass_type)
                field_kwargs.update({key: value})
            elif key != 'type':
                field_kwargs.update({key:value})
        for key, value in field_kwargs.copy().items():
            if value is None:
                field_kwargs.pop(key)

        # 如果是可選欄位且沒有明確設置驗證器，則不添加 DataRequired
        if is_optional and not field_kwargs.get('validators', None):
            field_kwargs['validators'] = [OptionalValidator()]

        form_fields[field.name] = form_field_class(**field_kwargs)
    if getattr(dataclass_type, 'form_javascript', None):
        form_fields['javascript'] = dataclass_type.form_javascript()

    form_class = type(dataclass_type.__name__+ 'ParamsForm', (FlaskForm,), form_fields)
    return form_class
```

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_form_pep604.py`：

```python
"""LIB-04：PEP 604 `X | None` 必須與 typing.Optional[X] 同語意。"""
from dataclasses import dataclass, field

import flask
import pytest

from funlab.utils.form import create_form_from_dataclass


@pytest.fixture
def app():
    a = flask.Flask(__name__)
    a.config['SECRET_KEY'] = 'test'
    return a


def test_pep604_gets_optional_validator_and_int_field(app):
    @dataclass
    class T604:
        n: int | None = None

    with app.test_request_context():
        form = create_form_from_dataclass(T604)()
        from wtforms import IntegerField
        from wtforms.validators import Optional as OptionalValidator
        assert isinstance(form.n, IntegerField)
        assert any(isinstance(v, OptionalValidator) for v in form.n.validators)


def test_typing_optional_behaviour_unchanged(app):
    from typing import Optional

    @dataclass
    class TOld:
        n: Optional[int] = None

    with app.test_request_context():
        form = create_form_from_dataclass(TOld)()
        from wtforms.validators import Optional as OptionalValidator
        assert any(isinstance(v, OptionalValidator) for v in form.n.validators)


def test_required_field_has_no_optional_validator(app):
    @dataclass
    class TReq:
        n: int = 0

    with app.test_request_context():
        form = create_form_from_dataclass(TReq)()
        from wtforms.validators import Optional as OptionalValidator
        assert not any(isinstance(v, OptionalValidator) for v in form.n.validators)
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_form_pep604.py tests/test_form_select_field.py` → 全 passed。

**(g) 風險與不要做的事**：`X | Y | None`（多元 Union）維持現況語意：標記 optional 但型別退回
StringField（不做猜測映射）。**不要**順手加 DataRequired 給非 Optional 欄位（會破壞 funlab-sched 既有表單）。

---

## LIB-05（P1）pyproject 相依宣告不全

**(a) 問題與影響**：`pyproject.toml:[project] dependencies`（L9-15）只宣告
colorama/sqlalchemy/cryptography/pandas/flask-caching。原始碼還直接 import：
`dateutil`（`funlab/utils/dtts.py` 頂層 `from dateutil.relativedelta import relativedelta`）、
`flask`、`flask_login`、`flask_wtf`、`werkzeug`、`wtforms`、`markupsafe`
（appbase.py / plugin.py / hook.py / form.py / auth.py / notification.py 頂層 import）。
在沒有其他 repo 順手帶入这些套件的乾淨環境中，`import funlab.core` 直接 ModuleNotFoundError。

**(b) 優先級**：P1。

**(c) 目標檔**：`pyproject.toml`。

**(d) 完整修正後區段**（dependencies 整段替換；版本下限對齊 2026-09-27 venv 實測：
flask 3.1.3 / flask-login 0.6.3 / flask-wtf 1.3.0 / wtforms 3.2.2 / werkzeug 3.1.8 / markupsafe 3.0.3 / python-dateutil 2.9.0）：

```toml
dependencies = [
    "colorama>=0.4.6,<0.5",
    "sqlalchemy>=2.0.43,<3",
    "cryptography>=42.0.7,<43",
    "pandas>=2.2.2,<3",
    "flask-caching>=2.3.0,<3",
    "flask>=3.1,<4",
    "flask-login>=0.6.3,<0.7",
    "flask-wtf>=1.3,<2",
    "wtforms>=3.2,<4",
    "werkzeug>=3.1,<4",
    "markupsafe>=3.0,<4",
    "python-dateutil>=2.9,<3",
]
```

**(e) 測試**：無需新增測試（宣告類修正）。

**(f) 驗證**：

```bash
cd funlab-libs && source ~/.venv/fund13/bin/activate && pip check && python -m pytest -q
```
預期：`pip check` 無 BrokenRequirements；pytest 結果不劣於基線。

**(g) 風險與不要做的事**：funlab-libs 是 PEP 420 namespace package，**不要**新增
`funlab/__init__.py`；**不要**把這些 import 改成 lazy（會掩蓋宣告缺漏，且 `appbase`
本就是 Flask 應用的核心路徑）；**不要**放寬既有 5 條版本上限。

---

## LIB-06（P1）pytest 誤收 docs 下的歷史測試腳本

**(a) 問題與影響**：倉庫根 `python -m pytest -q` 曾收集 `docs/prewarm/test_phase_*.py`，其中
`test_phase_1b_scientific_audit.py::test_import_time` 因 fixture `module_name` not found 而
ERROR，CI/日常驗證被歷史報告腳本污染。`docs/prewarm/` 已在本輪文件清理中刪除
（現行基線 119 passed, 0 error），本項剩餘工作是加 `testpaths` 設定，防止日後 docs/
再放腳本時同樣污染收集。

**(b) 優先級**：P1。

**(c) 目標檔**：`pyproject.toml`（新增 pytest 設定）。`docs/prewarm/` 已刪除；若任何來源仍需 Phase 報告數據，由 git 歷史取回。

**(d) 完整修正**：`pyproject.toml` 末尾新增：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

並刪除整個 `docs/prewarm/`（含 `__pycache__`）。刪除後 `PLUGIN_DEVELOPMENT_GUIDE.md`
中引用 `docs/prewarm/IMPORT_BEST_PRACTICES.md` 的文字已在本輪改寫中移除，指向 `docs/PREWARM.md`。

**(e) 測試**：無需新增測試。

**(f) 驗證**：

```bash
cd funlab-libs && python -m pytest -q
```
預期：不再出現 `docs/` 路徑的收集項目；本輪全部新測試與既有 120 條一起 passed、0 error。

**(g) 風險與不要做的事**：**不要**把 docs 腳本「移進 tests/ 修好再跑」——那些是一次性驗證報告的
附件，非回歸資產；**不要**用 `--ignore=docs` 當解法（治標不治本，其他人手跑仍會踩）。

---

## LIB-07（P1）`DataclassJSONEncoder.default` 誤用 `self`

**(a) 問題與影響**：`funlab/core/__init__.py:DataclassJSONEncoder.default`（約 L175-186）對
非 `_Readable` 的 dataclass 走 `dataclasses.fields(self)`——`self` 是 **encoder 物件**而非被
序列化物件 `o`。實跑驗證：`json.dumps(PlainDataclass(2), cls=DataclassJSONEncoder)` →
`TypeError: must be called with a dataclass type or instance`，該分支 100% 必掛。
另：分支末尾 `return f'{attrs}'` 回傳字串而非 dict，即使不掛也會輸出雙重編碼字串。

**(b) 優先級**：P1（API 必壞）。

**(c) 目標檔與函式**：`funlab/core/__init__.py:DataclassJSONEncoder.default`。

**(d) 完整修正後程式碼**（模組需已有 `is_dataclass`、`dataclasses`、`dtts` import，皆已存在）：

```python
class DataclassJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if is_dataclass(o):
            if isinstance(o, _Readable):
                return o.__readattrs__()
            attrs = {}
            for field in dataclasses.fields(o):
                if field.repr:
                    if field.name == 'timestamp' or field.name.endswith('_ts'):
                        val = getattr(o, field.name)
                        val: datetime = dtts.utc_timestamp2local_datetime(val) if val is not None else None
                        if isinstance(val, datetime) and val - datetime.combine(val.date(), time(0, 0, 0)) == timedelta(0):
                            val = val.date()
                        val = val.isoformat() if val is not None else None
                    elif field.type in (datetime, date):
                        val = getattr(o, field.name).isoformat()
                    else:
                        val = getattr(o, field.name)
                    attrs[field.name] = val
            return attrs
        elif type(o) in (datetime, date):
            return o.isoformat()
        elif hasattr(o, 'to_json'):
            return o.to_json()
        return super().default(o)
```

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_core_encoder.py`：

```python
"""LIB-07：DataclassJSONEncoder 必須序列化 dataclass 本體（而非 encoder self）。"""
import json
from dataclasses import dataclass

from funlab.core import DataclassJSONEncoder


@dataclass
class Plain:
    x: int = 1
    label: str = "a"


def test_plain_dataclass_encodes_to_dict():
    assert json.loads(json.dumps(Plain(7), cls=DataclassJSONEncoder)) == {"x": 7, "label": "a"}


def test_nested_plain_dataclass():
    @dataclass
    class Outer:
        inner: Plain = None
    out = json.dumps({"o": Outer(Plain(2))}, cls=DataclassJSONEncoder)
    assert json.loads(out)["o"]["inner"] == {"x": 2, "label": "a"}
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_core_encoder.py tests/test_log_and_config.py` → 全 passed。

**(g) 風險與不要做的事**：`field.type in (datetime, date)` 沿用原判斷（dataclass 若用字串型別註解會不命中，
屬既有語意，動它要另開項目）。**不要**動 `_Readable.__readattrs__` 的欄位格式化規則。

---

## LIB-08（P2）`Menu.has_menuitem` 遞迴早退

**(a) 問題與影響**：`funlab/core/menu.py:Menu.has_menuitem`（約 L193-201）遇到第一個子 `Menu`
時 `return menu.has_menuitem()`——空子選單直接回傳 False，**後面的 MenuItem 兄弟全被忽略**。
實跑驗證：`root=[空子Menu, MenuItem]` → `has_menuitem()=False`（應為 True）。
影響 `appbase._finalize_admin_menu`（約 L486-490）：admin 選單若「先註冊了空佔位 Menu 再註冊真實
項目」，整個 admin 選單不會出現在主選單列。

**(b) 優先級**：P2。

**(c) 目標檔與函式**：`funlab/core/menu.py:Menu.has_menuitem`。

**(d) 完整修正後程式碼**：

```python
    def has_menuitem(self)-> bool:
        for menu in self._menus:
            if isinstance(menu, MenuItem):
                return True
            if isinstance(menu, Menu) and menu.has_menuitem():
                return True
        return False
```

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_menu_has_menuitem.py`：

```python
"""LIB-08：has_menuitem 必須檢查所有子節點，不得因空子 Menu 提前返回。"""
from funlab.core.menu import Menu, MenuItem


def test_menuitem_after_empty_submenu_counts():
    root = Menu(title="root")
    root.append(Menu(title="empty-sub"))
    root.append(MenuItem(title="real", href="/x"))
    assert root.has_menuitem() is True


def test_all_empty_returns_false():
    root = Menu(title="root")
    root.append(Menu(title="a"))
    root.append(Menu(title="b"))
    assert root.has_menuitem() is False


def test_direct_menuitem_counts():
    root = Menu(title="root")
    root.append(MenuItem(title="only", href="/"))
    assert root.has_menuitem() is True
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_menu_has_menuitem.py` → 3 passed。

**(g) 風險與不要做的事**：`MenuDivider` 是 `MenuItem` 子類，會被計入（與既有行為一致，分隔線所在
選單不該整塊消失）。**不要**改 `Menu.append` 的合併去重邏輯。

---

## LIB-09（P2）`Config.update_with_ext` 不帶 section 必崩

**(a) 問題與影響**：`funlab/core/config.py:Config.update_with_ext`（約 L211-230）末尾無條件
`setattr(self, section, update_section)`；呼叫端不傳 section 時等於 `setattr(self, None, …)`。
實跑驗證：`Config({"A":1}).update_with_ext(Config({"B":2}))` →
`TypeError: attribute name must be string, not 'NoneType'`。簽名宣示 `section=None` 可用，實際必掛。

**(b) 優先級**：P2（現有呼叫端都有帶 section；這是修 API 自洽性）。

**(c) 目標檔與函式**：`funlab/core/config.py:Config.update_with_ext`。

**(d) 完整修正後程式碼**：

```python
    def update_with_ext(self, ext_conf:Config | dict, section:str=None):
        """
        Update the configuration with external configuration.

        Args:
            ext_conf (Config | dict): The external configuration to update with.
            section (str, optional): 要更新的 section；None 表示把 ext 的第一層
                直接覆蓋到自身第一層屬性。Defaults to None.
        """
        if isinstance(ext_conf, dict):
            ext_conf = Config(ext_conf, env_file_or_values=self._env_vars)

        if section:
            if section in self:
                update_section = self.get(section, {})
            else:
                update_section = {}

            if section in ext_conf:
                ext_part = ext_conf.get(section, {})
            else:
                ext_part = ext_conf.as_dict()

            merged = dict(update_section)
            merged.update(ext_part)
            setattr(self, section, merged)
        else:
            for key, value in ext_conf.as_dict().items():
                setattr(self, key, value)
```

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_config_update_ext.py`：

```python
"""LIB-09：update_with_ext 帶/不帶 section 都要可用。"""
from funlab.core.config import Config


def test_no_section_merges_top_level():
    c1 = Config({"A": 1})
    c2 = Config({"B": 2})
    c1.update_with_ext(c2)
    assert c1.get("A") == 1 and c1.get("B") == 2


def test_section_merge_overwrites_only_that_section():
    c1 = Config({"S": {"x": 1, "y": 2}, "T": {"z": 3}})
    c2 = Config({"S": {"y": 99}})
    c1.update_with_ext(c2, section="S")
    assert c1.get("S") == {"x": 1, "y": 99}
    assert c1.get("T") == {"z": 3}


def test_dict_ext_conf_accepted():
    c1 = Config({"A": 1})
    c1.update_with_ext({"B": 2})
    assert c1.get("B") == 2
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_config_update_ext.py tests/test_log_and_config.py` → 全 passed。

**(g) 風險與不要做的事**：`_Configuable.get_config`（`funlab/core/__init__.py`）走的是
`update_with_ext(ext_conf=…, section=section)`，帶 section，行為不變。**不要**改成就地
`update()` 巢狀 deep-merge（語意改變太大，本項只做淺合併＋None 分支）。

---

## LIB-10（P1）`create_entity_table` bare `except:` 吞例外鏈

**(a) 問題與影響**：`funlab/core/dbmgr.py:DbMgr.create_entity_table`（約 L213-221）用裸
`except:` 再 `raise Exception(字串)`，不接 `from e`。實跑驗證：模組不存在時新例外
`__cause__=None`、`__context__=ModuleNotFoundError` 只是意外殘留，正式 traceback 只剩一句
拼字訊息，真實原因（模組缺失 vs 欄位錯誤 vs 連線失敗）不可區分——啟動期建表失敗的排障成本極高。

**(b) 優先級**：P1。

**(c) 目標檔與函式**：`funlab/core/dbmgr.py:DbMgr.create_entity_table`。

**(d) 完整修正後程式碼**：

```python
    def create_entity_table(self, entities_class:str):
        *module, classname = entities_class.split('.')
        module = '.'.join(module)
        try:
            entity_class = lang.get_class(classname, module)
            with self.__lock:
                entity_class.__table__.create(bind=self.get_db_engine(), checkfirst=True)
        except Exception as exc:
            raise Exception(
                f'Not found entity class {classname} from module {module} for parameter:{entities_class}'
            ) from exc
```

> 注意：`self.__lock` 在同一 class 內名稱改寫有效，維持原寫法即可。

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_dbmgr_entity_table.py`：

```python
"""LIB-10：create_entity_table 失敗時必須保留原始例外鏈。"""
import pytest

from funlab.core.config import Config
from funlab.core.dbmgr import DbMgr


def test_missing_module_preserves_cause(tmp_path):
    mgr = DbMgr(Config({"url": f"sqlite:///{tmp_path / 'e.db'}"}))
    with pytest.raises(Exception) as ei:
        mgr.create_entity_table("no.such.module.Entity")
    assert "no.such.module" in str(ei.value) or "Entity" in str(ei.value)
    assert ei.value.__cause__ is not None
    assert isinstance(ei.value.__cause__, (ModuleNotFoundError, ValueError, AttributeError))
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_dbmgr_entity_table.py` → 1 passed。

**(g) 風險與不要做的事**：例外型別仍是 `Exception`（維持相容，上游若 `except Exception` 不受影響）。
**不要**把它改成只包 ImportError（會漏掉建表 SQL 錯誤的包装語意）。

---

## LIB-11（P1）HookManager 無鎖共用 dispatch 清單

**(a) 問題與影響**：`funlab/core/hook.py:HookManager`：`register_hook` 對
`self._hooks[name]` 邊 append 邊 sort，`call_hook` 直接 for 迭代同一 list，無任何鎖。
實跑驗證：callback 內對同一 hook 自我註冊，同一次 `call_hook` 迭代到就地插入的新條目，
連續觸發 5 次（正確語意：當次 dispatch 只執行註冊當下的快照）。多執行緒下
（prewarm 背景執行緒、SSE 推送執行緒都可能註冊 hook）迭代期間 append/sort 可造成漏跑或重複跑。

**(b) 優先級**：P1（執行緒安全為硬約束）。

**(c) 目標檔與函式**：`funlab/core/hook.py:HookManager.register_hook / call_hook`。

**(d) 完整修正後程式碼**（import 區加 `import threading`）：

```python
class HookManager:
    def __init__(self, app: Any):
        self.app = app
        self.logger = log.get_logger(self.__class__.__name__, level=logging.INFO)
        self._hooks: Dict[str, List[Tuple[int, Callable[..., Any], Optional[str]]]] = defaultdict(list)
        # Guards _hooks mutation vs. dispatch-snapshot-taking.  Callbacks are
        # invoked OUTSIDE the lock so a callback may register/unregister hooks
        # without deadlocking, and never mutates the in-flight dispatch.
        self._lock = threading.RLock()

    def register_hook(self, hook_name: str, callback: Callable[..., Any], priority: int = 100,
                      plugin_name: Optional[str] = None) -> None:
        with self._lock:
            self._hooks[hook_name].append((priority, callback, plugin_name))
            self._hooks[hook_name].sort(key=lambda item: item[0])

    def call_hook(self, hook_name: str, **context: Any) -> List[HookCallResult]:
        if "app" not in context:
            context["app"] = self.app
        if request is not None:
            try:
                context.setdefault("request", request)
            except RuntimeError:
                pass
        if current_user is not None:
            try:
                context.setdefault("current_user", current_user)
            except Exception:
                pass

        with self._lock:
            entries = list(self._hooks.get(hook_name, []))

        results: List[HookCallResult] = []
        for _, callback, plugin_name in entries:
            try:
                result = callback(context)
                results.append(HookCallResult(hook_name, callback, result))
            except Exception as exc:
                self.logger.error(
                    "Hook %s from %s failed: %s",
                    hook_name,
                    plugin_name or getattr(callback, "__module__", "unknown"),
                    exc,
                )
        return results
```

（`render_hook`、`list_hooks` 不必改：`list_hooks` 若要嚴格可加同款鎖，屬可選。）

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_hook_manager_threadsafe.py`：

```python
"""LIB-11：dispatch 使用註冊快照；回呼內再註冊不得污染當次 dispatch。"""
import threading

from funlab.core.hook import HookManager


def test_self_registering_callback_fires_once_per_dispatch():
    fired = []
    hm = HookManager(app=None)

    def self_spawner(ctx):
        fired.append(1)
        if len(fired) < 5:
            hm.register_hook("h", self_spawner)

    hm.register_hook("h", self_spawner)
    hm.call_hook("h")
    assert len(fired) == 1


def test_priority_order_still_applies():
    order = []
    hm = HookManager(app=None)
    hm.register_hook("h", lambda ctx: order.append("late"), priority=200)
    hm.register_hook("h", lambda ctx: order.append("early"), priority=50)
    hm.call_hook("h")
    assert order == ["early", "late"]


def test_concurrent_register_and_call_no_errors():
    hm = HookManager(app=None)
    hm.register_hook("h", lambda ctx: None)
    errors = []

    def reg_worker():
        try:
            for i in range(300):
                hm.register_hook("h", lambda ctx: None, priority=i)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    def call_worker():
        try:
            for _ in range(300):
                hm.call_hook("h")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=reg_worker), threading.Thread(target=reg_worker),
          threading.Thread(target=call_worker), threading.Thread(target=call_worker)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_hook_manager_threadsafe.py tests/test_plugin_and_notification.py tests/test_plugin_lifecycle.py` → 全 passed。

**(g) 風險與不要做的事**：快照語意代表「dispatch 進行中新註冊的 hook 從**下一次** call_hook 生效」，
這是業界慣例（Django/pytest 同），要在 PR description 寫明。**不要**在鎖內直接呼叫 callback
（會死鎖：callback 內 register_hook 同一 RLock 尚可，但 SSE/DB 慢回呼會卡死全部註冊者）。

---

## LIB-12（P2）PollingNotificationProvider `_dismissed_global` 無界成長

**(a) 問題與影響**：`funlab/core/appbase.py:PollingNotificationProvider.dismiss_items/dismiss_all`
把使用者 dismiss 過的**全域 id 永久留在** `_dismissed_global[user]`。全域佇列本身有
`maxlen=200`（舊訊息早已淘汰），但 dismissed 集合只增不減。實跑驗證：10,000 輪
`add_global + dismiss_all` 後 `_dismissed_global[user1]` 殘留 **10,000** 個 id——長期運行的
排程/系統通知環境是穩定記憶體洩漏（金融伺服器 24/7 常見）。

**(b) 優先級**：P2。

**(c) 目標檔與函式**：`funlab/core/appbase.py:PollingNotificationProvider.dismiss_items / dismiss_all`。

**(d) 完整修正後程式碼**（只換這兩個方法；id 單調遞增、全域佇列有限長，被淘汰的 id 永不復返，
因此把 dismissed 集合修剪為「∩ 目前佇列存活 id」是無損的）：

```python
    def dismiss_items(self, user_id: int, item_ids: list[int]) -> None:
        """Explicitly remove specific notifications for a user."""
        with self._lock:
            id_set = set(item_ids)
            # Mark global notifications as dismissed, then prune to IDs that are
            # still alive in the bounded global deque: evicted IDs can never be
            # re-delivered (IDs are monotonic), so remembering them is a leak.
            live_ids = {item["id"] for item in self._global}
            merged = self._dismissed_global[user_id] | id_set
            self._dismissed_global[user_id] = merged & live_ids
            # Remove per-user notifications outright
            user_store = self._per_user.get(user_id, {})
            for nid in id_set:
                user_store.pop(nid, None)

    def dismiss_all(self, user_id: int) -> None:
        """Explicitly remove all notifications for a user."""
        with self._lock:
            # Only remember dismissals for global IDs still in the bounded deque.
            live_ids = {item["id"] for item in self._global}
            self._dismissed_global[user_id] = self._dismissed_global[user_id] & live_ids | live_ids
            # Clear all per-user notifications
            self._per_user.pop(user_id, None)
            # Reset delivery cursors
            self._last_delivered_global.pop(user_id, None)
            self._last_delivered_user.pop(user_id, None)
```

> 修剪正當性：全域 id 單調遞增、佇列有限長（`deque(maxlen=max_global)`），被淘汰的 id 永不復返，
> 因此把 dismissed 集合修剪為「與目前佇列存活 id 的交集」不損失任何遮蔽語意，且集合有界。

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_notification_bounds.py`：

```python
"""LIB-12：dismiss 記錄必須有界（全域佇列淘汰後不再記憶已死 id）。"""
from funlab.core.appbase import PollingNotificationProvider


def test_dismissed_global_set_stays_bounded():
    p = PollingNotificationProvider(max_global=200)
    for i in range(10_000):
        p.add_global("t", f"m{i}")
        p.dismiss_all(1)
    assert len(p._dismissed_global[1]) <= 200


def test_dismiss_items_still_hides_live_global():
    p = PollingNotificationProvider(max_global=200)
    p.add_global("t", "one")
    nid = p._global[-1]["id"]
    p.dismiss_items(1, [nid])
    assert p.fetch_unread(1) == []


def test_dismiss_all_hides_current_globals():
    p = PollingNotificationProvider(max_global=200)
    for i in range(10):
        p.add_global("t", f"m{i}")
    p.dismiss_all(1)
    assert p.fetch_unread(1) == []
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_notification_bounds.py tests/test_plugin_and_notification.py` → 全 passed。

**(g) 風險與不要做的事**：本 provider 是單程序記憶體實作，語意上「重新啟動後 dismiss 記憶消失」本來就是現況，
不要試圖持久化。**不要**改 `_per_user` 的 eviction（min(id) 淘汰）演算法，本項不動它。

---

## LIB-13（P2）`PerformanceTracker` 共享計數無鎖（防禦性加固）

**(a) 問題與影響**：`funlab/utils/perf_track.py:PerformanceTracker._update_stats /
record_cache_hit / record_cache_miss` 對共享 dict 做讀-改-寫（`stats['calls'] += 1`、
`total_time += elapsed`、`avg_time = total/calls`），無鎖。`default_tracker` 是模組級單例，
Flask 多執行緒 + prewarm 背景執行緒可並發命中。誠實說明：探針（8 執行緒 × 20,000 次，
switchinterval=1e-6）**未觀察到計數丟失**（CPython GIL 讓單條 dict 存取僥倖安全），
但「複合更新無鎖」屬於共享可變狀態硬約束的反模式，且 free-threaded Python（3.13t）下會成真。
定位：防禦性加固，非已觀察到的故障。

**(b) 優先級**：P2。

**(c) 目標檔與函式**：`funlab/utils/perf_track.py:PerformanceTracker.__init__ / _update_stats / record_cache_hit / record_cache_miss`。

**(d) 完整修正後程式碼**（這四個方法整段替換；其餘方法不動）：

```python
class PerformanceTracker:
    """統一的性能追蹤器"""

    def __init__(self, enable_cache_stats: bool = True):
        # All mutations of self.stats / self.cache_stats go through _lock so
        # concurrent instrumented threads (Flask workers + prewarm threads)
        # cannot interleave read-modify-write updates.
        self._lock = threading.Lock()
        self.stats = defaultdict(lambda: {
            'calls': 0,
            'total_time': 0.0,
            'min_time': float('inf'),
            'max_time': 0.0,
            'avg_time': 0.0
        })
        self.cache_stats = {'hits': 0, 'misses': 0} if enable_cache_stats else None

    def record_cache_hit(self):
        """記錄快取命中"""
        if self.cache_stats:
            with self._lock:
                self.cache_stats['hits'] += 1

    def record_cache_miss(self):
        """記錄快取未命中"""
        if self.cache_stats:
            with self._lock:
                self.cache_stats['misses'] += 1

    def _update_stats(self, func: Callable, elapsed: float, args: tuple):
        """更新統計資料"""
        func_name = self._get_qualified_name(func, args)

        with self._lock:
            stats = self.stats[func_name]
            stats['calls'] += 1
            stats['total_time'] += elapsed
            stats['min_time'] = min(stats['min_time'], elapsed)
            stats['max_time'] = max(stats['max_time'], elapsed)
            stats['avg_time'] = stats['total_time'] / stats['calls']
```

檔案頂部 import 區加 `import threading`。

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_perf_track_lock.py`：

```python
"""LIB-13：PerformanceTracker 併發更新計數正確、不拋錯。"""
import threading

from funlab.utils.perf_track import PerformanceTracker


def test_concurrent_updates_count_exactly():
    t = PerformanceTracker()
    N, T = 5_000, 8

    def worker():
        f = lambda: None
        for _ in range(N):
            t._update_stats(f, 0.0001, ())

    threads = [threading.Thread(target=worker) for _ in range(T)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert t.stats["<lambda>"]["calls"] == N * T


def test_cache_hit_miss_counts():
    t = PerformanceTracker()
    for _ in range(100):
        t.record_cache_hit()
        t.record_cache_miss()
    assert t.cache_stats == {"hits": 100, "misses": 100}
    assert t.get_cache_hit_rate() == 0.5
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_perf_track_lock.py` → 2 passed。

**(g) 風險與不要做的事**：鎖只覆蓋計數區段，**不要**把被追蹤函數執行也圈進鎖內（會把並行工具變成串行）。
`reset_stats` 已重建 `cache_stats`，若要嚴格可在重建外層加鎖，非必須。

---

## LIB-14（P1）plugin 元資料快取無效期：幽靈 plugin 復活

**(a) 問題與影響**：`funlab/core/plugin_manager.py:PluginLoader.discover_plugins`（約 L159-222）
命中檔案快取時（步驟 2）**原樣還原所有中繼資料，不檢查對應 entry point 是否仍然存在**。
實跑驗證：向 `plugin_cache.json` 灌入一筆已不存在 entry point 的 `GhostPlugin`
（load_mode=startup, entry_point=gone.module:Ghost），`discover_plugins('probe_group_x')`
回傳 `['GhostPlugin']`——實務情境：移除/改名 plugin 後快取未清，啟動仍按幽靈中繼資料嘗試
`load_plugin_class`（`_entry_points` 查無時拋 RuntimeError，被 manager 轉 ERROR 狀態），
且 `_log_plugin_stats` 出現殭屍條目、依賴解析把幽靈當節點參與拓撲排序。

**(b) 優先級**：P1。

**(c) 目標檔與函式**：`funlab/core/plugin_manager.py:PluginLoader.discover_plugins`。

**(d) 完整修正後程式碼**：

```python
    def discover_plugins(self, group: str, force_refresh: bool = False) -> Dict[str, PluginMetadata]:
        """Discover plugins for an entry-point group and cache their metadata.

        Note: the old ``@lru_cache`` approach was removed because
        ``force_refresh=True`` must bypass stale results.

        ``entry_points(group=group)`` only reads distribution metadata. It does
        not import plugin modules, so it is safe to execute on every startup.
        We always enumerate live ``EntryPoint`` objects first so
        ``load_plugin_class()`` can call ``ep.load()`` reliably even when the
        richer metadata comes from the file cache.

        Cached metadata is only honoured for entry points that are STILL live;
        stale entries (uninstalled/renamed plugins) are dropped and the pruned
        view is re-cached so ghost plugins can never resurrect from disk.
        """
        cache_key = self.cache.get_cache_key(group)

        # Step 1: enumerate live entry points (fast, no imports).
        live_entry_points = entry_points(group=group)
        for ep in live_entry_points:
            self._entry_points[ep.name] = ep
        live_names = set(self._entry_points.keys())

        # Step 2: try the file cache for enriched ``PluginMetadata``.
        if not force_refresh:
            cached_data = self.cache.load_cache(cache_key)
            if cached_data:
                stale = {name for name in cached_data if name not in live_names}
                if stale:
                    self.logger.warning(
                        f"Dropping {len(stale)} stale plugin metadata entr(ies) from cache "
                        f"for group '{group}': {sorted(stale)}"
                    )
                    cached_data = {k: v for k, v in cached_data.items() if k in live_names}
                    # Persist the pruned view so the ghosts stay dead.
                    self.cache.save_cache(cache_key, cached_data)
                if cached_data:
                    self.logger.debug(f"Loading plugin metadata from cache for group: {group}")
                    field_names = set(PluginMetadata.__dataclass_fields__.keys())

                    def _make_meta(d: Dict[str, Any]) -> PluginMetadata:
                        filtered = {k: v for k, v in d.items() if k in field_names}
                        # Backward compatibility for cache entries written before ``load_mode``.
                        if 'load_mode' not in filtered:
                            if d.get('immediate_load', False):
                                filtered['load_mode'] = 'startup'
                            elif not d.get('lazy_load', True):
                                filtered['load_mode'] = 'startup'
                        return PluginMetadata(**filtered)

                    return {name: _make_meta(metadata)
                            for name, metadata in cached_data.items()}

        # Step 3: live discovery by reading ``pyproject.toml`` metadata.
        self.logger.progress(f"Discovering plugins for group: {group}", key='discover_plugins')

        plugins = {}
        for entry_point in live_entry_points:
            try:
                # Do not import the plugin class yet; only collect metadata.
                metadata = self._extract_metadata(entry_point)
                plugins[entry_point.name] = metadata
            except Exception as e:
                self.logger.error(f"Failed to extract metadata from {entry_point.name}: {e}")
                self.logger.end_progress(key='discover_plugins')

        # Cache the discovery result.
        cache_data = {name: metadata.__dict__ for name, metadata in plugins.items()}
        self.cache.save_cache(cache_key, cache_data)
        self.logger.end_progress(f"Discovered {len(plugins)} plugins in group {group}.")

        return plugins
```

> 另刪去原 L213 多餘的 `self.logger.error("")` 空行輸出（上面版本已不含）。

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_plugin_cache_staleness.py`：

```python
"""LIB-14：快取中已無對應 entry point 的 plugin 中繼資料必須被淘汰。"""
import funlab.core.plugin_manager as pm
from funlab.core.plugin_manager import PluginLoader


def test_ghost_metadata_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "entry_points", lambda group: [])
    loader = PluginLoader(tmp_path)
    key = loader.cache.get_cache_key("g")
    loader.cache.save_cache(key, {"Ghost": {
        "name": "Ghost", "version": "0.0.0", "description": "", "author": "",
        "dependencies": [], "optional_dependencies": [], "security_mode": "public",
        "provides_security": False, "load_mode": "startup", "auto_enable": True,
        "min_python_version": "3.11", "entry_point": "gone.module:Ghost",
        "config_schema": {}}})

    found = loader.discover_plugins("g", force_refresh=False)
    assert found == {}
    # 重新讀盤：幽靈不得復活
    assert loader.cache.load_cache(key) in ({}, None)


def test_live_metadata_survives_filter(tmp_path, monkeypatch):
    from importlib.metadata import EntryPoint

    ep = EntryPoint(name="Real", value="realpkg.module:Real", group="g")
    monkeypatch.setattr(pm, "entry_points", lambda group: [ep])
    loader = PluginLoader(tmp_path)
    key = loader.cache.get_cache_key("g")
    loader.cache.save_cache(key, {"Real": {
        "name": "Real", "load_mode": "startup", "dependencies": [],
        "optional_dependencies": [], "security_mode": "public",
        "provides_security": False, "entry_point": "realpkg.module:Real"}})

    found = loader.discover_plugins("g", force_refresh=False)
    assert "Real" in found
    assert found["Real"].load_mode == "startup"
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_plugin_cache_staleness.py tests/test_plugin_manager_security_mode.py` → 全 passed。
啟動實測另跑：把 `RESCAN_PLUGINS = true` 放進 app 設定可強制 `force_refresh`，驗證正常啟動路徑不受影響。

**(g) 風險與不要做的事**：`self._entry_points` 跨多次 `discover_plugins(不同 group)` 累積——若兩 group
有同名 plugin 屬上游部署問題，本項不處理。不要快取失效時直接 `invalidate_cache()` 全檔刪除
（會連別 group 的快取一起清掉，本實作只修剪該 key）。**不要**改快取檔案格式。

---

## LIB-15（P1）`unload_plugin` 對未載入 plugin 觸發被吞的 `NameError`

**(a) 問題與影響**：`funlab/core/plugin_manager.py:ModernPluginManager.unload_plugin`
（約 L763-798）：`instance` 只在 `plugin_info.instance is not None` 分支內賦值，但後面清除
app 映射的區塊（約 L779-786）**無條件**引用 `instance`——對從未載入的 plugin 呼叫 unload 時
走 `NameError`，又被 `except Exception: pass` 完全吞掉。實跑驗證：`unload_plugin('ghost',
instance=None)` 回傳 True 且無任何日誌；後續 `plugin_info.instance = None` 等收尾行實際被跳過。

**(b) 優先級**：P1。

**(c) 目標檔與函式**：`funlab/core/plugin_manager.py:ModernPluginManager.unload_plugin`。

**(d) 完整修正後程式碼**：

```python
    def unload_plugin(self, plugin_name: str) -> bool:
        """Unload a plugin instance and reset runtime state."""
        with self._lock:
            plugin_info = self.plugins.get(plugin_name)
            if not plugin_info:
                return False

            instance = plugin_info.instance
            try:
                if instance is not None:
                    if hasattr(instance, 'stop'):
                        instance.stop()
                    elif hasattr(instance, 'unload'):
                        instance.unload()

                    # Remove any mapping from the Flask app that points to this instance.
                    try:
                        name = getattr(instance, 'name', None)
                        if name and self.app.plugins.get(name) is instance:
                            del self.app.plugins[name]
                        if plugin_name in self.app.plugins and self.app.plugins.get(plugin_name) is instance:
                            del self.app.plugins[plugin_name]
                    except (KeyError, TypeError, AttributeError) as e:
                        self.logger.debug(
                            f"app.plugins mapping cleanup skipped for {plugin_name}: {e}")

                plugin_info.instance = None
                plugin_info.state = PluginState.UNLOADED
                plugin_info.error_message = None
                self._active_plugins.discard(plugin_name)
                return True
            except Exception as e:
                plugin_info.state = PluginState.ERROR
                plugin_info.error_message = str(e)
                self._active_plugins.discard(plugin_name)
                self.logger.error(f"Failed to unload plugin {plugin_name}: {e}")
                return False
```

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_plugin_manager_unload.py`：

```python
"""LIB-15：unload_plugin 對未載入 plugin 必須正常收尾（不再被 NameError 靜默中斷）。"""
from unittest.mock import MagicMock

from funlab.core.plugin_manager import (ModernPluginManager, PluginInfo,
                                        PluginMetadata, PluginState)


def _mgr():
    app = MagicMock()
    app.plugins = {}
    return ModernPluginManager(app)


def test_unload_never_loaded_plugin_finishes_state_reset():
    mgr = _mgr()
    info = PluginInfo(metadata=PluginMetadata(name="ghost"))
    info.state = PluginState.LOADED  # 模擬元資料在、instance 空的狀態
    mgr.plugins["ghost"] = info
    assert mgr.unload_plugin("ghost") is True
    assert info.state == PluginState.UNLOADED
    assert info.instance is None
    assert info.error_message is None


def test_unload_unknown_plugin_returns_false():
    assert _mgr().unload_plugin("nope") is False
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_plugin_manager_unload.py tests/test_plugin_manager_security_mode.py tests/test_plugin_lifecycle.py` → 全 passed。

**(g) 風險與不要做的事**：外層 broad `except Exception`（回報錯誤用）保留，但內層清理的
`except: pass` 已收斂為 `(KeyError, TypeError, AttributeError)` + debug log。
**不要**順手把 `cleanup()` 的反向卸載順序改掉。

---

## LIB-16（P2）prewarm：`run()` 之後註冊的任務永久 pending 且無警告

**(a) 問題與影響**：`funlab/core/prewarm.py:register` 不檢查 `_run_called`；`run()` 有
`_run_called` 守衛只執行一次。lazy plugin 在首請求才實例化、在
`register_prewarm_tasks()` 註冊——此時 `run()` 早已執行，任務永久 `pending`。
實跑驗證：run() 後註冊的任務 `status='pending'`、永不執行、**零警告**。
（本輪只加警告不改變排程語意；「run 後立即執行」屬行為變更，需另案評估阻塞風險。）

**(b) 優先級**：P2。

**(c) 目標檔與函式**：`funlab/core/prewarm.py:register`。

**(d) 完整修正**：在 `register()` 的 `with _lock:` 區塊**結束後**（函數末）加入：

```python
    with _lock:
        if name in _entries:
            if skip_if_exists:
                _logger.debug("Deferred import %r already registered  skipped.", name)
                return
            if not replace:
                raise ValueError(
                    f"Deferred import {name!r} already registered. "
                    "Use skip_if_exists=True (shared resources) or replace=True (tests)."
                )
        _entries[name] = _Entry(
            name=name, func=func, blocking=blocking, delay=delay,
            category=category, resource_key=resource_key, owner=owner, budget_sec=budget_sec
        )
        run_already = _run_called
        _logger.debug("Registered deferred import %r (blocking=%s, delay=%.1fs, category=%s, resource_key=%s)",
                      name, blocking, delay, category, resource_key)

    if run_already:
        _logger.warning(
            "Deferred import %r registered AFTER prewarm.run() was called; "
            "it will never execute (typical for lazily-loaded plugins). "
            "Move registration into a startup-mode plugin if it matters.",
            name,
        )
```

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_prewarm_late_register.py`：

```python
"""LIB-16：run() 後註冊必須留下 WARNING，且既有首次 run 語意不變。"""
import logging

import pytest

import funlab.core.prewarm as pw


@pytest.fixture(autouse=True)
def _clean():
    pw.reset()
    yield
    pw.reset()


def test_late_register_warns(caplog):
    pw.register("early", lambda: None, blocking=True)
    pw.run()
    with caplog.at_level(logging.WARNING, logger="funlab.core.prewarm"):
        pw.register("late", lambda: None, blocking=True)
    assert any("after prewarm.run()" in r.message for r in caplog.records)
    assert pw.status()["late"]["status"] == "pending"


def test_normal_register_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="funlab.core.prewarm"):
        pw.register("ok", lambda: None)
    assert not any("after prewarm.run()" in r.message for r in caplog.records)
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_prewarm_late_register.py tests/test_prewarm.py` → 全 passed。

**(g) 風險與不要做的事**：**不要**改成「run 後註冊立即同步執行」——lazy plugin 首請求路徑可能
因此卡住數十秒（日誌實測 calendar 預熱近 100s），必須由 dev-arch 另行裁決。caplog 的 logger 名以
`funlab.utils.log.get_logger(__name__)` 實作為準（funlab.core.prewarm）；若捕獲不到，改用
`caplog.records` 全域篩選。

---

## LIB-17（P2）`create_registry_tables` 以 `id()` 去重，id 可被重用

**(a) 問題與影響**：`funlab/core/dbmgr.py:DbMgr.create_registry_tables`（約 L199-211）用
`id(sa_registry)` 記在 `_created_registries`。Python 物件回收後 id 可分配給新物件——新 registry
被誤判「已建立過」而靜默略過建表。實跑驗證：50 次建立/回收出現 **46 次 id 重用**（重用現象客觀存在；
端到端「靜默略過」情境探針 P16b 未復現，因 `create_all` 本身 checkfirst 冪等——本項屬預防性修正）。

**(b) 優先級**：P2。

**(c) 目標檔與函式**：`funlab/core/dbmgr.py:DbMgr.create_registry_tables`。

**(d) 完整修正後程式碼**（頂部 import 區加 `import weakref`）：

```python
    def create_registry_tables(self, sa_registry):
        # Prevent concurrent create_all calls and avoid repeated creation for
        # the same registry which can lead to "deque mutated during iteration"
        # when SQLAlchemy dispatch listeners are modified during startup.
        # WeakSet (not id()) : an id can be recycled after GC, which would make
        # a brand-new registry look "already created"; a WeakKey entry simply
        # disappears with its registry.  create_all itself is checkfirst-idempotent.
        with self.__lock:
            if not hasattr(self, '_created_registries'):
                self._created_registries: "weakref.WeakSet" = weakref.WeakSet()
            if sa_registry in self._created_registries:
                return
            sa_registry.metadata.create_all(self.get_db_engine())
            self._created_registries.add(sa_registry)
```

**(e) 完整 pytest 測試程式碼** — 新檔 `tests/test_dbmgr_registry_dedup.py`：

```python
"""LIB-17：create_registry_tables 去重以物件身份為準，不受 id 重用影響。"""
import gc

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import registry as sa_registry

from funlab.core.config import Config
from funlab.core.dbmgr import DbMgr


def _mgr(tmp_path):
    return DbMgr(Config({"url": f"sqlite:///{tmp_path / 'r.db'}"}))


def test_same_registry_creates_once(tmp_path):
    mgr = _mgr(tmp_path)
    reg = sa_registry()
    Base = reg.generate_base()

    class T(Base):
        __tablename__ = "t_once"
        id = sa.Column(sa.Integer, primary_key=True)

    mgr.create_registry_tables(reg)
    mgr.create_registry_tables(reg)  # must not raise / re-create


def test_recycled_registry_gets_created(tmp_path):
    from sqlalchemy import inspect as sa_inspect
    mgr = _mgr(tmp_path)

    def make(table_name):
        reg = sa_registry()
        Base = reg.generate_base()
        cls = type("M", (Base,), {"__tablename__": table_name,
                                  "id": sa.Column(sa.Integer, primary_key=True)})
        mgr.create_registry_tables(reg)
        del reg, Base, cls
        gc.collect()

    make("t_a")
    make("t_b")
    names = set(sa_inspect(mgr.get_db_engine()).get_table_names())
    assert {"t_a", "t_b"} <= names
```

**(f) 驗證**：`cd funlab-libs && python -m pytest -q tests/test_dbmgr_registry_dedup.py tests/test_dbmgr.py` → 全 passed。

**(g) 風險與不要做的事**：`sqlalchemy.orm.registry` 支援 weakref（實測可建 WeakSet）。
**不要**改成無條件每次 `create_all`（啟動 cost 與 listener 併發問題就是本快取存在的原因）。

---

## LIB-18（P2）`slope2angle` 宣告與回傳型別不符

**(a) 問題與影響**：`funlab/core/jinja_filters.py:slope2angle` 宣告 `->float`，實跑回傳
`'45.000'`（str）與 `'NA'`。Jinja 端顯示正確，但任何 Python 呼叫端拿它做計算會出錯
（字串參與算術）。

**(b) 優先級**：P2（修註解不改行為，避免動模板端顯示格式）。

**(c) 目標檔與函式**：`funlab/core/jinja_filters.py:slope2angle`。

**(d) 完整修正後程式碼**：

```python
def slope2angle(slope:float)->str:
    """
    Convert the slope value to the angle in degrees (formatted string for
    template display; returns 'NA' when slope is None).

    Args:
        slope (float): The slope value.

    Returns:
        str: The angle in degrees formatted to 3 decimals, or 'NA'.
    """
    if slope is None:
        return 'NA'
    else:
        degree = math.degrees(math.atan(slope))
        return f'{degree:,.3f}'
```

**(e) 測試**：使用 LIB-03 的 `tests/test_jinja_filters.py` 中 `test_slope2angle_*` 兩條。

**(f) 驗證**：同 LIB-03。

**(g) 風險與不要做的事**：**不要**改成回傳 float——既有的圖表/模板都吃格式化字串。

---

# 附錄 A：新發現缺陷的驗證方式匯總（探針輸出原文）

探針存於 architect scratch（`probe_libs_new.py`/`probe2.py`/`probe3.py`/`probe4.py`，
僅 tmp sqlite/tmp 目錄）。2026-09-27 於 `~/.venv/fund13` 實跑：

| 條目 | 探針輸出（節錄） |
|---|---|
| LIB-01 | `nested session: rows after outer abort = [1, 2] (should be [])` |
| LIB-02 | `現況=postgresql+psycopg://fund:VeryL*@127.0.0.1:5432/fund13 / 修法=postgresql+psycopg://fund:***@127.0.0.1:5432/fund13` |
| LIB-03 | `month->%q: {3: '2', 6: '3', 9: '4', 12: '5'}（正確應為 1,2,3,4）` |
| LIB-04 | `PEP604 validators=[] vs Optional[] validators=['Optional']` |
| LIB-07 | `TypeError: must be called with a dataclass type or instance` |
| LIB-08 | `has_menuitem()=False (應為 True)` |
| LIB-09 | `TypeError: attribute name must be string, not 'NoneType'` |
| LIB-10 | `__cause__=None __context__=ModuleNotFoundError`（例外鏈不可用） |
| LIB-11 | `self-spawning callback fired 5 次（快照迭代應為 1 次）` |
| LIB-12 | `10000 輪 add+dismiss 後 _dismissed_global[user1] 殘留 10000 個 id（全域佇列上限僅 200）` |
| LIB-13 | `expected 160000, got 160000, lost 0`（未观察到丢失→定位为加固） |
| LIB-14 | `group 'probe_group_x' 實際 entry points=0，快殘留幽靈 plugin → discover 結果=['GhostPlugin'] load_mode=startup` |
| LIB-15 | `unload_plugin('ghost', instance=None) 回傳 True；原始碼引用未定義的 instance 變數，NameError 被 except-pass 吞掉` |
| LIB-16 | `run() 後註冊的 late 任務 status=pending，executed=['early']（late 永不執行且無警告）` |
| LIB-17 | `50 次建立，出現 46 次相同 id`；P16b 端到端未復現（如實記錄） |
| LIB-18 | `slope2angle(1.0) -> '45.000' type=str（宣告 ->float）` |

# 附錄 B：審查過但**未**立項的疑點（誠實記錄）

1. `funlab/utils/dtts.py:quarter_start_end_date` 的月末表 `30 if month in (4,6,9,11) else 31`
   乍看是 bug，實跑 12 個月全部回傳正確季末（3/31、6/30、9/30、12/31）——**不成立，未立項**。
2. `funlab/utils/url.py:get_request_url` 使用 `request.body`（Werkzeug Request 無此屬性）——
   驗證探針執行前使用者駁回了該命令，**未經實跑驗證，故不寫入方案**；建議後續以
   `request.get_data()` 補驗證再立項。
3. `_Readable.__readattrs__` 以 `isinstance(getattr(self,p), property)` 偵測 property——
   實跑驗證同樣因命令被駁回未完成，未寫入。
4. `funlab/core/dbmgr.py:NoDatabaseSessionExcption` 類名拼字錯誤但全倉無引用——改名屬 API
   破壞性動作，未立項。
5. `funlab/core/config.py:Config.__setitem__` 對不存在 key 只拋裸 `ValueError()`——影響小，未立項。
6. `Config` 支援 `{{ENV_VAR:NAME}}` 與 `{{a.b}}` 內部引用，替換後 `_env_vars` 仍留在實例——
   與 funlab-flaskr H1 修復相關但屬該倉範圍，未在本方案重複立項。
