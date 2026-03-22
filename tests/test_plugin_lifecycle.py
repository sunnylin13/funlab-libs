from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest


class _HookManagerStub:
    def call_hook(self, *args, **kwargs):
        pass

    def register_hook(self, *args, **kwargs):
        pass


def _make_app(hook_spy=None):
    app = MagicMock()
    app.extensions = {}
    app.plugins = {}

    if hook_spy is None:
        app.hook_manager = _HookManagerStub()
    else:
        hm = MagicMock()
        hm.call_hook = lambda name, **kwargs: hook_spy.append(name)
        hm.register_hook = MagicMock()
        app.hook_manager = hm

    from funlab.core.config import Config
    cfg = Config({})
    cfg._env_vars = {}
    app.get_section_config.return_value = cfg
    app._config = MagicMock()
    app._config._env_vars = {}
    return app


def _make_plugin_cls(base_cls):
    def _fake_init_blueprint(self, url_prefix=None):
        self.bp_name = self.name + "_bp"
        self._blueprint = MagicMock()

    attrs = {
        "_init_blueprint": _fake_init_blueprint,
        "_init_configuration": lambda self: setattr(
            self,
            "plugin_config",
            MagicMock(**{"get.return_value": False, "as_dict.return_value": {}}),
        ),
    }
    return type("_TestPlugin", (base_cls,), attrs)


@pytest.fixture
def app():
    return _make_app()


@pytest.fixture
def plugin_cls():
    from funlab.core.plugin import Plugin
    return _make_plugin_cls(Plugin)


@pytest.fixture
def service_cls():
    from funlab.core.plugin import ServicePlugin
    return _make_plugin_cls(ServicePlugin)


@pytest.fixture
def security_cls():
    with patch("flask_login.LoginManager", MagicMock()):
        from funlab.core.plugin import SecurityPlugin
        return _make_plugin_cls(SecurityPlugin)


class TestLifecycle:
    def test_initial_state_ready(self, app, plugin_cls):
        from funlab.core.plugin import PluginLifecycleState
        p = plugin_cls(app)
        assert p.state == PluginLifecycleState.READY

    def test_start_stop_reload(self, app, plugin_cls):
        from funlab.core.plugin import PluginLifecycleState
        p = plugin_cls(app)
        assert p.start() is True
        assert p.state == PluginLifecycleState.RUNNING
        assert p.reload() is True
        assert p.state == PluginLifecycleState.RUNNING
        assert p.stop() is True
        assert p.state == PluginLifecycleState.STOPPED

    def test_start_idempotent(self, app, plugin_cls):
        p = plugin_cls(app)
        calls = []
        p._on_start = lambda: calls.append(1)
        assert p.start() is True
        assert p.start() is True
        assert len(calls) == 1


class TestHooks:
    def test_instance_hooks_order(self, app, plugin_cls):
        fired = []
        p = plugin_cls(app)
        p.add_lifecycle_hook("before_start", lambda: fired.append("before"))
        p.add_lifecycle_hook("after_start", lambda: fired.append("after"))
        p.start()
        assert fired == ["before", "after"]

    def test_global_hooks_fired(self):
        fired = []
        app = _make_app(hook_spy=fired)
        from funlab.core.plugin import Plugin
        cls = _make_plugin_cls(Plugin)

        p = cls(app)
        assert "plugin_after_init" in fired

        fired.clear()
        p.start()
        assert "plugin_before_start" in fired
        assert "plugin_after_start" in fired


class TestSecurityService:
    def test_security_login_manager_exists(self, app, security_cls):
        p = security_cls(app)
        assert p.login_manager is not None

    def test_service_health(self, app, service_cls):
        from funlab.core.plugin import PluginLifecycleState
        p = service_cls(app)
        assert p._perform_health_check() is False
        p.start()
        assert p._perform_health_check() is True
        p._state = PluginLifecycleState.ERROR
        assert p.health_check() is False

    def test_default_route_policy_resolves_unbound_function(self, app, plugin_cls):
        from funlab.core.policy import is_authenticated_user

        plugin_cls.default_route_policy = is_authenticated_user
        p = plugin_cls(app)

        resolved = p._resolve_default_route_policy()
        user = SimpleNamespace(is_authenticated=True)

        assert resolved is is_authenticated_user
        assert resolved(user) is True

    def test_default_route_policy_keeps_instance_method_binding(self, app, plugin_cls):
        class _MethodPolicyPlugin(plugin_cls):
            def default_route_policy(self, user):
                return bool(getattr(user, 'is_authenticated', False))

        p = _MethodPolicyPlugin(app)
        resolved = p._resolve_default_route_policy()
        user = SimpleNamespace(is_authenticated=True)

        assert getattr(resolved, '__self__', None) is p
        assert resolved(user) is True


class TestBackgroundWorkerMixin:
    def _make_worker_plugin(self, app):
        from funlab.core.plugin import BackgroundWorkerMixin, ServicePlugin

        class _WorkerPlugin(BackgroundWorkerMixin, ServicePlugin):
            def _init_blueprint(self, url_prefix=None):
                self.bp_name = self.name + "_bp"
                self._blueprint = MagicMock()

            def _init_configuration(self):
                self.plugin_config = MagicMock(
                    **{"get.return_value": False, "as_dict.return_value": {}}
                )

        return _WorkerPlugin(app)

    def test_worker_start_stop(self, app):
        p = self._make_worker_plugin(app)
        started = threading.Event()

        def worker():
            started.set()
            p.worker_stop_event.wait(timeout=1.0)

        p.start_worker(worker)
        assert started.wait(timeout=1.0)
        p.stop_worker(timeout=1.0)


class TestNonCompat:
    def test_viewplugin_alias_removed(self):
        with pytest.raises(ImportError):
            from funlab.core.plugin import ViewPlugin  # noqa: F401

    def test_enhanced_alias_removed(self):
        with pytest.raises(ImportError):
            from funlab.core.plugin import EnhancedViewPlugin  # noqa: F401
