# Plugin Lifecycle：實務應用場景

## 概述

本文檔詳細說明 Funlab 中三個重要的 `call_hook` 使用場景：
1. **HTTP Request Handler** - 在請求生命週期中的 hooks
2. **Database Model Operation** - 在資料庫操作時的 hooks
3. **Template/UI Rendering** - 在模板渲染時的 hooks

這些場景展示了 Layer 3（全局 hooks）如何在應用的不同層級實現跨越式的擴展。

---

## 場景 1：HTTP Request Handler 中的 Hooks

### 1.1 架構概述

在 `appbase.py` 中，應用程序在 Flask 的請求生命週期中註冊了多個 hook 觸發點：

```
Request 到達
    ↓
[before_request]  ← hook: 'request_before_processing'
    ↓
業務邏輯處理
    ↓
[after_request]   ← hook: 'request_after_processing'
    ↓
[errorhandler]    ← hook: 'request_error_occurred'
    ↓
[teardown]        ← hook: 'request_teardown'
    ↓
Response 返回
```

### 1.2 具體實現位置

在 `appbase.py` 的 `register_request_handler()` 方法中：

```python
def register_request_handler(self):
    @self.before_request
    def before_request_handler():
        """在每個請求前執行"""
        g.request_start_time = time.time()

        # 觸發 Layer 3 global hooks
        if hasattr(self, 'hook_manager'):
            self.hook_manager.call_hook(
                'request_before_processing',
                request=request,
                app=self
            )

    @self.after_request
    def after_request_handler(response):
        """在請求後執行，可修改 response"""
        elapsed = time.time() - g.request_start_time

        # 觸發 Layer 3 global hooks
        if hasattr(self, 'hook_manager'):
            self.hook_manager.call_hook(
                'request_after_processing',
                response=response,
                elapsed_time=elapsed,
                request=request,
                app=self
            )

        return response

    @self.errorhandler(Exception)
    def handle_error(error):
        """在發生異常時執行"""
        # 觸發 Layer 3 global hooks
        if hasattr(self, 'hook_manager'):
            self.hook_manager.call_hook(
                'request_error_occurred',
                error=error,
                request=request,
                app=self
            )

        return error_response, 500

    @self.teardown_appcontext
    def teardown_db():
        """請求結束時的清理"""
        # 觸發 Layer 3 global hooks
        if hasattr(self, 'hook_manager'):
            self.hook_manager.call_hook(
                'request_teardown',
                app=self
            )
```

### 1.3 實務應用場景

#### 場景 A：完整的 API 請求日誌記錄

```python
class APIRequestLogger:
    """
    記錄所有 API 請求的詳細信息：
    - 請求路徑、方法、參數
    - 響應狀態、耗時
    - 用戶信息（如果已登入）
    """

    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger('api_requests')

    def register_hooks(self):
        # 在請求開始時記錄基本信息
        self.app.hook_manager.register_hook(
            'request_before_processing',
            callback=self._log_request_start,
            priority=100
        )

        # 在請求結束時記錄完整信息
        self.app.hook_manager.register_hook(
            'request_after_processing',
            callback=self._log_request_end,
            priority=800  # 後期優先級，確保所有其他處理已完成
        )

        # 在發生錯誤時記錄
        self.app.hook_manager.register_hook(
            'request_error_occurred',
            callback=self._log_request_error,
            priority=100
        )

    def _log_request_start(self, context):
        """記錄請求開始"""
        request = context['request']
        g.api_log = {
            'timestamp': datetime.now().isoformat(),
            'method': request.method,
            'path': request.path,
            'remote_addr': request.remote_addr,
            'user_id': current_user.id if not current_user.is_anonymous else None,
            'user_agent': request.user_agent.string,
            'params': dict(request.args),
        }

    def _log_request_end(self, context):
        """記錄請求結束"""
        response = context['response']
        elapsed = context['elapsed_time']

        g.api_log.update({
            'status_code': response.status_code,
            'elapsed_seconds': round(elapsed, 3),
            'response_size': len(response.data) if response.data else 0,
        })

        # 寫入日誌
        self.logger.info(
            f"{g.api_log['method']} {g.api_log['path']} "
            f"→ {g.api_log['status_code']} ({g.api_log['elapsed_seconds']}s) "
            f"user:{g.api_log['user_id']}"
        )

        # 當響應時間過長時發出警告
        if elapsed > 1.0:
            self.logger.warning(
                f"Slow request: {g.api_log['method']} "
                f"{g.api_log['path']} took {elapsed:.2f}s"
            )

    def _log_request_error(self, context):
        """記錄錯誤"""
        error = context['error']
        request = context['request']

        self.logger.error(
            f"Error in {request.method} {request.path}: {error}",
            exc_info=error
        )

# 在應用初始化時註冊
logger = APIRequestLogger(app)
logger.register_hooks()
```

#### 場景 B：安全審計 - 追蹤所有敏感操作

```python
class SecurityAuditTracker:
    """
    追蹤敏感操作：
    - 誰（user_id）
    - 做什麼（operation）
    - 在何時（timestamp）
    - 結果如何（status）
    """

    def __init__(self, app):
        self.app = app
        self.audit_db = AuditDatabase(app)

    def register_hooks(self):
        self.app.hook_manager.register_hook(
            'request_after_processing',
            callback=self._track_sensitive_operations,
            priority=700
        )

    def _track_sensitive_operations(self, context):
        """追蹤敏感操作"""
        request = context['request']
        response = context['response']

        # 只記錄敏感操作（例如 PUT/DELETE）
        if request.method in ('PUT', 'DELETE', 'POST'):
            if self._is_sensitive_path(request.path):
                self.audit_db.create_audit_record(
                    user_id=current_user.id if not current_user.is_anonymous else None,
                    operation=f"{request.method} {request.path}",
                    status_code=response.status_code,
                    timestamp=datetime.now(),
                    ip_address=request.remote_addr,
                    user_agent=request.user_agent.string
                )

    def _is_sensitive_path(self, path):
        """判斷路徑是否敏感"""
        sensitive_prefixes = ['/api/users', '/api/settings', '/api/admin']
        return any(path.startswith(prefix) for prefix in sensitive_prefixes)

# 在應用初始化時註冊
audit = SecurityAuditTracker(app)
audit.register_hooks()
```

#### 場景 C：性能監控 - 追蹤慢請求

```python
class PerformanceMonitor:
    """
    監控應用性能：
    - 記錄所有請求耗時分佈
    - 識別慢請求
    - 計算 P50、P95、P99 等指標
    """

    SLOW_REQUEST_THRESHOLD = 1.0  # 超過 1 秒視為慢請求

    def __init__(self, app):
        self.app = app
        self.request_times = collections.defaultdict(list)

    def register_hooks(self):
        self.app.hook_manager.register_hook(
            'request_after_processing',
            callback=self._record_performance,
            priority=750
        )

    def _record_performance(self, context):
        """記錄性能指標"""
        request = context['request']
        elapsed = context['elapsed_time']

        endpoint = f"{request.method} {request.blueprint}:{request.endpoint}" if request.endpoint else "unknown"
        self.request_times[endpoint].append(elapsed)

        # 當請求過慢時，發出警告給管理員
        if elapsed > self.SLOW_REQUEST_THRESHOLD:
            self._alert_slow_request(endpoint, elapsed)

    def _alert_slow_request(self, endpoint, elapsed):
        """發送警告"""
        # 可以發送郵件、Slack 訊息等
        alert_message = f"慢請求警告：{endpoint} 耗時 {elapsed:.2f}s"
        # send_alert(alert_message)
        pass

    def get_statistics(self, endpoint=None):
        """獲取性能統計"""
        if endpoint:
            times = self.request_times.get(endpoint, [])
        else:
            times = [t for times in self.request_times.values() for t in times]

        if not times:
            return None

        return {
            'count': len(times),
            'avg': statistics.mean(times),
            'median': statistics.median(times),
            'p95': numpy.percentile(times, 95),
            'p99': numpy.percentile(times, 99),
            'max': max(times),
            'min': min(times),
        }
```

#### 場景 D：請求上下文初始化 - 為每個請求準備資源

```python
class RequestContextInitializer:
    """
    在每個請求開始時初始化上下文資源：
    - 用戶偏好設置
    - 資料庫連接
    - 快取連接
    - 權限檢查
    """

    def __init__(self, app):
        self.app = app

    def register_hooks(self):
        self.app.hook_manager.register_hook(
            'request_before_processing',
            callback=self._initialize_context,
            priority=10  # 早期優先級，首先初始化
        )

        self.app.hook_manager.register_hook(
            'request_teardown',
            callback=self._cleanup_context,
            priority=100
        )

    def _initialize_context(self, context):
        """初始化請求上下文"""
        request = context['request']

        # 初始化 g 對象中的資源
        if not current_user.is_anonymous:
            g.user_preferences = UserPreference.get_for_user(current_user.id)
            g.user_permissions = Permission.get_for_user(current_user.id)
            g.user_locale = g.user_preferences.get('locale', 'en')
        else:
            g.user_preferences = {}
            g.user_permissions = set()
            g.user_locale = request.accept_languages.best_match(['en', 'zh-TW', 'zh-CN'])

        # 初始化請求統計
        g.db_queries = []
        g.cache_hits = 0
        g.cache_misses = 0

    def _cleanup_context(self, context):
        """清理請求上下文"""
        # 記錄請求統計
        if hasattr(g, 'db_queries'):
            logging.info(f"Database queries in request: {len(g.db_queries)}")

        if hasattr(g, 'cache_hits'):
            cache_hit_rate = g.cache_hits / (g.cache_hits + g.cache_misses) if (g.cache_hits + g.cache_misses) > 0 else 0
            logging.info(f"Cache hit rate: {cache_hit_rate:.1%}")
```

---

## 場景 2：Database Model Operation 中的 Hooks

### 2.1 架構概述

在 `model_hook.py` 中，定義了 `ModelHookMixin` 類，提供了在資料庫操作時觸發 hooks 的機制：

```
Model.save() 或 Model.delete() 被調用
    ↓
[before_save/before_delete]     ← Layer 3: 'model_before_save' hook
    ↓
實際的 SQLAlchemy 操作
    ↓
[after_save/after_delete]       ← Layer 3: 'model_after_save' hook
    ↓
[after_create]（僅新建）         ← Layer 3: 'model_after_create' hook
    ↓
Operation 完成
```

### 2.2 具體實現

```python
class ModelHookMixin:
    """提供資料庫操作的 Hook 觸發點"""

    def save(self, session: Session, app: Optional['Flask'] = None, commit: bool = True):
        """儲存物件，觸發 before_save 和 after_save hooks"""

        is_new = session.is_modified(self, include_collections=False) if self in session else True

        # =================== 前置 Hook ===================
        # Layer 3: 全局 hook（應用級別）
        if app and hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_before_save',
                model=self,
                model_class=self.__class__,
                session=session,
                is_new=is_new
            )

        # =================== 實際操作 ===================
        session.add(self)
        if commit:
            session.commit()

        # =================== 後置 Hook ===================
        if app and hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_after_save',
                model=self,
                model_class=self.__class__,
                session=session,
                is_new=is_new
            )

            # 新建時額外觸發 after_create
            if is_new:
                app.hook_manager.call_hook(
                    'model_after_create',
                    model=self,
                    model_class=self.__class__,
                    session=session
                )

        return self

    def delete(self, session: Session, app: Optional['Flask'] = None, commit: bool = True):
        """刪除物件，觸發 before_delete 和 after_delete hooks"""

        # =================== 前置 Hook ===================
        if app and hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_before_delete',
                model=self,
                model_class=self.__class__,
                session=session
            )

        # =================== 實際操作 ===================
        session.delete(self)
        if commit:
            session.commit()

        # =================== 後置 Hook ===================
        if app and hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_after_delete',
                model=self,
                model_class=self.__class__,
                session=session
            )
```

### 2.3 實務應用場景

#### 場景 A：自動版本控制 - 追蹤資料變更歷史

```python
class ChangeTracker:
    """
    使用 model_before_save/after_save hooks 自動追蹤資料變更
    """

    def __init__(self, app):
        self.app = app

    def register_hooks(self):
        self.app.hook_manager.register_hook(
            'model_before_save',
            callback=self._capture_old_values,
            priority=10  # 早期執行，捕捉舊值
        )

        self.app.hook_manager.register_hook(
            'model_after_save',
            callback=self._record_change,
            priority=900  # 後期執行，記錄變更
        )

    def _capture_old_values(self, context):
        """在保存前捕捉舊值"""
        model = context['model']

        # 使用 SQLAlchemy 的檢查機制獲取舊值
        mapper = inspect(model.__class__)
        model._old_values = {}

        for column in mapper.columns:
            attr_name = column.name
            if hasattr(model, attr_name):
                model._old_values[attr_name] = getattr(model, attr_name)

    def _record_change(self, context):
        """保存後記錄變更"""
        model = context['model']
        model_class = context['model_class']

        old_values = getattr(model, '_old_values', {})

        # 比較新舊值，找出哪些字段被修改了
        mapper = inspect(model_class)
        changes = {}

        for column in mapper.columns:
            attr_name = column.name
            new_value = getattr(model, attr_name, None)
            old_value = old_values.get(attr_name)

            if new_value != old_value:
                changes[attr_name] = {
                    'old': old_value,
                    'new': new_value
                }

        if changes:
            # 記錄到 ChangeLog 表
            ChangeLog.create(
                model_name=model_class.__name__,
                model_id=model.id,
                user_id=current_user.id if not current_user.is_anonymous else None,
                changes=json.dumps(changes),
                timestamp=datetime.now()
            )

# 在應用初始化時註冊
tracker = ChangeTracker(app)
tracker.register_hooks()
```

#### 場景 B：全文搜索索引更新 - 自動同步搜索引擎

```python
class SearchIndexManager:
    """
    當資料保存或刪除時，自動更新搜索引擎索引
    （例如 Elasticsearch）
    """

    def __init__(self, app, es_client):
        self.app = app
        self.es = es_client

    def register_hooks(self):
        self.app.hook_manager.register_hook(
            'model_after_save',
            callback=self._update_search_index,
            priority=800
        )

        self.app.hook_manager.register_hook(
            'model_after_delete',
            callback=self._remove_search_index,
            priority=800
        )

    def _update_search_index(self, context):
        """保存後更新搜索索引"""
        model = context['model']
        model_class = context['model_class']

        # 只更新支持搜索的模型
        if not hasattr(model, 'to_search_doc'):
            return

        doc = model.to_search_doc()
        index_name = f"{model_class.__name__.lower()}_index"

        try:
            self.es.index(
                index=index_name,
                id=model.id,
                body=doc
            )
            logging.info(f"Updated search index for {model_class.__name__}:{model.id}")
        except Exception as e:
            logging.error(f"Failed to update search index: {e}")

    def _remove_search_index(self, context):
        """刪除後移除搜索索引"""
        model = context['model']
        model_class = context['model_class']
        index_name = f"{model_class.__name__.lower()}_index"

        try:
            self.es.delete(
                index=index_name,
                id=model.id
            )
            logging.info(f"Removed search index for {model_class.__name__}:{model.id}")
        except Exception as e:
            logging.error(f"Failed to remove search index: {e}")

# 在應用初始化時註冊
search_manager = SearchIndexManager(app, es_client)
search_manager.register_hooks()
```

#### 場景 C：業務邏輯驗證 - 保存前檢查業務規則

```python
class BusinessRuleValidator:
    """
    在保存前驗證業務規則
    例如：檢查庫存、驗證訂單狀態轉換等
    """

    def __init__(self, app):
        self.app = app

    def register_hooks(self):
        self.app.hook_manager.register_hook(
            'model_before_save',
            callback=self._validate_order,
            priority=50  # 較早執行，防止無效數據進入資料庫
        )

    def _validate_order(self, context):
        """驗證訂單業務規則"""
        model = context['model']

        # 只驗證 Order 模型
        if not isinstance(model, Order):
            return

        # 檢查狀態轉換的有效性
        if hasattr(model, '_old_status'):
            old_status = model._old_status
            new_status = model.status

            valid_transitions = {
                'PENDING': ['CONFIRMED', 'CANCELLED'],
                'CONFIRMED': ['SHIPPED', 'CANCELLED'],
                'SHIPPED': ['DELIVERED', 'RETURNED'],
                'DELIVERED': ['RETURNED'],
                'CANCELLED': [],
            }

            if new_status not in valid_transitions.get(old_status, []):
                raise ValueError(
                    f"Invalid order status transition: {old_status} → {new_status}"
                )

        # 檢查庫存
        for item in model.items:
            product = item.product
            if product.stock < item.quantity:
                raise ValueError(
                    f"Insufficient stock for {product.name}: "
                    f"need {item.quantity}, have {product.stock}"
                )

# 在應用初始化時註冊
validator = BusinessRuleValidator(app)
validator.register_hooks()
```

#### 場景 D：快取同步 - 保存後更新應用快取

```python
class CacheSynchronizer:
    """
    當資料變更時，自動更新相關快取
    確保快取和資料庫保持同步
    """

    def __init__(self, app, cache):
        self.app = app
        self.cache = cache

    def register_hooks(self):
        self.app.hook_manager.register_hook(
            'model_after_save',
            callback=self._invalidate_cache,
            priority=750
        )

        self.app.hook_manager.register_hook(
            'model_after_delete',
            callback=self._invalidate_cache,
            priority=750
        )

    def _invalidate_cache(self, context):
        """使快取失效"""
        model = context['model']
        model_class = context['model_class']

        # 根據模型類型清除對應的快取
        cache_patterns = {
            'User': [f'user_{model.id}', 'user_list'],
            'Product': [f'product_{model.id}', 'product_list', 'category_*'],
            'Order': [f'order_{model.id}', f'user_{model.user_id}_orders'],
        }

        patterns = cache_patterns.get(model_class.__name__, [])
        for pattern in patterns:
            self.cache.delete_many(self.cache.keys(pattern))

        logging.info(f"Invalidated cache for {model_class.__name__}:{model.id}")
```

---

## 場景 3：Template/UI Rendering 中的 Hooks

### 3.1 架構概述

在 `base.html` 中，定義了多個 hook 觸發點，允許 plugins 在模板渲染的不同階段注入內容：

```
HTML 模板開始渲染
    ↓
<head>
    ...
    {{ call_hook('view_layouts_base_html_head') }}
    ↑
    Plugin 可以在這裡注入 CSS、meta 標籤等
    ↓
</head>

<body>
    {{ g.mainmenu|safe }}

    <div class="page-body">
        {{ call_hook('view_layouts_base_content_top') }}

        {% block page_body %}...{% endblock %}

        {{ call_hook('view_layouts_base_content_bottom') }}
    </div>

    ...

    {{ call_hook('view_layouts_base_body_bottom') }}
    ↑
    Plugin 可以在這裡注入 JavaScript、追蹤代碼等
</body>
```

### 3.2 具體實現

在 `appbase.py` 中，定義了 Jinja2 的 `call_hook` 函數：

```python
def register_jinja_filters(self):
    """註冊 Jinja2 過濾器和全局函數"""

    if hasattr(self, 'hook_manager'):
        # 定義 call_hook 全局函數
        def call_hook(hook_name):
            """在模板中調用全局 hooks"""
            results = self.hook_manager.call_hook(
                hook_name,
                app=self
            )
            # 將結果連接成字符串
            return ''.join(str(r) for r in results if r)

        # 註冊為 Jinja2 全局函數
        self.jinja_env.globals['call_hook'] = call_hook
```

### 3.3 實務應用場景

#### 場景 A：動態注入 CSS 和 JavaScript

```python
class ThemePlugin(EnhancedViewPlugin):
    """
    允許動態注入 CSS 和 JavaScript
    """

    def __init__(self, app):
        super().__init__(app)
        self.name = 'ThemePlugin'
        self.bp_name = 'theme'

    def _on_start(self):
        """在插件啟動時註冊 hooks"""

        # 在 <head> 中注入自定義 CSS
        self.app.hook_manager.register_hook(
            'view_layouts_base_html_head',
            callback=self._inject_custom_css,
            priority=500
        )

        # 在 </body> 前注入自定義 JavaScript
        self.app.hook_manager.register_hook(
            'view_layouts_base_body_bottom',
            callback=self._inject_custom_js,
            priority=500
        )

    def _inject_custom_css(self, context):
        """注入 CSS"""
        theme = g.user_preferences.get('theme', 'light')
        return f"""
        <link rel="stylesheet" href="/static/css/theme-{theme}.css">
        <link rel="stylesheet" href="/static/css/custom.css">
        """

    def _inject_custom_js(self, context):
        """注入 JavaScript"""
        return """
        <script src="/static/js/theme-switcher.js"></script>
        <script>
            // 初始化主題切換器
            ThemeSwitcher.init();
        </script>
        """
```

#### 場景 B：頁面內容增強 - 注入額外的 UI 元素

```python
class AnalyticsPlugin(EnhancedViewPlugin):
    """
    在頁面中注入分析和監控代碼
    """

    def __init__(self, app):
        super().__init__(app)
        self.name = 'AnalyticsPlugin'

    def _on_start(self):
        """註冊分析相關的 hooks"""

        # 在內容頂部注入分析欄
        self.app.hook_manager.register_hook(
            'view_layouts_base_content_top',
            callback=self._inject_analytics_dashboard,
            priority=100
        )

        # 在內容底部注入分析追蹤碼
        self.app.hook_manager.register_hook(
            'view_layouts_base_body_bottom',
            callback=self._inject_tracking_code,
            priority=600
        )

    def _inject_analytics_dashboard(self, context):
        """注入分析儀表板"""
        if not current_user.is_anonymous and current_user.is_admin:
            return """
            <div class="analytics-bar" style="background: #f5f5f5; padding: 10px; margin-bottom: 20px;">
                <span>📊 Page Views: <strong>{{ page_stats.views }}</strong></span>
                <span>⏱️ Avg Load Time: <strong>{{ page_stats.avg_load_time }}ms</strong></span>
                <span>👥 Current Users: <strong>{{ page_stats.current_users }}</strong></span>
            </div>
            """
        return ""

    def _inject_tracking_code(self, context):
        """注入分析追蹤碼"""
        return """
        <script>
        // Google Analytics
        window.dataLayer = window.dataLayer || [];
        function gtag(){dataLayer.push(arguments);}
        gtag('js', new Date());
        gtag('config', 'GA_MEASUREMENT_ID');

        // Custom page view tracking
        fetch('/api/analytics/page-view', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                path: window.location.pathname,
                title: document.title,
                referrer: document.referrer,
                timestamp: new Date().toISOString()
            })
        });
        </script>
        """
```

#### 場景 C：用戶通知系統 - 動態顯示通知

```python
class NotificationPlugin(EnhancedViewPlugin):
    """
    在頁面頂部動態顯示用戶通知
    - 系統消息
    - 警告
    - 成功提示
    """

    def __init__(self, app):
        super().__init__(app)
        self.name = 'NotificationPlugin'

    def _on_start(self):
        """註冊通知相關的 hooks"""

        self.app.hook_manager.register_hook(
            'view_layouts_base_content_top',
            callback=self._render_notifications,
            priority=10  # 最早優先級，顯示在內容最頂部
        )

    def _render_notifications(self, context):
        """渲染通知 HTML"""
        if current_user.is_anonymous:
            return ""

        # 獲取未讀通知
        notifications = Notification.get_unread_for_user(current_user.id)

        if not notifications:
            return ""

        html = '<div class="notification-stack">'

        for notif in notifications:
            html += f"""
            <div class="notification notification-{notif.level}"
                 data-id="{notif.id}">
                <div class="notification-content">
                    <strong>{notif.title}</strong>
                    <p>{notif.message}</p>
                </div>
                <button class="notification-close" onclick="closeNotification({notif.id})">
                    ✕
                </button>
            </div>
            """

        html += '</div>'
        html += """
        <style>
        .notification-stack {
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 9999;
            max-width: 400px;
        }
        .notification {
            background: white;
            border-left: 4px solid #007bff;
            padding: 15px;
            margin-bottom: 10px;
            border-radius: 4px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        .notification-error { border-left-color: #dc3545; }
        .notification-warning { border-left-color: #ffc107; }
        .notification-success { border-left-color: #28a745; }
        </style>
        <script>
        function closeNotification(id) {
            const el = document.querySelector(`[data-id="${id}"]`);
            el.style.display = 'none';
            // 標記為已讀
            fetch('/api/notifications/mark-read', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id})
            });
        }
        </script>
        """

        return html
```

#### 場景 D：SEO 優化 - 動態注入 Meta 標籤和結構化數據

```python
class SEOPlugin(EnhancedViewPlugin):
    """
    為每個頁面動態注入：
    - Meta 標籤（description, keywords）
    - Open Graph 標籤（社交媒體分享）
    - 結構化數據（Schema.org JSON-LD）
    """

    def __init__(self, app):
        super().__init__(app)
        self.name = 'SEOPlugin'

    def _on_start(self):
        """註冊 SEO 相關的 hooks"""

        self.app.hook_manager.register_hook(
            'view_layouts_base_html_head',
            callback=self._inject_meta_tags,
            priority=200
        )

    def _inject_meta_tags(self, context):
        """注入 Meta 標籤"""
        # 根據當前頁面生成 meta 信息
        page_info = self._get_page_info()

        if not page_info:
            return ""

        html = f"""
        <!-- SEO Meta Tags -->
        <meta name="description" content="{page_info['description']}">
        <meta name="keywords" content="{page_info['keywords']}">
        <meta name="author" content="{page_info['author']}">

        <!-- Open Graph for Social Media -->
        <meta property="og:title" content="{page_info['title']}">
        <meta property="og:description" content="{page_info['description']}">
        <meta property="og:image" content="{page_info['image']}">
        <meta property="og:url" content="{page_info['url']}">

        <!-- Twitter Card -->
        <meta name="twitter:card" content="summary_large_image">
        <meta name="twitter:title" content="{page_info['title']}">
        <meta name="twitter:description" content="{page_info['description']}">

        <!-- Canonical URL -->
        <link rel="canonical" href="{page_info['url']}">

        <!-- Structured Data (JSON-LD) -->
        <script type="application/ld+json">
        {json.dumps(page_info['structured_data'])}
        </script>
        """

        return html

    def _get_page_info(self):
        """根據當前頁面獲取 SEO 信息"""
        # 這可以從路由、模型或頁面配置中獲取
        if request.endpoint == 'products.detail':
            product_id = request.args.get('id')
            product = Product.get_by_id(product_id)

            if product:
                return {
                    'title': product.name,
                    'description': product.description[:160],
                    'keywords': ','.join(product.tags),
                    'author': product.vendor.name,
                    'image': product.main_image_url,
                    'url': request.base_url,
                    'structured_data': {
                        '@context': 'https://schema.org/',
                        '@type': 'Product',
                        'name': product.name,
                        'description': product.description,
                        'image': product.main_image_url,
                        'offers': {
                            '@type': 'Offer',
                            'price': product.price,
                            'priceCurrency': 'TWD',
                        }
                    }
                }

        return None
```

---

## 場景 4：高級整合 - 跨層級的完整場景

### 4.1 完整電商訂單流程中的 Hooks

這個場景展示了如何在一個完整的業務流程中整合三個層級的 hooks：

```python
class OrderManagementSystem:
    """
    完整的訂單管理系統，演示跨越 Layer 3 的三個場景：

    1. HTTP Request Handler - 追蹤訂單提交
    2. Model Hook - 驗證和同步
    3. Template Hook - UI 更新
    """

    def __init__(self, app):
        self.app = app

    def register_all_hooks(self):
        """註冊所有 hooks"""

        # ======================== 場景 1: Request Handler ========================

        # 在訂單提交前驗證用戶會話
        self.app.hook_manager.register_hook(
            'request_before_processing',
            callback=self._validate_order_session,
            priority=50
        )

        # 在訂單提交後記錄日誌
        self.app.hook_manager.register_hook(
            'request_after_processing',
            callback=self._log_order_request,
            priority=750
        )

        # ======================== 場景 2: Model Hook ========================

        # 在訂單保存前驗證商品庫存
        self.app.hook_manager.register_hook(
            'model_before_save',
            callback=self._validate_inventory,
            priority=50
        )

        # 在訂單保存後更新庫存和搜索索引
        self.app.hook_manager.register_hook(
            'model_after_save',
            callback=self._update_inventory_and_index,
            priority=800
        )

        # ======================== 場景 3: Template Hook ========================

        # 在頁面內容頂部顯示訂單統計
        self.app.hook_manager.register_hook(
            'view_layouts_base_content_top',
            callback=self._show_order_summary,
            priority=100
        )

    # ======================== Request Handler Hooks ========================

    def _validate_order_session(self, context):
        """驗證訂單提交會話"""
        request = context['request']

        if request.endpoint and 'order' in request.endpoint:
            if current_user.is_anonymous:
                raise AccessDenied("Must be logged in to submit order")

            # 驗證用戶地址信息是否完整
            if not current_user.default_address:
                raise ValidationError("Please set a default shipping address first")

    def _log_order_request(self, context):
        """記錄訂單相關的 HTTP 請求"""
        request = context['request']
        response = context['response']

        if request.endpoint and 'order.create' in request.endpoint:
            # 只記錄訂單創建成功
            if response.status_code == 201:
                g.logger.info(
                    f"Order created successfully by user {current_user.id} "
                    f"in {context['elapsed_time']:.2f}s"
                )

    # ======================== Model Hooks ========================

    def _validate_inventory(self, context):
        """驗證庫存（保存前）"""
        model = context['model']

        if not isinstance(model, Order):
            return

        # 檢查每個訂單項目的庫存
        for item in model.items:
            if item.product.stock < item.quantity:
                raise InsufficientInventory(
                    f"Only {item.product.stock} units of "
                    f"{item.product.name} available"
                )

    def _update_inventory_and_index(self, context):
        """更新库存和索引（保存後）"""
        model = context['model']

        if not isinstance(model, Order):
            return

        # 扣除库存
        for item in model.items:
            item.product.stock -= item.quantity
            item.product.save(session=db.session, app=self.app)

        # 更新搜索索引（如果存在搜索功能）
        if hasattr(model, 'to_search_doc'):
            es.index(
                index='orders',
                id=model.id,
                body=model.to_search_doc()
            )

    # ======================== Template Hooks ========================

    def _show_order_summary(self, context):
        """在頁面頂部顯示訂單摘要"""
        if current_user.is_anonymous or request.endpoint != 'orders.list':
            return ""

        # 獲取用戶最近的訂單
        recent_orders = Order.query.filter_by(
            user_id=current_user.id
        ).order_by(Order.created_at.desc()).limit(5).all()

        html = '<div class="order-summary">'
        html += f'<h3>最近訂單 ({len(recent_orders)})</h3>'

        for order in recent_orders:
            html += f"""
            <div class="order-item">
                <span>訂單 #{order.id}</span>
                <span>{order.created_at.strftime('%Y-%m-%d')}</span>
                <span class="badge badge-{order.status}">
                    {order.get_status_display()}
                </span>
            </div>
            """

        html += '</div>'
        return html
```

---

## 總結

| 場景 | Hook 觸發點 | 典型用途 | 優先級建議 |
|------|-----------|--------|---------|
| **Request Handler** | `request_before/after_processing` | 日誌、監控、安全審計 | before: 10-50, after: 700-800 |
| **Model Save** | `model_before/after_save` | 驗證、索引、快取同步 | before: 50, after: 750-800 |
| **Model Delete** | `model_before/after_delete` | 清理、審計、索引移除 | before: 50, after: 800 |
| **Template Render** | `view_layouts_base_*` | CSS/JS 注入、UI 增強、SEO | 100-600 |

---

**相關文檔：**
- [PLUGIN_LIFECYCLE_ARCHITECTURE.md](PLUGIN_LIFECYCLE_ARCHITECTURE.md) - 架構深入說明
- [PLUGIN_LIFECYCLE_EXAMPLES.md](PLUGIN_LIFECYCLE_EXAMPLES.md) - 更多代碼示例
- [PLUGIN_LIFECYCLE_FAQ.md](PLUGIN_LIFECYCLE_FAQ.md) - 常見問題
