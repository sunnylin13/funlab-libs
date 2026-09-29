from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from flask import Flask


def test_admin_required_forbids_in_public_mode(monkeypatch):
    from funlab.core import auth as auth_mod

    app = Flask(__name__)
    app.authorization_enabled = False
    app.login_manager = MagicMock()

    monkeypatch.setattr(auth_mod, 'current_user', SimpleNamespace(is_authenticated=False, is_admin=False))
    monkeypatch.setattr(auth_mod, '_forbidden_response', lambda: ('forbidden', 403))

    @auth_mod.admin_required
    def protected():
        return 'ok'

    with app.test_request_context('/conf_data'):
        response, status = protected()

    assert status == 403
    assert app.login_manager.unauthorized.call_count == 0


def test_admin_required_delegates_unauthenticated_secured_mode(monkeypatch):
    from funlab.core import auth as auth_mod

    app = Flask(__name__)
    app.authorization_enabled = True
    app.login_manager = MagicMock()
    app.login_manager.unauthorized.return_value = ('login required', 401)

    monkeypatch.setattr(auth_mod, 'current_user', SimpleNamespace(is_authenticated=False, is_admin=False))

    @auth_mod.admin_required
    def protected():
        return 'ok'

    with app.test_request_context('/conf_data'):
        response = protected()

    assert response == ('login required', 401)
    app.login_manager.unauthorized.assert_called_once_with()


def test_admin_required_forbids_authenticated_non_admin(monkeypatch):
    from funlab.core import auth as auth_mod

    app = Flask(__name__)
    app.authorization_enabled = True
    app.login_manager = MagicMock()

    monkeypatch.setattr(auth_mod, 'current_user', SimpleNamespace(is_authenticated=True, is_admin=False, role='user'))
    monkeypatch.setattr(auth_mod, '_forbidden_response', lambda: ('forbidden', 403))

    @auth_mod.admin_required
    def protected():
        return 'ok'

    with app.test_request_context('/conf_data'):
        response, status = protected()

    assert status == 403
    assert app.login_manager.unauthorized.call_count == 0


def test_role_required_allows_authorized_user(monkeypatch):
    from funlab.core import auth as auth_mod

    app = Flask(__name__)
    app.authorization_enabled = True
    app.login_manager = MagicMock()

    monkeypatch.setattr(auth_mod, 'current_user', SimpleNamespace(is_authenticated=True, is_admin=False, role='supervisor'))

    @auth_mod.role_required(['supervisor', 'manager'])
    def protected():
        return 'ok'

    with app.test_request_context('/portfolio'):
        response = protected()

    assert response == 'ok'


def test_policy_required_forbids_when_policy_returns_false(monkeypatch):
    from funlab.core import auth as auth_mod

    app = Flask(__name__)
    app.authorization_enabled = True
    app.login_manager = MagicMock()

    monkeypatch.setattr(auth_mod, 'current_user', SimpleNamespace(is_authenticated=True, is_admin=False, role='user'))
    monkeypatch.setattr(auth_mod, '_forbidden_response', lambda: ('forbidden', 403))

    @auth_mod.policy_required(lambda user: False)
    def protected():
        return 'ok'

    with app.test_request_context('/admin'):
        response = protected()

    assert response == ('forbidden', 403)


def test_role_required_is_case_insensitive(monkeypatch):
    """ADR-045: role_required delegates to policy.has_role (case-insensitive)."""
    from funlab.core import auth as auth_mod

    app = Flask(__name__)
    app.authorization_enabled = True
    app.login_manager = MagicMock()

    monkeypatch.setattr(auth_mod, 'current_user', SimpleNamespace(is_authenticated=True, role='Admin'))

    @auth_mod.role_required(['admin'])
    def protected():
        return 'ok'

    with app.test_request_context('/admin'):
        response = protected()

    assert response == 'ok'


def test_forbidden_response_falls_back_without_template():
    """ADR-045: missing error-403.html must yield 403, not TemplateNotFound/500."""
    app = Flask(__name__)  # no error-403.html on the template search path

    from funlab.core.auth import _forbidden_response

    with app.test_request_context('/x'):
        body, status = _forbidden_response()

    assert status == 403
    assert body == 'Forbidden'


def test_default_route_policy_staticmethod_style():
    """ADR-045 convention: staticmethod(...) resolves to the bare function."""
    from funlab.core.policy import is_authenticated_user
    from funlab.core.plugin import Plugin

    class _Shim:
        default_route_policy = staticmethod(is_authenticated_user)

    resolved = Plugin._resolve_default_route_policy(_Shim())
    user = SimpleNamespace(is_authenticated=True)
    assert resolved is is_authenticated_user
    assert resolved(user) is True
