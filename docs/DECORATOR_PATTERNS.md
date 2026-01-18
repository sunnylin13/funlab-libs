# 彈性 Decorator 範例

以下是如何實作一個同時支援兩種使用模式的 decorator：

## 彈性版本的 admin_required

```python
from functools import wraps
from flask_login import current_user
from flask import render_template, redirect

def admin_required(func=None, *, redirect_url=None, message=None):
    """
    Admin required decorator that supports both usage patterns:

    用法 1：不帶括號
    @admin_required
    def my_function():
        pass

    用法 2：帶空括號
    @admin_required()
    def my_function():
        pass

    用法 3：帶自定義參數
    @admin_required(redirect_url='/unauthorized', message='需要管理員權限')
    def my_function():
        pass
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not getattr(current_user, 'is_admin', False):
                if redirect_url:
                    return redirect(redirect_url)
                else:
                    error_msg = message or "需要管理員權限才能存取此頁面"
                    return render_template('error-403.html', msg=error_msg), 403
            return f(*args, **kwargs)
        return wrapper

    # 判斷使用模式
    if func is None:
        # 被調用時帶括號：@admin_required() 或 @admin_required(redirect_url='...')
        return decorator
    else:
        # 被調用時不帶括號：@admin_required
        return decorator(func)


def role_required(roles=None, *, redirect_url=None, message=None):
    """
    彈性的角色檢查 decorator

    用法 1：簡單角色檢查
    @role_required(['admin', 'manager'])
    def my_function():
        pass

    用法 2：帶自定義錯誤處理
    @role_required(['admin'], redirect_url='/login', message='權限不足')
    def my_function():
        pass
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user_role = getattr(current_user, 'role', None)

            if roles and user_role not in roles:
                if redirect_url:
                    return redirect(redirect_url)
                else:
                    error_msg = message or f"需要以下角色之一：{', '.join(roles)}"
                    return render_template('error-403.html', msg=error_msg), 403

            return func(*args, **kwargs)
        return wrapper

    # 如果 roles 是一個函數，表示是不帶參數的裝飾器使用
    if callable(roles):
        func = roles
        roles = ['admin']  # 預設角色
        return decorator(func)

    return decorator
```

## 使用範例

```python
from flask import Blueprint
from flask_login import login_required
from your_auth_module import admin_required, role_required

bp = Blueprint('example', __name__)

# 範例 1：簡單的管理員檢查（不帶括號）
@bp.route('/admin-simple')
@login_required
@admin_required
def admin_simple():
    return "簡單管理員頁面"

# 範例 2：帶空括號的管理員檢查
@bp.route('/admin-brackets')
@login_required
@admin_required()
def admin_brackets():
    return "帶括號的管理員頁面"

# 範例 3：自定義重定向的管理員檢查
@bp.route('/admin-custom')
@login_required
@admin_required(redirect_url='/login', message='需要管理員權限登入')
def admin_custom():
    return "自定義錯誤處理的管理員頁面"

# 範例 4：角色檢查
@bp.route('/manager-only')
@login_required
@role_required(['admin', 'manager'])
def manager_only():
    return "管理員或經理才能存取"

# 範例 5：自定義角色檢查
@bp.route('/special-role')
@login_required
@role_required(['special_user'], message='需要特殊用戶權限')
def special_role():
    return "特殊角色頁面"

# 範例 6：組合使用多個裝飾器
@bp.route('/super-secure')
@login_required
@admin_required
@role_required(['super_admin'])
def super_secure():
    return "超級安全頁面"
```

## 進階範例：條件式權限檢查

```python
def conditional_admin_required(condition_func=None):
    """
    條件式管理員權限檢查

    @conditional_admin_required(lambda: datetime.now().hour < 18)
    def evening_admin_only():
        pass
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 先檢查基本管理員權限
            if not getattr(current_user, 'is_admin', False):
                return render_template('error-403.html',
                                     msg='需要管理員權限'), 403

            # 再檢查額外條件
            if condition_func and not condition_func():
                return render_template('error-403.html',
                                     msg='當前時間不允許存取'), 403

            return func(*args, **kwargs)
        return wrapper
    return decorator

# 使用範例
from datetime import datetime

@bp.route('/time-restricted-admin')
@login_required
@conditional_admin_required(lambda: 9 <= datetime.now().hour <= 17)
def time_restricted_admin():
    return "只有在工作時間內管理員才能存取"
```

## 為什麼使用彈性 Decorator？

### 優點：
1. **向後相容**：現有代碼不需要修改
2. **靈活性**：可以根據需要添加參數
3. **一致性**：統一的 API 介面
4. **擴展性**：容易添加新功能

### 使用建議：
1. **簡單場景**：使用不帶括號的模式 `@admin_required`
2. **需要自定義**：使用帶參數的模式 `@admin_required(redirect_url='/custom')`
3. **團隊協作**：統一使用其中一種模式保持一致性

目前您的 `auth.py` 中的 `admin_required` 已經是不需要括號的簡潔模式，這是推薦的做法。如果未來需要更多靈活性，可以參考上面的彈性版本進行升級。
