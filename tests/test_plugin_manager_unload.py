"""LIB-15：unload_plugin 對未載入 plugin 必須正常收尾（不再被 NameError 靜默中斷）。"""
from unittest.mock import MagicMock

from funlab.core.plugin_manager import (ModernPluginManager, PluginInfo,
                                        PluginMetadata, PluginState)


def _mgr():
    app = MagicMock()
    app.plugins = {}
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
