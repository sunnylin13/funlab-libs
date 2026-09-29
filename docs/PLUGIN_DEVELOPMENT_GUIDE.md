# Funlab Plugin 開發指引（現行 API 版）

本文件每個 import、類別名、方法簽名都已逐一核對原始碼
（`funlab/core/plugin.py`、`plugin_manager.py`、`appbase.py`、`menu.py`、`auth.py`、
`prewarm.py`、`notification.py`）。舊版引用的 `EnhancedViewPlugin` /
`EnhancedSecurityPlugin` / `EnhancedServicePlugin` **不存在**，已全部改正。

## 1. Plugin 類型（`funlab/core/plugin.py`）

| 類別 | 用途 | 要點 |
|---|---|---|
| `Plugin` | 一般 View plugin | 自動 Blueprint（`static/`、`templates/`）、選單容器、指標/健康 |
| `SecurityPlugin` | 認證 provider | `__init__` 建自己的 `LoginManager`（`login_view = "<bp>.login"`），`login_manager` property 讓 manager 自動接線 |
| `ServicePlugin` | 後台服務 | `__init__` 觸發 `plugin_service_init` hook；`_perform_health_check` 以 RUNNING 為準 |
| `BackgroundWorkerMixin` | 背景執行緒 helper | `start_worker/stop_worker/worker_stop_requested` |

`plugin.name` 由類別名去掉尾綴 `View/Security/Service/Plugin` 再小寫
（`Plugin._generate_plugin_name`）：`SSEService → "sse"`、`AuthView → "auth"`。
Blueprint 名 `{name}_bp`，url_prefix 預設 `/`+name（`__init__(app, url_prefix=...)` 可改）。

## 2. 註冊：entry point + 中繼資料

```toml
# your-plugin 的 pyproject.toml（範本：funlab-sse）
[project.entry-points."funlab_plugin"]
MyView = "funlab.myview.view:MyView"

[tool.funlab_plugin_metadata.MyView]
load_mode = "startup"          # "startup"（有 Blueprint/背景執行緒/選單時必須）| "lazy"（預設）
security_mode = "public"       # public | optional | required（required：無 auth provider 時不啟用）
# provides_security = true     # 只有認證 plugin 要
# dependencies = ["SSEService"]           # 硬相依：缺席→本 plugin DISABLED
# optional_dependencies = ["SchedService"] # 軟相依：只影響載入順序/降級
```

- 有 Blueprint 路由的 plugin **必須** `load_mode="startup"`：Flask 處理首請求後
  `register_blueprint` 會失敗（manager 會降級警告 `_blueprint_registered=False`）。
- 中繼資料以 `.plugin_cache/plugin_cache.json` 快取；改過 pyproject 中繼資料後可用
  app 設定 `RESCAN_PLUGINS = true` 強制重掃。

## 3. View plugin 完整範例（可直接照抄的 API）

```python
# funlab/myview/view.py
from flask import render_template, request, jsonify
from flask_login import current_user, login_required

from funlab.core.auth import admin_required, role_required
from funlab.core.menu import Menu, MenuItem
from funlab.core.plugin import Plugin


class MyView(Plugin):
    """示範 View plugin。"""

    # 整個 blueprint 預設要登入（慣例與規則見 docs/權限控制開發使用指南.md）
    # default_route_policy = staticmethod(is_authenticated_user)
    # default_route_exempt_endpoints = {'health'}

    def _on_init(self):
        """__init__ 期呼叫：註冊路由（此时 blueprint 已建好）。"""
        self._register_routes()

    def setup_menus(self):
        """選單：建立自己的容器後，用 app.append_*menu 掛進應用選單。

        注意：Menu/MenuItem 沒有 `items=`/`endpoint=` 參數——
        MenuItem 用 href；選單項用 append() 加入。
        """
        super().setup_menus()
        self.app.append_mainmenu([
            MenuItem(title='My Home', href=f'/{self.name}/'),
            MenuItem(title='My Admin', href=f'/{self.name}/admin'),
        ])

    def _register_routes(self):
        @self.blueprint.route('/')
        def index():
            return render_template('myview/index.html', user=current_user)

        @self.blueprint.route('/admin')
        @admin_required                      # 不帶括號（現行實作不支援括號）
        def admin():
            return jsonify({'metrics': self.metrics})

        @self.blueprint.route('/managers')
        @role_required(['admin', 'manager'])
        def managers():
            return 'ok'

    def _perform_health_check(self) -> bool:
        return True
```

**選單 API 真相**（`funlab/core/menu.py` + 現行使用範例
`funlab-auth/funlab/auth/view.py:AuthView.setup_menus`、
`funlab-flaskr/funlab/flaskr/plugin_mgmt_view.py`）：

- `MenuItem(title=..., href=..., icon=..., badge=...)`——沒有 `endpoint` 參數，
  路由位址自己組 href（`url_for` 在 `setup_menus` 時期不一定可用）。
- 掛到應用：`app.append_mainmenu / insert_mainmenu / append_adminmenu /
  insert_adminmenu / append_usermenu / insert_usermenu`（`appbase.py`，參數收單個或 list）。
- `self._mainmenu` / `self.usermenu` 是 plugin 自己的佔位容器（`setup_menus` 預設建
  dummy Menu）；把項目經 `app.append_*` 掛進應用層才會顯示。
- admin 選單項放 `app.append_adminmenu([...])`：非 admin 使用者自動不可見
  （`appbase._init_menu_container` 給 admin 選單掛了 `required_policy=is_admin`）。

## 4. Security plugin（認證 provider）

```python
from flask import redirect, render_template, request, url_for
from flask_login import login_user, logout_user

from funlab.core.plugin import SecurityPlugin


class AuthView(SecurityPlugin):
    def _on_init(self):
        # 用「自己的」login_manager（SecurityPlugin 已建好並在 login_manager property 曝露）
        @self.login_manager.user_loader
        def load_user(user_id):
            return self._load_user_from_db(user_id)     # 找不到必須回 None
        self._register_routes()

    def _register_routes(self):
        @self.blueprint.route('/login', methods=['GET', 'POST'])
        def login():
            if request.method == 'POST':
                user = self._authenticate(request.form['username'],
                                           request.form['password'])
                if user:
                    login_user(user, remember=bool(request.form.get('rememberme')))
                    return redirect(url_for('root_bp.home'))
            return render_template('auth/login.html')

        @self.blueprint.route('/logout')
        def logout():
            logout_user()
            return redirect(url_for('root_bp.index'))
```

框架行為（`plugin_manager.py:_register_plugin_to_flask`）：

- 任何暴露 `login_manager` property 的 plugin 都符合 `ISecurityProvider`（Protocol，
  不必繼承 `SecurityPlugin` 也行，但繼承會幫你建好 LoginManager 與 login_view 慣例）。
- 第一個 provider 會**取代** appbase 的匿名佔位 loader，app 轉
  `SecurityMode.SECURED`、`authorization_enabled=True`；第二個 provider 只留路由並警告。
- 認證 plugin 應宣告 `provides_security = true` + `load_mode = "startup"`，
  manager 會把它排在其他 startup plugin 之前載入。
- `SecurityPlugin.login_manager.login_view` 已是 `"<bp_name>.login"`；未登入訪問受保護
  路由會導向它。若 login 路由另有名字，可在 plugin 上宣告 `login_view` 屬性（manager 會寫進
  `blueprint_login_views`）。
- `user_loader` **回 None 即可**讓 flask-login 導向匿名——不要在回 None 後還存取
  `user.username`（funlab-auth 有相關缺陷記錄，屬該倉修復範圍）。

## 5. Service plugin（背景服務）

```python
from funlab.core.plugin import BackgroundWorkerMixin, ServicePlugin


class MyService(BackgroundWorkerMixin, ServicePlugin):
    def _on_start(self):
        self.start_worker(self._loop, name='mysvc-loop')

    def _on_stop(self):
        self.stop_worker(timeout=5.0)      # set stop event + join(timeout)

    def _loop(self):
        while not self.worker_stop_requested:
            if self.worker_stop_event.wait(timeout=1.0):
                break
            # ... 週期工作 ...
```

- 生命週期由 `ModernPluginManager` 驅動：`_load_plugin_sync` 內建實例後立即 `start()`；
  失敗→`PluginState.ERROR`（不會部分殘留於 `app.plugins`）。
- `_on_stop` 由框架經 `_run_stop_safely` 執行：**最多一次、5 秒逾時續走**，
  裏面做 join/關閉資源，勿做長等待。
- 掛 DB 表：覆寫 `entities_registry` property 回傳你的
  `sqlalchemy.orm.registry`（manager 會呼叫 `app.dbmgr.create_registry_tables(...)`）。
  跨套件 FK 請共用 `funlab.core._entity_registry.APP_ENTITIES_REGISTRY`。

## 6. 設定（`_Configuable.get_config`，`funlab/core/__init__.py`）

- plugin 目錄放 `conf/plugin.toml`，**section 名稱 = 類別名**：

```toml
# conf/plugin.toml
[MyView]
greeting = "hello"
```

- 主 app `config.toml` 的同名 section 會覆蓋 plugin.toml（外掛層疊加，
  `Plugin._init_configuration` 以 `get_section_config(類別名)` 作 ext_config）。
- 讀取：`self.plugin_config.get('greeting', 'default')`；巢狀 section 用
  `self.plugin_config.get('database', {})`。`reload()` 會重跑 `_init_configuration`。
- 環境變數/加密值機制（`{{ENV_VAR:NAME}}`、vars2env Fernet）屬 `funlab/utils/vars2env.py`
  與 config 檔約定，plugin 端不需自行解密。

## 7. 通知 / 即時事件（`funlab/core/notification.py`）

取得 provider 的**唯一正確寫法**：

```python
prov = getattr(self.app, 'notification_provider', None)
if prov and prov.supports_realtime:
    prov.send_event('PriceUpdate', user_id, {'symbol': '2330', 'price': 123.0})
```

介面要點（`INotificationProvider`）：

- 核心：`send_user_notification(title, message, target_userid=None, priority='NORMAL',
  expire_after=None)`（None=全域）、`send_global_notification(...)`、
  `fetch_unread(user_id)`、`dismiss_items(user_id, ids)`、`dismiss_all(user_id)`。
- 即時擴充：`send_event(event_type, target_userid, payload, priority='NORMAL',
  expire_after=None) -> bool`（polling provider 恆回 False）、
  `get_connected_users(event_type) -> set`、`supports_realtime` property。
- 換 provider 用 `app.set_notification_provider(provider)`（funlab-flaskr 提供該方法；
  funlab-sse 的 `SSEService` 即如此掛上）。⚠️ **沒有 `app.sse_service` 這個屬性**——
  任何消費端用 `getattr(app, 'sse_service', None)` 都會靜默失效。

## 8. Import 與 prewarm 最佳實踐

- 輕量模組 top-level import；重型依賴（pandas/numpy/broker SDK）function-level import。
  `sys.modules` 已有快取，不要自製 `_lazy()` 包裝。
- 要消掉首請求延遲才用 prewarm；多 plugin 共用同一資源時 `skip_if_exists=True`
  （或 `resource_key=` 做資源級去重）。
- ⚠️ lazy plugin 在首請求才實例化——那时 app 啟動的 `prewarm.run()` 已執行完，
  你註冊的任務**不會再被執行**（註冊時記 WARNING，`status()` 中標 `late: true`）。
  需要保證執行的预热請把 plugin 設 `load_mode="startup"`。
- API 全貌與語意見 `docs/PREWARM.md`。

```python
from funlab.core.prewarm import register_prewarm

class MyView(Plugin):
    def register_prewarm_tasks(self):
        register_prewarm(
            "myview.heavy_stack",
            self._warmup,
            delay=3.0,
            skip_if_exists=True,
            owner="myview",
        )

    @staticmethod
    def _warmup():
        import pandas  # noqa: F401
```

## 9. 測試模式（照 `tests/test_plugin_lifecycle.py` 的做法）

```python
from unittest.mock import MagicMock
from funlab.core.config import Config

def make_app():
    app = MagicMock()
    app.extensions = {}
    app.plugins = {}
    hm = MagicMock(); hm.call_hook = lambda *a, **k: None
    app.hook_manager = hm
    cfg = Config({}); cfg._env_vars = {}
    app.get_section_config.return_value = cfg
    app._config = MagicMock(); app._config._env_vars = {}
    return app

def test_start_stop():
    from funlab.core.plugin import Plugin, PluginLifecycleState
    class _T(Plugin):
        def _init_blueprint(self, url_prefix=None):
            self.bp_name = self.name + "_bp"; self._blueprint = MagicMock()
        def _init_configuration(self):
            self.plugin_config = MagicMock()
    p = _T(make_app())
    assert p.state == PluginLifecycleState.READY
    assert p.start() is True and p.state == PluginLifecycleState.RUNNING
    assert p.stop() is True and p.state == PluginLifecycleState.STOPPED
```

完整生命週期/狀態機細節見 `docs/PLUGIN_LIFECYCLE.md`；權限模式見
`docs/權限控制開發使用指南.md`；DB 使用規則見 `docs/DBMGR開發使用指南.md`。
