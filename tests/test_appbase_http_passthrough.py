"""TECHDEBT 回歸測試：errorhandler(Exception) 必須放行 HTTPException。

來源：QA t_f69f26ac 觀察 LOW-obs-1 — ``_FlaskBase.register_request_handler``
註冊的 catch-all ``errorhandler(Exception)`` 會把 HTTPException（405、404、
手動 abort 的自訂碼）一併吞掉、渲染成 500。修復後：

- HTTPException 維持原始狀態碼（不再渲染 error-500）。
- 更特定的 error handler（如 Flask 預設 404、或另 registered 的
  CSRFError 專屬 handler）不受影響 — handler 查找依 MRO，HTTPException
  放行邏輯只在通用 Exception handler 內部生效。
- 非 HTTP 例外仍走原 500 路徑（error-500 模板 + hook）。
"""
from __future__ import annotations

import pytest
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

from funlab.core.appbase import _FlaskBase

# 探針 app 設定：section 名稱必須與 class 名稱一致
# （_init_configuration 用 self.__class__.__name__ 抓 section；缺 section 時
# 整個 Config 攤平，[CACHE] 會被誤並進 Flask config）。
PROBE_CONFIG = """
[HttpPassthroughProbe]
APP_NAME = 'httppassthrough-probe'
SECRET_KEY = 'pinned-test-secret'
PREWARM_ENABLED = false
ENV = { TESTING = true, WSGI = 'flask', PORT = 5998, DEBUG = true }

[CACHE]
CACHE_TYPE = 'SimpleCache'
"""


class HttpPassthroughProbe(_FlaskBase):
    """最小 _FlaskBase 子classed；register_routes/register_menu 為 noop。"""

    def register_routes(self):
        @self.route('/get-only', methods=['GET'])
        def get_only():
            return 'ok'

        @self.route('/teapot')
        def teapot():
            from flask import abort
            abort(418)

        @self.route('/boom')
        def boom():
            raise ValueError('kaboom')

        @self.route('/csrf-reject')
        def csrf_reject():
            # 模擬 CSRFProtect 在 before_request 丟 CSRFError 的路徑
            raise CSRFError(description='The CSRF token is missing.')

    def register_menu(self):
        pass


@pytest.fixture(scope='module')
def probe_app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('httppassthrough_cfg')
    cfg = tmp / 'config.toml'
    cfg.write_text(PROBE_CONFIG, encoding='utf-8')
    app = HttpPassthroughProbe(configfile=str(cfg), envfile=None,
                               import_name='test_httppassthrough_app')
    # 對齊 funlab-flaskr conftest：本測試一律匿名，拔除 DB-backed loader。
    app.login_manager._request_callback = None

    # 專屬 CSRFError handler（funlab-flaskr _init_csrf_protection 的等价物）：
    # 必須比通用 Exception handler 優先命中，且不受放行邏輯影響。
    @app.errorhandler(CSRFError)
    def _csrf_handler(error):
        return 'csrf-rejected', 400

    app.config['PROPAGATE_EXCEPTIONS'] = False
    return app


def test_method_not_allowed_stays_405(probe_app):
    """QA LOW-obs-1 核心案例：GET-only 路由打 DELETE → 405，不再是 500。"""
    client = probe_app.test_client()
    resp = client.delete('/get-only')
    assert resp.status_code == 405


def test_method_not_allowed_json_client_stays_405(probe_app):
    """JSON client 也要拿到 405（不是被渲染成 500 JSON）。"""
    client = probe_app.test_client()
    resp = client.delete('/get-only', json={})
    assert resp.status_code == 405


def test_missing_route_stays_404(probe_app):
    """未註冊路由 → Flask 預設 404，不被 Exception handler 渲染成 500。"""
    client = probe_app.test_client()
    resp = client.get('/no-such-route')
    assert resp.status_code == 404


def test_abort_status_code_passthrough(probe_app):
    """view 內 abort(418) → 418 原樣通過（自訂 HTTPException 碼）。"""
    client = probe_app.test_client()
    resp = client.get('/teapot')
    assert resp.status_code == 418


def test_csrf_error_dedicated_handler_wins(probe_app):
    """另註冊的 CSRFError 專屬 handler 仍命中 400，不受放行邏輯影響。"""
    client = probe_app.test_client()
    resp = client.get('/csrf-reject')
    assert resp.status_code == 400
    assert resp.get_data(as_text=True) == 'csrf-rejected'


def test_non_http_exception_still_500(probe_app):
    """非 HTTP 例外維持原行為：500 + error-500 fallback 文字。"""
    client = probe_app.test_client()
    resp = client.get('/boom')
    assert resp.status_code == 500
    assert 'kaboom' in resp.get_data(as_text=True)


def test_handler_serves_httpexception_directly(probe_app):
    """白箱驗證：通用 handler 回傳的就是原 HTTPException 物件本身。"""
    # errorhandler(Exception) 的 callable 存在 app.error_handler_spec[None]
    specs = probe_app.error_handler_spec[None]
    generic = specs.get(None, {}).get(Exception)
    assert generic is not None, 'catch-all Exception handler 應已註冊'

    from werkzeug.exceptions import MethodNotAllowed
    exc = MethodNotAllowed(405)
    with probe_app.test_request_context('/get-only', method='DELETE'):
        rv = generic(exc)
    assert rv is exc, 'HTTPException 應原樣放行（同一物件），而非渲染 500'
    assert isinstance(rv, HTTPException)
