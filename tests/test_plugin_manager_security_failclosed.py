"""AUTH-03 跨倉提案段（A6-2）：安全元件構造失敗必須 fail-closed。

根因（funlab-auth PLAN §AUTH-03 (g)）：plugin_manager 捕獲 plugin 構造例外後
僅記錄並讓 app 照常以 ``security_mode=PUBLIC`` 啟動 —— 一個設定鍵讓整站驗證
消失但服務繼續跑＝fail-open。本測試鎖定：

* ``provides_security=True`` 且 ``load_mode='startup'`` 的 plugin 構造失敗／
  start() 回 False → ``register_plugins()`` 必須丟出例外拒絕啟動（fail-closed）。
* 紅線（PLAN §AUTH-03 (g)／派工清單）：**非安全元件不得拒絕啟動**——維持現況
  僅記錄、其他 plugin 照常載入。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _BoomSecurity:
    """構造即炸的安全元件（模擬 AUTH-03 E3：AuthView __init__ AttributeError）。"""
    name = 'auth'

    def __init__(self, app):
        raise AttributeError("'bool' object has no attribute 'pop'")


class _StartFalseSecurity:
    """構造成功但 start() 回 False 的安全元件。"""
    name = 'auth'

    def __init__(self, app):
        pass

    def start(self):
        return False


class _OkSecurity:
    name = 'auth'

    def __init__(self, app):
        pass


class _OkPlain:
    name = 'plain'

    def __init__(self, app):
        pass


class _BoomPlain:
    """構造炸掉的非安全元件：只准記錄，不准拖垮啟動。"""
    def __init__(self, app):
        raise RuntimeError('non-security plugin boom')


def _meta(name, *, startup=True, provides_security=False, security_mode='public'):
    from funlab.core.plugin_manager import PluginMetadata

    return PluginMetadata(
        name=name,
        entry_point=f'fake.pkg:{name}',
        load_mode='startup' if startup else 'lazy',
        provides_security=provides_security,
        security_mode=security_mode,
    )


def _make_manager(tmp_path, metas, load_side_effect):
    """ModernPluginManager with discovery + class-loading stubbed out.

    ``load_side_effect`` maps plugin name -> class or exception.
    """
    from funlab.core.plugin_manager import ModernPluginManager

    app = MagicMock()
    app.plugins = {}
    app.extensions = {}
    app.authorization_enabled = False
    app.security_mode = 'public'
    app.login_manager = None
    app.dbmgr = None
    app.register_blueprint = MagicMock()

    manager = ModernPluginManager(app, cache_dir=tmp_path / '.plugin_cache')

    manager.plugin_loader.discover_plugins = MagicMock(return_value=dict(metas))

    def _load(plugin_name, metadata):
        result = load_side_effect[plugin_name]
        if isinstance(result, Exception):
            raise result
        return result

    manager.plugin_loader.load_plugin_class = MagicMock(side_effect=_load)
    return manager


def test_security_plugin_construct_failure_refuses_startup(tmp_path):
    """AUTH-03 根因鎖定：AuthView 構造炸 → register_plugins 必須 raise，
    而不是讓 app 以 PUBLIC 無驗證啟動。"""
    from funlab.core.plugin_manager import SecurityProviderStartupError

    metas = {'AuthView': _meta('AuthView', provides_security=True)}
    manager = _make_manager(tmp_path, metas, {'AuthView': _BoomSecurity})

    with pytest.raises(SecurityProviderStartupError) as exc:
        manager.register_plugins()
    assert 'AuthView' in str(exc.value)


def test_security_plugin_start_false_refuses_startup(tmp_path):
    from funlab.core.plugin_manager import SecurityProviderStartupError

    metas = {'AuthView': _meta('AuthView', provides_security=True)}
    manager = _make_manager(tmp_path, metas, {'AuthView': _StartFalseSecurity})

    with pytest.raises(SecurityProviderStartupError):
        manager.register_plugins()


def test_security_plugin_module_missing_refuses_startup(tmp_path):
    """provides_security 元件連模組都找不到（DISABLED 路徑）同樣不得
    fail-open 啟動。"""
    from funlab.core.plugin_manager import SecurityProviderStartupError

    metas = {'AuthView': _meta('AuthView', provides_security=True)}
    manager = _make_manager(
        tmp_path, metas,
        {'AuthView': ModuleNotFoundError("No module named 'funlab.auth'")},
    )

    with pytest.raises(SecurityProviderStartupError):
        manager.register_plugins()


def test_non_security_plugin_failure_does_not_refuse_startup(tmp_path):
    """紅線：非安全元件構造失敗維持現況——僅記錄，啟動不拒絕，
    其餘 plugin 照常載入。"""
    metas = {
        'BadPlugin': _meta('BadPlugin'),
        'Plain': _meta('Plain'),
    }
    manager = _make_manager(
        tmp_path, metas,
        {'BadPlugin': _BoomPlain, 'Plain': _OkPlain},
    )

    # 不得丟出任何例外
    manager.register_plugins()

    from funlab.core.plugin_manager import PluginState

    assert manager.get_plugin_state('BadPlugin') == PluginState.ERROR.value
    assert manager.get_plugin_state('Plain') == PluginState.ACTIVE.value


def test_non_security_required_plugin_blocked_still_starts(tmp_path):
    """紅線迴歸：security_mode='required' 的非安全元件在 PUBLIC 下被
    _can_activate_plugin 擋成 DISABLED，這是既有降級路徑，不得 raise。"""
    metas = {'FundMgrView': _meta('FundMgrView', security_mode='required')}
    manager = _make_manager(tmp_path, metas, {'FundMgrView': _OkPlain})

    manager.register_plugins()

    from funlab.core.plugin_manager import PluginState

    assert manager.get_plugin_state('FundMgrView') == PluginState.DISABLED.value


def test_security_plugin_success_does_not_raise(tmp_path):
    """正例：安全元件正常載入 → 啟動照常（現行 finfun 路徑零回歸）。"""
    metas = {'AuthView': _meta('AuthView', provides_security=True)}
    manager = _make_manager(tmp_path, metas, {'AuthView': _OkSecurity})

    manager.register_plugins()

    from funlab.core.plugin_manager import PluginState

    assert manager.get_plugin_state('AuthView') == PluginState.ACTIVE.value


def test_lazy_security_plugin_not_gated_at_startup(tmp_path):
    """範疇界定：啟動閘只管 load_mode='startup' 的安全元件（現行框架
    僅在 startup 迴圈載入 provides_security；lazy 者維持現況）。"""
    metas = {'LateAuth': _meta('LateAuth', startup=False, provides_security=True)}
    manager = _make_manager(tmp_path, metas, {'LateAuth': _BoomSecurity})

    manager.register_plugins()  # 不得 raise

    from funlab.core.plugin_manager import PluginState

    assert manager.get_plugin_state('LateAuth') == PluginState.UNLOADED.value
