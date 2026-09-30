# Plugin 體系全景與生命週期（現行實作）

本文件是 fund13 全部「plugin 式動態擴充」機制的單一權威文件，合併並取代舊
`PLUGIN_LIFECYCLE_*` 系列（2026-09-30 整併：舊英文系列引用不存在的
`EnhancedViewPlugin`/`EnhancedServicePlugin`、死連結與過時口徑，全部退場）。
內容逐一核對當前的 `funlab/core/plugin.py`、`plugin_manager.py`、`hook.py`、
`appbase.py`、`prewarm.py`、`model_hook.py` 與各消費倉原始碼。
開發新 plugin 的照抄指南見 `PLUGIN_DEVELOPMENT_GUIDE.md`；prewarm 細節見
`PREWARM開發使用指南.md`。

## 0. 系統中的三個 plugin 家族

同為「動態發現＋載入」，三者生命週期語意不同、程式碼互不共用：

| 家族 | entry-point group / 協定 | 載入者 | 生命週期語意 | 現有成員 |
|---|---|---|---|---|
| A 應用 plugin | `funlab_plugin` ＋ `[tool.funlab_plugin_metadata.<Name>]` | `ModernPluginManager`（本文件 §1–§4） | 進程級服務/View，啟動期實例化 | AuthView、SchedService、SSEService、QuoteService、FundMgrView、OptionView；內建 PluginManagerView 由 FunlabFlask 直接構造，**不走 manager** |
| B 排程任務 | `funlab_sched_task`（`SchedTask` dataclass，非 Plugin 子孫） | `SchedService._load_tasks()` 自行 `entry_points()` 掃描 | 可被排程**重複執行**的任務，帶表單參數重播 | finfun-finfetch Fetch*×6、finfun-factor×2、finfun-fundmgr BookKeeping/ReturnReconcile、finfun-quantanlys×5 |
| C 券商 UTIF | 模組級 duck-typing：`finfun.broker.{name}` 需含 `create_utif_quote_adapter`/`utif_login_quote`(/`utif_quote_probe`) 或 `create_utif_trade_adapter`/`utif_login_trade` | `finfun.utif.broker_plugin.BrokerPluginLoader`＋`finfun.quotesvcs.pool.discovery.AutoDiscovery` | 可登入/登出的外部連線資源，運行期動態加入 Pool | finfun-broker-sino/fubon/yuanta/capital |

家族 A 的「全部 plugin 註冊完成」目前沒有正式訊號：SchedService 與 QuoteService 皆在
`plugin_after_init` 回呼中以 `plugin_name in {'pluginmanager','PluginManagerView'}`
字串匹配推斷（脆弱握手，已列改善項）。

## 1. 兩層狀態機（家族 A）

### 1.1 Plugin 物件狀態（`plugin.py:PluginLifecycleState`）

`INITIALIZING → READY → STARTING → RUNNING → STOPPING → STOPPED`，另有 `RELOADING` 與 `ERROR`。

| 方法 | 前置檢查 | 序列（成功路徑） | 例外路徑 |
|---|---|---|---|
| `start()` | 已 RUNNING→直接 True；處於 STARTING/STOPPING/RELOADING→警告並 False（防重入） | STARTING → 全域 hook `plugin_before_start` → 實例 hook `before_start` → `_on_start()` → RUNNING → 實例 hook `after_start` → 全域 hook `plugin_after_start`，回 True | ERROR、`_health.is_healthy=False`、hook `on_error` + `_on_error()`，回 False |
| `stop()` | 已 STOPPED→True | STOPPING → `plugin_before_stop` → `before_stop` → `_run_stop_safely()` → STOPPED → `after_stop` → `plugin_after_stop` | 同上 |
| `reload()` | RELOADING 中→警告 False | `plugin_before_reload` → `stop()`（失敗即中止）→ `_on_reload()`（預設重讀設定+選單）→ `start()` → `plugin_after_reload` | 同上 |
| `unload()` | — | `_on_unload()` → `stop()` | — |
| `health_check()` | ERROR 狀態直接 False | `_perform_health_check()` → 更新 `_health` | 例外時記 error_count 並 False |

- `Plugin.__init__` 副官序：建 Blueprint（含性能中介層＋default policy 中介層）→
  讀設定 → `setup_menus()` → `_on_init()` → `register_prewarm_tasks()` →
  全域 hook `plugin_after_init` → 轉 READY。
- `start/stop/reload` 全程持 `self._lock`（RLock）：`_on_start` 等回呼在鎖內執行，
  **不要在其中做長阻塞**（會卡住同 plugin 的其他生命週期操作）。
- `_run_stop_safely(timeout=5.0)`：`_on_stop()` 在獨立 daemon 執行緒執行，`_stop_executed`
  旗標保證最多執行一次；逾時只警告不等待。
  ✅ **已修復（PR fix/plugin-lifecycle-p1）**：舊版 `reload()→stop()` 置
  `_stop_executed=True` 而 `start()` 不重置，此後該實例的 `stop()` 永久跳過
  `_on_stop`。現行 `start()` 進入 STARTING 時重置旗標，reload 之後的 `stop()`
  照常執行 `_on_stop`。SchedService 的 SCH-06 自保碼保留作防禦。

### 1.2 Manager 層狀態（`plugin_manager.py:PluginState`）

`UNLOADED → LOADING → LOADED → ACTIVE`；載入例外→`ERROR`；安全模式擋下→`DISABLED`。

`ModernPluginManager` 流程（`register_plugins`）：

1. `PluginLoader.discover_plugins(group)`：`entry_points(group='funlab_plugin')` 列舉
   （只讀 metadata，不 import）；pyproject 的 `[tool.funlab_plugin_metadata.<Name>]`
   提供 `dependencies / optional_dependencies / load_mode / security_mode / provides_security`
   （`_apply_plugin_meta` **只映射這五鍵**；PluginMetadata 其餘欄位為保留未用）。
   結果寫入 `.plugin_cache/plugin_cache.json`；已卸裝的幽靈列會被自動剔除並重寫快取。
2. `PluginDependencyResolver.resolve_load_order`：缺硬-dependency 者剔除（連坐級聯）；
   軟-dependency 缺席只降級；DFS 拓撲排序（字母序起點保證確定性；迴圈→`PluginCycleError`）。
3. `provides_security=True` 的 startup plugin 先載（安全 provider 優先接線），其餘
   `load_mode='startup'` 同步載入；`load_mode='lazy'`（預設）留待首次 `get_plugin()` 觸發。
4. **fail-closed（AUTH-03）**：`load_mode='startup'` 且 `provides_security=True` 的元件
   載入失敗→丟 `SecurityProviderStartupError`，拒絕以 PUBLIC 模式啟動。
   非安全元件失敗一律僅記錄、不拒絕啟動（DISABLED/ERROR 進 stats）。
5. 每個載入執行 `_register_plugin_to_flask`：登記 `app.plugins[name]`、`register_blueprint`
   （首請求後註冊失敗→`_blueprint_registered=False` 降級警告）、`entities_registry` 建表
   （app 無 dbmgr 時明確跳過並警告）、`ISecurityProvider`（runtime-checkable Protocol，
   有 `login_manager` property 即符合）接線→替換佔位 login_manager、轉
   `SecurityMode.SECURED`／`authorization_enabled=True`；第二個 provider 只留路由並警告。
6. `Plugin.__init__` 也寫 `app.extensions[self.name]`；`unload_plugin`/`cleanup` 現與
   `app.plugins` 對稱清除 extensions（僅當值 is 本實例），熱重載不會殘留死實例
   （PR fix/plugin-lifecycle-p1 修復）。

常用 API：`get_plugin(name)`（會觸發 lazy 載入）、`peek_plugin(name)`（不觸發）、
`load_plugin`、`unload_plugin`、`reload_plugin`、`get_plugin_state`、`get_plugin_stats`、
`cleanup()`（反向卸載全部 + loader shutdown（現為 no-op，執行池死碼已刪）；
`appbase._cleanup_on_exit` 於
SIGTERM/SIGINT/SIGHUP 呼叫它，之後 dbmgr flush/release）。

### 1.3 安全閘門

- `security_mode`（pyproject 中繼資料）：`required` 在 `authorization_enabled=False` 時
  轉 `DISABLED`；`optional` 與 `public` **現行行為相同**（`_can_activate_plugin` 只分流
  `required`——文件如實記錄，勿假裝 optional 有降級邏輯）。
- 路由級：`default_route_policy = staticmethod(policy)` 掛整個 blueprint 的
  before_request（ADR-045），豁免走 `default_route_exempt_endpoints` 端點集合或
  `Plugin.skip_default_policy` 裝飾器。規則與慣例見 `權限控制開發使用指南.md`。

## 2. 三個擴充層（Layer 1/2/3）與實際使用狀態

```
Layer 1  Template Method（subclass override）    — plugin 自己的行為        ✅ 主力
Layer 2  Instance Hooks（plugin.add_lifecycle_hook）— 外部監看「某個」plugin  ⚠ 零生產消費
Layer 3  Global Hooks（app.hook_manager）         — 應用層廣播              部分在用
```

### Layer 1：可覆寫範式方法（`plugin.py:Plugin`）

`_on_init()`、`register_prewarm_tasks()`、`_on_start()`、`_on_stop()`、`_on_reload()`、
`_on_menu_reload()`、`_on_unload()`、`_on_error(error)`、`_perform_health_check()`、
`setup_menus()`。現行覆寫者共 7 個 plugin（Sched/SSE/Quote/Auth/FundMgr/Option/
PluginManagerView）——這是框架真正的擴充主力。

### Layer 2：實例鉤（`plugin.py:Plugin.add_lifecycle_hook`）

事件：`before_start / after_start / before_stop / after_stop / on_error`。
回呼例外只記 log（`_execute_hooks`）。
**現況：僅單元測試使用，全 workspace 零生產消費**；與 Layer 3 語意重疊，
属評估移除的死 API 面（見 artifacts/plugin-architecture-review-20260930.md R2）。
新代碼請勿導入。

### Layer 3：全域 hook（`hook.py:HookManager`）

```python
app.hook_manager.register_hook(name, callback, priority=100, plugin_name=None)
app.hook_manager.call_hook(name, **context)      # 回 List[HookCallResult]
app.hook_manager.render_hook(name, **context)    # 串接字串結果 → Markup
```

- callback 收到**單一 dict 參數**（context）；`app`、`request`、`current_user`
  會自動 setdefault 進去。
- priority 小的先跑；單一 callback 例外只記 log，不中斷其餘。
- `register_hook` 與 `call_hook` 共用 RLock 保護，callback 內可安全再註冊
  （dispatch 時回呼在鎖外執行）。

**框架內建 hook 名與生產消費端矩陣**（2026-09-30 逐倉 grep 實證）：

| 組別 | 名稱 | 觸發點 | 生產消費端 |
|---|---|---|---|
| Plugin 生命週期 | `plugin_after_init` | `plugin.py:Plugin.__init__` | ✅ SchedService、QuoteService（見 §0 脆弱握手） |
| | `plugin_service_init` | `ServicePlugin.__init__` | 無（僅測試） |
| | `plugin_before/after_start`、`plugin_before/after_stop`、`plugin_before/after_reload` | `start()/stop()/reload()` | **無任何註冊者**（觸發了但無人收；保留作預留擴充點） |
| Controller | `controller_before_request`、`controller_after_request`、`controller_error_handler` | `appbase.py:_FlaskBase.register_request_handler` | 僅各 plugin 的 `HOOK_EXAMPLES` 示範（config 預設關） |
| Template（jinja global `call_hook`） | `view_layouts_base_html_head`、`view_layouts_base_content_top`、`view_layouts_base_content_bottom`、`view_layouts_base_body_bottom` | funlab-flaskr `layouts/base.html`、`base-fullscreen.html`（各 4 處實碼） | 僅 HOOK_EXAMPLES 示範 |
| Sched 任務 | `task_before_execute`、`task_after_execute`、`task_error` | `funlab-sched task.py:_execute_with_hooks` | 僅 HOOK_EXAMPLES 示範 |
| Model | `model_before_save`、`model_after_save`、`model_after_create`、`model_before_delete`、`model_after_delete` | `model_hook.py:ModelHookMixin.save/delete` | **ModelHookMixin 全系統零使用者；hook 零註冊**（整組預留/死代碼） |

模板用法：`{{ call_hook('view_layouts_base_html_head') }}`。
教訓：判斷某 hook「有人在用」以內，先 grep `register_hook` 的註冊端，不要以觸發端存在為準。

## 3. Model hooks 用法（`model_hook.py:ModelHookMixin`，現況零使用）

```python
from funlab.core.model_hook import ModelHookMixin

class Note(ModelHookMixin, Base):
    ...

note.save(session, app)            # model_before_save → commit → model_after_save (+model_after_create)
note.delete(session, app)          # model_before_delete → delete+commit → model_after_delete
```

`app` 參數省略時不觸發 hook。引入前注意 finfun-core 實體走 dbmgr session 慣例
（見 `DBMGR開發使用指南.md`），本 Mixin 自帶 commit 語意，混用需先裁示事務邊界。

## 4. 背景執行緒 helper（`plugin.py:BackgroundWorkerMixin`，現況零生產使用）

```python
class MyService(BackgroundWorkerMixin, ServicePlugin):
    def _on_start(self):
        self.start_worker(self._loop, name="mysvc")
    def _on_stop(self):
        self.stop_worker(timeout=5.0)
    def _loop(self):
        while not self.worker_stop_requested:   # 或 worker_stop_event.wait(1)
            ...
```

`_perform_health_check`（mixin 版）會檢查 worker thread 存活。
註：現行背景服務（Sched loader、Quote loader）都是手搓 daemon Thread——新代碼用本
Mixin 優於手搓（健康檢查自動含线程存活）。

## 5. 選錯層的原則

| 需求 | 用 |
|---|---|
| plugin 自己要開關資源 | Layer 1（`_on_start/_on_stop`） |
| 消掉首請求的重型 import 延遲 | prewarm（`register_prewarm_tasks`，見 PREWARM 指南） |
| 全應用審計/指標/級聯 | Layer 3（全域 hook；注意 §2 矩陣：生命週期 hook 目前無人消費） |
| 請求級橫斷（log/audit） | `controller_*` hooks |
| 頁面注入 CSS/JS | `view_layouts_*` hooks |
| ORM 寫入副作用 | `model_*` hooks（先確認 §3 事務邊界） |
| 「等所有 plugin 註冊完再啟動 X」 | 現況=模仿 SchedService 監聽 `plugin_after_init`＋比對 pluginmanager（脆弱）；改善後=監聽專門訊號 |

## 6. 觀測面

- `/health`（funlab-flaskr root_bp）：逐 plugin `health`（is_healthy/error_count/
  last_error）＋ prewarm 狀態；late-pending 不計 degraded；明細受 `HEALTH_DETAIL`
  （local/admin/open）控管。非 200＝degraded。
- `/plugin-manager/*`（PluginManagerView，admin 限定）：`get_plugin_stats()` 的
  state/load_time/last_access/error_message/load_mode、load/reload/health/metrics API、
  plugin cache 清除。
- `PluginHealth.uptime` 由 `health_check()` 以 `_metrics.start_time` 補算（本 PR 修復，
  不再恆 0）；`metrics`（request_count/error_rate/avg_response_time）
  由 blueprint 中介層自動記錄，可用。

## 7. 相關測試

`funlab-libs/tests/`：`test_plugin_lifecycle.py`（狀態機/鉤序/BackgroundWorkerMixin）、
`test_plugin_manager_security_mode.py` 與 `test_plugin_manager_security_failclosed.py`
（security_mode 閘門、AUTH-03 拒啟動）、`test_plugin_manager_unload.py`、
`test_plugin_cache_staleness.py`（幽靈列剔除）、`test_plugin_and_notification.py`、
`test_hook_manager_threadsafe.py`、`test_prewarm*.py`。
