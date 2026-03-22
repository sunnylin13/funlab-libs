from __future__ import annotations

from types import SimpleNamespace

from funlab.core.menu import MenuItem
from funlab.core.policy import (
    has_role,
    is_authenticated_user,
    is_admin,
    is_supervisor,
)


def test_admin_policies_follow_is_admin_flag():
    user = SimpleNamespace(is_admin=True, role='manager')

    assert is_admin(user) is True
    assert is_admin(user) is True


def test_supervisor_policy_follows_role_case_insensitively():
    user = SimpleNamespace(is_admin=False, role='Supervisor')

    assert has_role(user, 'supervisor') is True
    assert is_supervisor(user) is True


def test_non_supervisor_cannot_select_other_manager_data():
    user = SimpleNamespace(is_admin=False, role='manager')

    assert is_supervisor(user) is False
    assert has_role(user, 'supervisor') is False


def test_authenticated_user_policy_helpers_follow_authentication_state():
    authenticated = SimpleNamespace(is_authenticated=True, is_admin=False, role='manager')
    anonymous = SimpleNamespace(is_authenticated=False, is_admin=False, role='manager')

    assert is_authenticated_user(authenticated) is True

    assert is_authenticated_user(anonymous) is False


def test_manager_email_scope_rule_composed_from_core_policies():
    supervisor = SimpleNamespace(is_admin=False, role='supervisor', email='lead@example.com')
    manager = SimpleNamespace(is_admin=False, role='manager', email='owner@example.com')

    requested = 'other@example.com'
    assert (requested == manager.email or is_supervisor(manager)) is False
    assert (requested == supervisor.email or is_supervisor(supervisor)) is True


def test_menu_required_policy_controls_menu_accessibility():
    item = MenuItem(title='Admin', href='/admin', required_policy=is_admin)

    admin_user = SimpleNamespace(is_admin=True, role='manager')
    normal_user = SimpleNamespace(is_admin=False, role='admin-assistant')

    assert item.is_accessible(admin_user) is True
    assert item.is_accessible(normal_user) is False


def test_scheduler_and_quote_admin_policies_follow_admin_capability():
    admin_user = SimpleNamespace(is_admin=True, role='manager')
    normal_user = SimpleNamespace(is_admin=False, role='supervisor')

    assert is_admin(admin_user) is True
    assert is_admin(normal_user) is False