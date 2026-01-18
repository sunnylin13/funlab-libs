# Funlab Plugin 開發指引

## 目錄
1. [架構概述](#架構概述)
2. [Plugin 類型](#plugin-類型)
3. [開發環境設置](#開發環境設置)
4. [ViewPlugin 開發](#viewplugin-開發)
5. [SecurityPlugin 開發](#securityplugin-開發)
6. [ServicePlugin 開發](#serviceplugin-開發)
7. [生命週期管理](#生命週期管理)
8. [性能監控](#性能監控)
9. [配置管理](#配置管理)
10. [最佳實踐](#最佳實踐)
11. [完整範例](#完整範例)

## 架構概述

Funlab Plugin 系統基於增強的基礎類別，提供以下核心功能：

- **生命週期管理**: 自動管理 Plugin 的初始化、啟動、停止和卸載
- **健康監控**: 內建健康檢查和錯誤恢復機制
- **性能指標**: 自動收集請求數量、響應時間等指標
- **配置管理**: 支援動態配置和熱重載
- **安全機制**: 內建驗證和授權支援

### Plugin 基礎架構

```python
from funlab.core.enhanced_plugin import EnhancedViewPlugin, EnhancedSecurityPlugin, EnhancedServicePlugin
from funlab.core.auth import admin_required, role_required
```

## Plugin 類型

### 1. ViewPlugin (EnhancedViewPlugin)
- **用途**: 提供 Web UI 介面的 Plugin
- **特點**: 自動 Blueprint 管理、選單整合、模板支援
- **適用場景**: 管理介面、報表頁面、用戶操作介面

### 2. SecurityPlugin (EnhancedSecurityPlugin)
- **用途**: 處理身份驗證和授權的 Plugin
- **特點**: 內建 LoginManager、安全指標、會話管理
- **適用場景**: 登入系統、權限管理、安全控制

### 3. ServicePlugin (EnhancedServicePlugin)
- **用途**: 提供後台服務的 Plugin
- **特點**: 後台執行緒管理、服務監控、自動重啟
- **適用場景**: 資料處理、定時任務、API 服務

## 開發環境設置

### 項目結構
```
your-plugin/
├── README.md
├── pyproject.toml
├── plugin.toml           # Plugin 配置檔
├── finfun/
│   └── your_plugin/
│       ├── __init__.py
│       ├── plugin.py     # 主要 Plugin 類別
│       ├── models.py     # 資料模型 (可選)
│       ├── views.py      # 路由處理 (可選)
│       ├── services.py   # 業務邏輯 (可選)
│       ├── static/       # 靜態檔案
│       └── templates/    # 模板檔案
└── tests/
    └── test_plugin.py
```

### 基本依賴
```toml
# pyproject.toml
[tool.poetry.dependencies]
python = "^3.11"
funlab-libs = "*"
flask = "*"
flask-login = "*"
```

## ViewPlugin 開發

### 基本 ViewPlugin 範例

```python
# finfun/your_plugin/plugin.py
from funlab.core.enhanced_plugin import EnhancedViewPlugin
from funlab.core.auth import admin_required, role_required
from funlab.core.menu import Menu, MenuItem
from flask import render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user

class YourViewPlugin(EnhancedViewPlugin):
    """您的 ViewPlugin 範例"""

    def __init__(self, app, url_prefix=None):
        super().__init__(app, url_prefix)
        self.register_routes()

    def setup_menus(self):
        """設置選單項目"""
        # 主選單 (管理員可見)
        self._mainmenu = Menu(
            title="您的功能",
            items=[
                MenuItem(title="主控台", endpoint=f"{self.bp_name}.dashboard"),
                MenuItem(title="設定", endpoint=f"{self.bp_name}.settings"),
            ]
        )

        # 用戶選單
        self._usermenu = Menu(
            title="我的功能",
            items=[
                MenuItem(title="個人頁面", endpoint=f"{self.bp_name}.profile"),
            ],
            collapsible=True
        )

    def register_routes(self):
        """註冊路由"""

        @self.blueprint.route('/')
        @login_required
        def index():
            return render_template('your_plugin/index.html')

        @self.blueprint.route('/dashboard')
        @login_required
        @admin_required
        def dashboard():
            metrics = self.metrics
            health = self.health
            return render_template('your_plugin/dashboard.html',
                                 metrics=metrics, health=health)

        @self.blueprint.route('/api/data')
        @login_required
        def api_data():
            return jsonify({
                'status': 'success',
                'data': self._get_data()
            })

        @self.blueprint.route('/settings', methods=['GET', 'POST'])
        @login_required
        @role_required(['admin', 'manager'])
        def settings():
            if request.method == 'POST':
                # 處理設定更新
                return redirect(url_for(f'{self.bp_name}.settings'))
            return render_template('your_plugin/settings.html')

    def _get_data(self):
        """獲取資料的私有方法"""
        return {"message": "Hello from plugin!"}

    def _perform_health_check(self) -> bool:
        """自定義健康檢查"""
        try:
            # 執行您的健康檢查邏輯
            self._get_data()
            return True
        except Exception as e:
            self.mylogger.error(f"Health check failed: {e}")
            return False
```

### 模板範例

```html
<!-- templates/your_plugin/index.html -->
{% extends "base.html" %}

{% block title %}您的功能{% endblock %}

{% block content %}
<div class="container">
    <h1>歡迎使用您的功能</h1>
    <div id="plugin-content">
        <!-- 內容區域 -->
    </div>
</div>

<script>
// 載入資料的 JavaScript
fetch('/your_plugin/api/data')
    .then(response => response.json())
    .then(data => {
        document.getElementById('plugin-content').innerHTML =
            '<p>' + data.data.message + '</p>';
    });
</script>
{% endblock %}
```

## SecurityPlugin 開發

### 認證 Plugin 範例

```python
from funlab.core.enhanced_plugin import EnhancedSecurityPlugin
from funlab.core.auth import admin_required
from flask import render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, current_user
from werkzeug.security import check_password_hash

class AuthPlugin(EnhancedSecurityPlugin):
    """認證 Plugin"""

    def __init__(self, app, url_prefix='auth'):
        super().__init__(app, url_prefix)
        self.register_routes()
        self.setup_login_manager()

    def setup_login_manager(self):
        """設置 LoginManager"""
        @self.login_manager.user_loader
        def load_user(user_id):
            # 從資料庫載入用戶
            return self._load_user_from_db(user_id)

    def register_routes(self):

        @self.blueprint.route('/login', methods=['GET', 'POST'])
        def login():
            if request.method == 'POST':
                username = request.form['username']
                password = request.form['password']

                user = self._authenticate_user(username, password)
                if user:
                    login_user(user)
                    self.record_login_attempt(True)
                    return redirect(url_for('root_bp.home'))
                else:
                    self.record_login_attempt(False)
                    flash('Invalid credentials', 'error')

            return render_template('auth/login.html')

        @self.blueprint.route('/logout')
        def logout():
            logout_user()
            return redirect(url_for('root_bp.index'))

        @self.blueprint.route('/security-metrics')
        @admin_required
        def security_metrics():
            metrics = self.get_security_metrics()
            return render_template('auth/metrics.html', metrics=metrics)

    def _authenticate_user(self, username, password):
        """認證用戶"""
        # 實作您的認證邏輯
        user = self._get_user_by_username(username)
        if user and check_password_hash(user.password_hash, password):
            return user
        return None

    def _load_user_from_db(self, user_id):
        """從資料庫載入用戶"""
        # 實作用戶載入邏輯
        pass

    def _get_user_by_username(self, username):
        """根據用戶名獲取用戶"""
        # 實作用戶查詢邏輯
        pass
```

## ServicePlugin 開發

### 後台服務 Plugin 範例

```python
import threading
import time
from funlab.core.enhanced_plugin import EnhancedServicePlugin

class DataProcessorPlugin(EnhancedServicePlugin):
    """資料處理服務 Plugin"""

    def __init__(self, app):
        super().__init__(app)
        self._processing_queue = []
        self._process_lock = threading.RLock()
        self.register_routes()

    def start_service(self):
        """啟動服務"""
        if not self._service_running:
            self._service_running = True
            self._stop_event.clear()
            self._service_thread = threading.Thread(target=self._service_worker)
            self._service_thread.daemon = True
            self._service_thread.start()
            self.mylogger.info("Data processor service started")

    def stop_service(self):
        """停止服務"""
        if self._service_running:
            self._service_running = False
            self._stop_event.set()
            if self._service_thread:
                self._service_thread.join(timeout=10)
            self.mylogger.info("Data processor service stopped")

    def reload_service(self):
        """重新載入服務"""
        self.mylogger.info("Reloading data processor service")
        self.stop_service()
        # 重新載入配置
        self._init_configuration()
        self.start_service()

    def _service_worker(self):
        """服務工作執行緒"""
        while self._service_running and not self._stop_event.is_set():
            try:
                if self._processing_queue:
                    with self._process_lock:
                        if self._processing_queue:
                            task = self._processing_queue.pop(0)
                            self._process_task(task)

                # 檢查停止信號
                if self._stop_event.wait(timeout=1):
                    break

            except Exception as e:
                self.mylogger.error(f"Service worker error: {e}")
                time.sleep(5)  # 錯誤後等待重試

    def _process_task(self, task):
        """處理任務"""
        start_time = time.time()
        try:
            # 實作您的任務處理邏輯
            self.mylogger.info(f"Processing task: {task}")
            time.sleep(0.1)  # 模擬處理時間

            # 記錄成功的指標
            response_time = time.time() - start_time
            self._metrics.record_request(response_time, True)

        except Exception as e:
            # 記錄失敗的指標
            response_time = time.time() - start_time
            self._metrics.record_request(response_time, False)
            self.mylogger.error(f"Task processing failed: {e}")

    def add_task(self, task):
        """添加任務到處理佇列"""
        with self._process_lock:
            self._processing_queue.append(task)

    def register_routes(self):
        """註冊 API 路由"""

        @self.blueprint.route('/status')
        @admin_required
        def status():
            return {
                'service_running': self._service_running,
                'queue_size': len(self._processing_queue),
                'metrics': self.metrics,
                'health': self.health.__dict__
            }

        @self.blueprint.route('/add-task', methods=['POST'])
        @admin_required
        def add_task():
            task_data = request.json
            self.add_task(task_data)
            return {'status': 'Task added successfully'}

    def _perform_health_check(self) -> bool:
        """健康檢查"""
        # 檢查服務執行緒是否正常
        if not self._service_running:
            return False

        if self._service_thread and not self._service_thread.is_alive():
            return False

        # 檢查佇列是否過載
        if len(self._processing_queue) > 1000:
            self.mylogger.warning("Processing queue is overloaded")
            return False

        return True
```

## 生命週期管理

### 生命週期 Hooks

```python
class YourPlugin(EnhancedViewPlugin):

    def __init__(self, app, url_prefix=None):
        super().__init__(app, url_prefix)

        # 註冊生命週期 hooks
        self.add_lifecycle_hook('before_start', self._before_start_hook)
        self.add_lifecycle_hook('after_start', self._after_start_hook)
        self.add_lifecycle_hook('before_stop', self._before_stop_hook)
        self.add_lifecycle_hook('on_error', self._error_hook)

    def _before_start_hook(self):
        """啟動前執行"""
        self.mylogger.info("Preparing plugin for startup...")
        # 初始化資源

    def _after_start_hook(self):
        """啟動後執行"""
        self.mylogger.info("Plugin started successfully!")
        # 啟動後續任務

    def _before_stop_hook(self):
        """停止前執行"""
        self.mylogger.info("Preparing plugin for shutdown...")
        # 清理資源

    def _error_hook(self, error):
        """錯誤處理"""
        self.mylogger.error(f"Plugin error occurred: {error}")
        # 錯誤恢復邏輯
```

### 手動生命週期控制

```python
# 在應用中控制 Plugin 生命週期
plugin = YourPlugin(app)

# 啟動 Plugin
if plugin.start():
    print("Plugin started successfully")

# 檢查狀態
print(f"Plugin state: {plugin.state}")
print(f"Plugin health: {plugin.health}")

# 健康檢查
if not plugin.health_check():
    print("Plugin is unhealthy, restarting...")
    plugin.reload()

# 停止 Plugin
plugin.stop()
```

## 性能監控

### 內建指標

所有 Plugin 自動收集以下指標：
- 請求數量
- 錯誤數量
- 響應時間 (平均/最小/最大)
- 錯誤率
- 每秒請求數
- 運行時間

### 自定義指標

```python
class MonitoredPlugin(EnhancedViewPlugin):

    def __init__(self, app, url_prefix=None):
        super().__init__(app, url_prefix)

        # 自定義指標
        self._custom_metrics = {
            'database_queries': 0,
            'cache_hits': 0,
            'cache_misses': 0
        }
        self._custom_lock = threading.RLock()

    def record_database_query(self):
        """記錄資料庫查詢"""
        with self._custom_lock:
            self._custom_metrics['database_queries'] += 1

    def record_cache_access(self, hit: bool):
        """記錄快取存取"""
        with self._custom_lock:
            if hit:
                self._custom_metrics['cache_hits'] += 1
            else:
                self._custom_metrics['cache_misses'] += 1

    def get_custom_metrics(self):
        """獲取自定義指標"""
        with self._custom_lock:
            return self._custom_metrics.copy()

    @property
    def metrics(self):
        """覆寫以包含自定義指標"""
        base_metrics = super().metrics
        custom_metrics = self.get_custom_metrics()

        # 計算快取命中率
        total_cache_access = custom_metrics['cache_hits'] + custom_metrics['cache_misses']
        cache_hit_rate = custom_metrics['cache_hits'] / max(1, total_cache_access)

        return {
            **base_metrics,
            **custom_metrics,
            'cache_hit_rate': cache_hit_rate
        }
```

## 配置管理

### Plugin 配置檔範例

```toml
# plugin.toml
[YourPlugin]
enabled = true
debug_mode = false
max_connections = 100
timeout = 30

[YourPlugin.database]
host = "localhost"
port = 5432
name = "your_db"

[YourPlugin.cache]
enabled = true
ttl = 3600
max_size = 1000

[YourPlugin.features]
feature_a = true
feature_b = false
```

### 動態配置使用

```python
class ConfigurablePlugin(EnhancedViewPlugin):

    def __init__(self, app, url_prefix=None):
        super().__init__(app, url_prefix)
        self._load_config()

    def _load_config(self):
        """載入配置"""
        # 獲取 Plugin 特定配置
        self.max_connections = self.plugin_config.get('max_connections', 100)
        self.timeout = self.plugin_config.get('timeout', 30)

        # 獲取嵌套配置
        db_config = self.plugin_config.get('database', {})
        self.db_host = db_config.get('host', 'localhost')
        self.db_port = db_config.get('port', 5432)

        # 功能開關
        features = self.plugin_config.get('features', {})
        self.feature_a_enabled = features.get('feature_a', True)

    def reload_config(self):
        """重新載入配置"""
        self.mylogger.info("Reloading plugin configuration")
        self._init_configuration()
        self._load_config()

        # 應用新配置
        self._apply_config_changes()

    def _apply_config_changes(self):
        """應用配置變更"""
        # 根據新配置調整 Plugin 行為
        pass
```

## 最佳實踐

### 1. 錯誤處理

```python
def register_routes(self):

    @self.blueprint.route('/risky-operation')
    @login_required
    def risky_operation():
        try:
            result = self._perform_risky_operation()
            return jsonify({'status': 'success', 'result': result})

        except ValidationError as e:
            self.mylogger.warning(f"Validation error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 400

        except DatabaseError as e:
            self.mylogger.error(f"Database error: {e}")
            return jsonify({'status': 'error', 'message': 'Database error'}), 500

        except Exception as e:
            self.mylogger.error(f"Unexpected error: {e}")
            return jsonify({'status': 'error', 'message': 'Internal error'}), 500
```

### 2. 資源管理

```python
class ResourceManagedPlugin(EnhancedViewPlugin):

    def _on_start(self):
        """啟動時初始化資源"""
        self._connection_pool = self._create_connection_pool()
        self._cache = self._create_cache()

    def _on_stop(self):
        """停止時清理資源"""
        if hasattr(self, '_connection_pool'):
            self._connection_pool.close()

        if hasattr(self, '_cache'):
            self._cache.clear()

    def _create_connection_pool(self):
        """創建連接池"""
        # 實作連接池邏輯
        pass

    def _create_cache(self):
        """創建快取"""
        # 實作快取邏輯
        pass
```

### 3. 測試支援

```python
# tests/test_plugin.py
import pytest
from unittest.mock import Mock
from finfun.your_plugin.plugin import YourPlugin

class TestYourPlugin:

    @pytest.fixture
    def mock_app(self):
        app = Mock()
        app.extensions = {}
        app.get_section_config.return_value = {}
        return app

    @pytest.fixture
    def plugin(self, mock_app):
        return YourPlugin(mock_app)

    def test_plugin_initialization(self, plugin):
        assert plugin.name == 'your'
        assert plugin.state.value == 'ready'

    def test_plugin_start_stop(self, plugin):
        assert plugin.start() == True
        assert plugin.state.value == 'running'

        assert plugin.stop() == True
        assert plugin.state.value == 'stopped'

    def test_health_check(self, plugin):
        plugin.start()
        assert plugin.health_check() == True

    def test_metrics_collection(self, plugin):
        metrics = plugin.metrics
        assert 'uptime' in metrics
        assert 'request_count' in metrics
```

## 完整範例

以下是一個完整的 Plugin 範例，展示了所有主要功能：

```python
# finfun/demo_plugin/plugin.py
from funlab.core.enhanced_plugin import EnhancedViewPlugin
from funlab.core.auth import admin_required, role_required
from funlab.core.menu import Menu, MenuItem
from flask import render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
import threading
import time

class DemoPlugin(EnhancedViewPlugin):
    """完整的 Demo Plugin 範例"""

    def __init__(self, app, url_prefix='demo'):
        # 初始化自定義屬性
        self._demo_data = {}
        self._data_lock = threading.RLock()

        # 調用父類初始化
        super().__init__(app, url_prefix)

        # 註冊路由和設置
        self.register_routes()
        self._setup_lifecycle_hooks()
        self._load_demo_config()

    def setup_menus(self):
        """設置選單"""
        self._mainmenu = Menu(
            title="Demo功能",
            items=[
                MenuItem(title="主控台", endpoint=f"{self.bp_name}.dashboard"),
                MenuItem(title="資料管理", endpoint=f"{self.bp_name}.data_management"),
                MenuItem(title="設定", endpoint=f"{self.bp_name}.settings"),
            ]
        )

        self._usermenu = Menu(
            title="Demo",
            items=[
                MenuItem(title="我的資料", endpoint=f"{self.bp_name}.my_data"),
            ],
            collapsible=True
        )

    def register_routes(self):
        """註冊所有路由"""

        @self.blueprint.route('/')
        @login_required
        def index():
            return render_template('demo/index.html',
                                 plugin_name=self.name,
                                 user=current_user)

        @self.blueprint.route('/dashboard')
        @login_required
        @admin_required
        def dashboard():
            metrics = self.metrics
            health = self.health
            custom_metrics = self._get_custom_metrics()

            return render_template('demo/dashboard.html',
                                 metrics=metrics,
                                 health=health,
                                 custom_metrics=custom_metrics)

        @self.blueprint.route('/data-management')
        @login_required
        @role_required(['admin', 'manager'])
        def data_management():
            data = self._get_all_data()
            return render_template('demo/data_management.html', data=data)

        @self.blueprint.route('/my-data')
        @login_required
        def my_data():
            user_data = self._get_user_data(current_user.id)
            return render_template('demo/my_data.html', data=user_data)

        @self.blueprint.route('/settings', methods=['GET', 'POST'])
        @login_required
        @admin_required
        def settings():
            if request.method == 'POST':
                # 處理設定更新
                self._update_settings(request.form.to_dict())
                flash('設定已更新', 'success')
                return redirect(url_for(f'{self.bp_name}.settings'))

            current_settings = self._get_current_settings()
            return render_template('demo/settings.html',
                                 settings=current_settings)

        # API 路由
        @self.blueprint.route('/api/data')
        @login_required
        def api_get_data():
            try:
                data = self._get_user_data(current_user.id)
                return jsonify({
                    'status': 'success',
                    'data': data,
                    'timestamp': time.time()
                })
            except Exception as e:
                self.mylogger.error(f"API error: {e}")
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to fetch data'
                }), 500

        @self.blueprint.route('/api/data', methods=['POST'])
        @login_required
        def api_save_data():
            try:
                data = request.json
                self._save_user_data(current_user.id, data)
                return jsonify({
                    'status': 'success',
                    'message': 'Data saved successfully'
                })
            except Exception as e:
                self.mylogger.error(f"API save error: {e}")
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to save data'
                }), 500

        @self.blueprint.route('/api/health')
        @admin_required
        def api_health():
            health_status = self.health_check()
            return jsonify({
                'healthy': health_status,
                'state': self.state.value,
                'metrics': self.metrics,
                'timestamp': time.time()
            })

    def _setup_lifecycle_hooks(self):
        """設置生命週期 hooks"""
        self.add_lifecycle_hook('before_start', self._before_start)
        self.add_lifecycle_hook('after_start', self._after_start)
        self.add_lifecycle_hook('before_stop', self._before_stop)
        self.add_lifecycle_hook('on_error', self._on_error)

    def _before_start(self):
        """啟動前準備"""
        self.mylogger.info("Demo plugin preparing to start...")
        self._init_demo_data()

    def _after_start(self):
        """啟動後處理"""
        self.mylogger.info("Demo plugin started successfully!")
        # 可以啟動背景任務等

    def _before_stop(self):
        """停止前清理"""
        self.mylogger.info("Demo plugin preparing to stop...")
        self._cleanup_resources()

    def _on_error(self, error):
        """錯誤處理"""
        self.mylogger.error(f"Demo plugin error: {error}")
        # 可以實作錯誤恢復邏輯

    def _load_demo_config(self):
        """載入 Demo 特定配置"""
        self.demo_feature_enabled = self.plugin_config.get('demo_feature_enabled', True)
        self.max_data_items = self.plugin_config.get('max_data_items', 1000)
        self.data_retention_days = self.plugin_config.get('data_retention_days', 30)

    def _init_demo_data(self):
        """初始化 Demo 資料"""
        with self._data_lock:
            if not self._demo_data:
                self._demo_data = {
                    'initialized_at': time.time(),
                    'users': {},
                    'global_settings': {}
                }

    def _get_user_data(self, user_id):
        """獲取用戶資料"""
        with self._data_lock:
            return self._demo_data['users'].get(str(user_id), {})

    def _save_user_data(self, user_id, data):
        """保存用戶資料"""
        with self._data_lock:
            self._demo_data['users'][str(user_id)] = {
                **data,
                'updated_at': time.time()
            }

    def _get_all_data(self):
        """獲取所有資料 (管理員用)"""
        with self._data_lock:
            return self._demo_data.copy()

    def _get_current_settings(self):
        """獲取當前設定"""
        return {
            'demo_feature_enabled': self.demo_feature_enabled,
            'max_data_items': self.max_data_items,
            'data_retention_days': self.data_retention_days
        }

    def _update_settings(self, settings):
        """更新設定"""
        self.demo_feature_enabled = settings.get('demo_feature_enabled') == 'on'
        self.max_data_items = int(settings.get('max_data_items', 1000))
        self.data_retention_days = int(settings.get('data_retention_days', 30))

    def _get_custom_metrics(self):
        """獲取自定義指標"""
        with self._data_lock:
            return {
                'total_users': len(self._demo_data.get('users', {})),
                'demo_feature_enabled': self.demo_feature_enabled,
                'data_size': len(str(self._demo_data))
            }

    def _cleanup_resources(self):
        """清理資源"""
        # 實作資源清理邏輯
        pass

    def _perform_health_check(self) -> bool:
        """執行健康檢查"""
        try:
            # 檢查資料結構完整性
            with self._data_lock:
                if not isinstance(self._demo_data, dict):
                    return False

                # 檢查用戶數量是否超過限制
                user_count = len(self._demo_data.get('users', {}))
                if user_count > self.max_data_items:
                    self.mylogger.warning(f"User count ({user_count}) exceeds limit ({self.max_data_items})")
                    return False

            return True

        except Exception as e:
            self.mylogger.error(f"Health check failed: {e}")
            return False

# Plugin 註冊
def create_plugin(app):
    """Plugin 工廠函數"""
    return DemoPlugin(app)
```

### 對應的模板檔案

```html
<!-- templates/demo/index.html -->
{% extends "base.html" %}

{% block title %}Demo Plugin{% endblock %}

{% block content %}
<div class="container">
    <h1>歡迎使用 Demo Plugin</h1>
    <p>您好，{{ user.username }}！</p>

    <div class="row">
        <div class="col-md-6">
            <div class="card">
                <div class="card-header">
                    <h5>我的資料</h5>
                </div>
                <div class="card-body" id="user-data">
                    載入中...
                </div>
            </div>
        </div>

        <div class="col-md-6">
            <div class="card">
                <div class="card-header">
                    <h5>操作</h5>
                </div>
                <div class="card-body">
                    <button class="btn btn-primary" onclick="loadData()">載入資料</button>
                    <button class="btn btn-success" onclick="saveData()">保存資料</button>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
function loadData() {
    fetch('/{{ plugin_name }}/api/data')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                document.getElementById('user-data').innerHTML =
                    '<pre>' + JSON.stringify(data.data, null, 2) + '</pre>';
            } else {
                alert('載入失敗: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('載入失敗');
        });
}

function saveData() {
    const sampleData = {
        message: 'Hello from frontend!',
        timestamp: new Date().toISOString()
    };

    fetch('/{{ plugin_name }}/api/data', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(sampleData)
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            alert('保存成功!');
            loadData(); // 重新載入資料
        } else {
            alert('保存失敗: ' + data.message);
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('保存失敗');
    });
}

// 頁面載入時自動載入資料
document.addEventListener('DOMContentLoaded', loadData);
</script>
{% endblock %}
```

這份完整的開發指引涵蓋了：

1. **三種 Plugin 類型的詳細說明和範例**
2. **完整的生命週期管理**
3. **性能監控和自定義指標**
4. **配置管理和動態重載**
5. **安全機制和權限控制**
6. **錯誤處理和資源管理**
7. **測試支援**
8. **完整的實際範例**

開發者可以根據這份指引快速建立功能完整、穩定可靠的 Plugin。
