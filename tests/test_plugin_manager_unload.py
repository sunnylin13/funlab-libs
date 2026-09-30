"""LIB-15：unload_plugin 對未載入 plugin 必須正常收尾（不再被 NameError 靜默中斷）。"""
from unittest.mock import MagicMock

from funlab.core.plugin_manager import (ModernPluginManager, PluginInfo,
                                        PluginMetadata, PluginState)


def _mgr():
    app = MagicMock()
    app.plugins = {}
    app.extensions = {}
    return ModernPluginManager(app)


def test_unload_never_loaded_plugin_finishes_state_reset():
    mgr = _mgr()
    info = PluginInfo(metadata=PluginMetadata(name="ghost"))
    info.state = PluginState.LOADED  # 模擬元資料在、instance 空的狀態
    mgr.plugins["ghost"] = info
    assert mgr.unload_plugin("ghost") is True
    assert info.state == PluginState.UNLOADED
    assert info.instance is None
    assert info.error_message is None


def test_unload_unknown_plugin_returns_false():
    assert _mgr().unload_plugin("nope") is False


def test_unload_clears_app_extensions_for_this_instance():
    """R7：Plugin.__init__ 寫 app.extensions[name]=self，unload 必須對稱清除，
    且僅當該 key 的值 is 本實例（不誤刪他人註冊）。"""
    mgr = _mgr()
    instance = MagicMock()
    instance.name = "demo"
    mgr.app.plugins["demo"] = instance
    mgr.app.extensions["demo"] = instance
    other = object()
    mgr.app.extensions["other"] = other  # 不屬於本 plugin，不得被刪

    info = PluginInfo(metadata=PluginMetadata(name="demo"))
    info.instance = instance
    mgr.plugins["demo"] = info

    assert mgr.unload_plugin("demo") is True
    assert "demo" not in mgr.app.extensions
    assert "demo" not in mgr.app.plugins
    assert mgr.app.extensions["other"] is other  # 其他實例不受影響
