# 權限 Decorator 與 Route Policy 模式（現行 API）

本文件只描述 `funlab-libs` **現有** API；所有引用都能在原始碼中找到。

## 現有裝飾器（`funlab/core/auth.py`）

| API | 簽名 | 失敗行為 |
|---|---|---|
| `policy_required(policy)` | `policy_required(policy)(func)` | 未登入→依安全模式導向登入或 403；policy(user) False→403（`error-403.html`） |
| `role_required(roles)` | `role_required(['admin','manager'])`，比對 `current_user.role` | 未登入同上；角色不符→403 |
| `admin_required(func)` | 直接裝飾（**不帶括號**），實作為 `policy_required(is_admin)` | 403 |
| `evaluate_policy(policy)` | 供 before_request middleware 手動呼叫 | 回傳失敗 response 或 `None` |

未登入的導向邏輯在 `auth.py:_handle_unauthenticated`：`app.authorization_enabled` 為真時走
`login_manager.unauthorized()`（導向登入頁），否則直接 403。

**注意**：`admin_required` 現行實作不支援 `@admin_required()` 帶括號用法（會把函數物件當
`func` 之外的東西傳錯位置）。統一寫法：不帶括號。

## 共用 policy 函數（`funlab/core/policy.py`）

```python
from funlab.core.policy import is_admin, has_role, is_supervisor, is_authenticated_user
```

- `is_admin(user)`：`getattr(user, 'is_admin', False)`。
- `has_role(user, *roles)`：大小寫不拘比對 `user.role`。
- `is_supervisor(user)`＝`has_role(user, 'supervisor')`。
- `is_authenticated_user(user)`：`user.is_authenticated`。

## 組合用法

```python
from funlab.core.auth import admin_required, role_required, policy_required
from funlab.core.policy import is_supervisor

@self.blueprint.route('/admin-only')
@admin_required
def admin_only(): ...

@self.blueprint.route('/managers')
@role_required(['admin', 'manager'])
def managers(): ...

@self.blueprint.route('/supervisor')
@policy_required(is_supervisor)
def supervisor_only(): ...
```

## Plugin 層級 default route policy（`funlab/core/plugin.py:Plugin`）

整個 Blueprint 的預設守門，免逐路由加裝飾器：

```python
from funlab.core.plugin import Plugin
from funlab.core.policy import is_authenticated_user

class MyView(Plugin):
    default_route_policy = is_authenticated_user          # 類別或 instance method 皆可
    default_route_exempt_endpoints = {'login', 'health'}  # 白名單（endpoint 去掉 bp 前綴後的名字）

    def _register_routes(self):
        @self.blueprint.route('/ping')
        @Plugin.skip_default_policy                       # 單一路由豁免
        def ping():
            return 'pong'
```

機制（`plugin.py:Plugin._add_default_policy_middleware` / `_resolve_default_route_policy`）：

- blueprint `before_request` 只对 `self.bp_name.` 前綴的 endpoint 生效。
- 免除途徑有二：`default_route_exempt_endpoints` 集合，或 view 函數掛
  `Plugin.skip_default_policy`（設定 `_skip_default_policy` 旗標）。
- `default_route_policy` 若為綁定到本 instance、且除 self 外必選位置參數為 0 的方法，
  會以 unbound 函數解析後呼叫（policy 收到 `current_user`）。
- 實際判定統一走 `funlab.core.auth.evaluate_policy`。

## 與 authorization 開關係

`ModernPluginManager` 裝上安全 provider（實作 `ISecurityProvider.login_manager`）後
`app.authorization_enabled = True`（`plugin_manager.py:ModernPluginManager._register_plugin_to_flask`）。
`security_mode='required'` 的 plugin 在 PUBLIC 模式不會啟用（同檔 `_can_activate_plugin`）。
