from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def _make_manager(authorization_enabled: bool = False, security_mode: str = 'public'):
    from funlab.core.plugin_manager import ModernPluginManager

    app = MagicMock()
    app.plugins = {}
    app.extensions = {}
    app.authorization_enabled = authorization_enabled
    app.security_mode = security_mode
    app.login_manager = None
    app.dbmgr = None
    app.register_blueprint = MagicMock()
    return ModernPluginManager(app)


def test_required_plugin_blocked_in_public_mode():
    from funlab.core.plugin_manager import PluginMetadata

    manager = _make_manager(authorization_enabled=False, security_mode='public')
    metadata = PluginMetadata(name='FundMgrView', security_mode='required')

    allowed, reason = manager._can_activate_plugin('FundMgrView', metadata)

    assert allowed is False
    assert reason is not None
    assert 'requires an auth provider' in reason


def test_required_plugin_allowed_in_secured_mode():
    from funlab.core.plugin_manager import PluginMetadata

    manager = _make_manager(authorization_enabled=True, security_mode='secured')
    metadata = PluginMetadata(name='FundMgrView', security_mode='required')

    allowed, reason = manager._can_activate_plugin('FundMgrView', metadata)

    assert allowed is True
    assert reason is None


def test_security_provider_allowed_before_auth_enabled():
    from funlab.core.plugin_manager import PluginMetadata

    manager = _make_manager(authorization_enabled=False, security_mode='public')
    metadata = PluginMetadata(name='AuthView', security_mode='required', provides_security=True)

    allowed, reason = manager._can_activate_plugin('AuthView', metadata)

    assert allowed is True
    assert reason is None