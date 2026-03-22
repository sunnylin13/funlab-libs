from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from flask import Flask


def test_admin_required_forbids_in_public_mode(monkeypatch):
    from funlab.core import auth as auth_mod

    app = Flask(__name__, template_folder='d:/08.dev/fundlife/funlab-flaskr/funlab/flaskr/templates')
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

    app = Flask(__name__, template_folder='d:/08.dev/fundlife/funlab-flaskr/funlab/flaskr/templates')
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