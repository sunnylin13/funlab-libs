# Plugin 生命週期（現行實作）

本文件合併舊 `PLUGIN_LIFECYCLE_*` 系列，內容逐一核對
`funlab/core/plugin.py`、`funlab/core/plugin_manager.py`、`funlab/core/hook.py`、
`funlab/core/appbase.py` 現行原始碼。

## 1. 兩層狀態機

### 1.1 Plugin 物件狀態（`plugin.py:PluginLifecycleState`）

`INITIALIZING → READY → STARTING → RUNNING → STOPPING → STOPPED`，另有 `RELOADING` 與 `ERROR`。

| 方法 | 前置檢查 | 序列（成功路徑） | 例外路徑 |
|---|---|---|---|
| `start()` | 已 RUNNING→直接 True；處於 STARTING/STOPPING/RELOADING→警告並 False（防重入） | STARTING → 全域 hook `plugin_before_start` → 實例 hook `before_start` → `_on_start()` → RUNNING → 實例 hook `after_start` → 全域 hook `plugin_after_start`，回 True | ERROR、`_health.is_healthy=False`、hook `on_error` + `_on_error()`，回 False |
| `stop()` | 已 STOPPED→True | STOPPING → `plugin_before_stop` → `before_stop` → `_run_stop_safely()` → STOPPED → `after_stop` → `plugin_after_stop` | 同上 |
| `reload()` | RELOADING 中→警告 False | `plugin_before_reload` → `stop()`（失敗即中止）→ `_on_reload()`（預設重讀設定+選單）→ `start()` → `plugin_after_reload` | 同上 |
| `unload()` | — | `_on_unload()` → `stop()` | — |
| `health_check()` | ERROR 狀態直接 False | `_perform_health_check()` → 更新 `_health` | 例外時記 error_count 並 False |

- `start/stop/reload` 全程持 `self._lock`（RLock）：`_on_start` 等回呼在鎖內執行，
  **不要在其中做長阻塞**（會卡住同 plugin 的其他生命週期操作）。
- `_run_stop_safely(timeout=5.0)`：`_on_stop()` 在獨立 daemon 執行緒執行，`_stop_executed`
  旗標保證最多執行一次；逾時只警告不等待（`plugin.py:Plugin._run_stop_safely`）。
- `Plugin.__init__` 尾端呼叫全域 hook `plugin_after_init`，然後才轉 READY。

### 1.2 Manager 層狀態（`plugin_manager.py:PluginState`）

`UNLOADED → LOADING → LOADED → ACTIVE`；載入例外→`ERROR`；安全模式擋下→`DISABLED`。

`ModernPluginManager` 流程（`register_plugins`）：

1. `PluginLoader.discover_plugins(group)`：`entry_points(group='funlab_plugin')` 列舉
   （只讀 metadata，不 import）；pyproject 的 `[tool.funlab_plugin_metadata.<Name>]`
   提供 `dependencies / optional_dependencies / load_mode / security_mode / provides_security`。
   結果寫入 `.plugin_cache/plugin_cache.json`。
2. `PluginDependencyResolver.resolve_load_order`：缺硬-dependency 者剔除；
   軟-dependency 缺席只降級；DFS 拓撲排序（迴圈→`PluginCycleError`）。
3. `provides_security=True` 的 startup plugin 先載（安全 provider 優先接線），
   其餘 `load_mode='startup'` 同步載入；`load_mode='lazy'`（預設）留待首次
   `get_plugin()` 觸發。
4. 每個載入執行 `_register_plugin_to_flask`：登记 `app.plugins[name]`、
   `register_blueprint`、`entities_registry` 建表、ISecurityProvider 接線。

常用 API：`get_plugin(name)`（會觸發 lazy 載入）、`peek_plugin(name)`（不觸發）、
`load_plugin`、`unload_plugin`、`reload_plugin`、`get_plugin_state`、`get_plugin_stats`、
`cleanup()`（反向卸載全部 + 關閉 loader 執行池；`appbase._cleanup_on_exit` 呼叫它）。

## 2. 三個擴充層（Layer 1/2/3）

```
Layer 1  Template Method（subclass override）    — plugin 自己的行為
Layer 2  Instance Hooks（plugin.add_lifecycle_hook）— 外部監看「某個」plugin
Layer 3  Global Hooks（app.hook_manager）         — 應用層廣播，監看「所有」事件
```

### Layer 1：可覆寫範式方法（`plugin.py:Plugin`）

`_on_init()`、`register_prewarm_tasks()`、`_on_start()`、`_on_stop()`、`_on_reload()`、
`_on_menu_reload()`、`_on_unload()`、`_on_error(error)`、`_perform_health_check()`。

### Layer 2：實例鉤（`plugin.py:Plugin.add_lifecycle_hook`）

事件：`before_start / after_start / before_stop / after_stop / on_error`。

```python
other = app.plugin_manager.get_plugin('quote')
other.add_lifecycle_hook('after_start', lambda: my_service.connect())
```

回呼例外只記 log，不影響生命週期主流程（`_execute_hooks`）。

### Layer 3：全域 hook（`hook.py:HookManager`）

```python
app.hook_manager.register_hook(name, callback, priority=100, plugin_name=None)
app.hook_manager.call_hook(name, **context)      # 回 List[HookCallResult]
app.hook_manager.render_hook(name, **context)    # 串接字串結果 → Markup
```

- callback 收到**單一 dict 參數**（context）；`app`、`request`、`current_user`
  會自動 setdefault 進去。
- priority 小的先跑；單一 callback 例外只記 log，不中斷其餘。
- ⚠️ 現行 `register_hook` 與 `call_hook` 共用 list 無鎖，且 dispatch 就地迭代——
  註冊請集中在啟動/初始化期，回呼內不要自我註冊同一 hook（見 `IMPROVEMENT_PLAN.md` LIB-11）。

**框架內建的 hook 名**：

| 組別 | 名稱 | 觸發點 |
|---|---|---|
| Plugin 生命週期 | `plugin_after_init`、`plugin_service_init`、`plugin_before_start`、`plugin_after_start`、`plugin_before_stop`、`plugin_after_stop`、`plugin_before_reload`、`plugin_after_reload` | `plugin.py` 各生命週期方法 |
| Controller | `controller_before_request`、`controller_after_request`、`controller_error_handler` | `appbase.py:_FlaskBase.register_request_handler` |
| Template（jinja global `call_hook`） | `view_layouts_base_html_head`、`view_layouts_base_content_top`、`view_layouts_base_content_bottom`、`view_layouts_base_body_bottom` | 基礎模板 `<head>`/內容前後；`appbase.py:register_jinja_filters` 註冊 `call_hook` |
| Model | `model_before_save`、`model_after_save`、`model_after_create`、`model_before_delete`、`model_after_delete` | `model_hook.py:ModelHookMixin.save/delete`（model、model_class、session、is_new 等進 context） |

模板用法：`{{ call_hook('view_layouts_base_html_head') }}`。

## 3. Model hooks 用法（`model_hook.py:ModelHookMixin`）

```python
from funlab.core.model_hook import ModelHookMixin

class Note(ModelHookMixin, Base):
    ...

note.save(session, app)            # model_before_save → commit → model_after_save (+model_after_create)
note.delete(session, app)          # model_before_delete → delete+commit → model_after_delete
```

`app` 參數省略時不觸發 hook（只作普通 save/delete）。

## 4. 背景執行緒 helper（`plugin.py:BackgroundWorkerMixin`）

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

## 5. 選錯層的原則

| 需求 | 用 |
|---|---|
| plugin 自己要開關資源 | Layer 1（`_on_start/_on_stop`） |
| A 想知道 B 啟動了 | Layer 2（對 B 的 instance 註冊） |
| 全應用審計/指標/級聯 | Layer 3（全域 hook） |
| 請求級橫斷（log/audit） | `controller_*` hooks |
| 頁面注入 CSS/JS | `view_layouts_*` hooks |
| ORM 寫入副作用 | `model_*` hooks |

## 6. 相關測試

`tests/test_plugin_lifecycle.py`（狀態機/鉤序/SecurityPlugin/BackgroundWorkerMixin）、
`tests/test_plugin_manager_security_mode.py`（security_mode 閘門）、
`tests/test_plugin_and_notification.py`。
