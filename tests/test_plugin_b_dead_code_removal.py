"""PLUGIN-B（kanban t_c0ecb5c5）：死代碼刪除的回歸鉤。

使用者裁決 2026-09-30（artifacts/plugin-architecture-review-20260930.md §5）：
- R2：Layer2 實例 hook（add_lifecycle_hook/_execute_hooks/_lifecycle_hooks）刪除
- R4：PluginMetadata 未映射欄位（auto_enable/min_python_version/config_schema）刪除
- R5：SecurityPlugin 子類刪除（AuthView 已走 ISecurityProvider 結構協定）
- plugin_service_init hook 呼叫刪除（零生產消費）

本檔以「不存在斷言」釘死刪除結果，防止死 API 面復活。
紅線（不得回歸觸碰）：ISecurityProvider、_register_plugin_to_flask 接線、
AUTH-03 fail-closed、model_hook.py、plugin_before/after_{start,stop,reload}
六個全域 hook 觸發（R3 裁決保留）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from funlab.core.plugin import ISecurityProvider, Plugin, ServicePlugin
from funlab.core.plugin_manager import PluginLoader, PluginMetadata


# ------------------------------------------------------------------ R2 Layer2
def test_layer2_instance_hook_api_removed():
    """R2：實例 hook API 三面（註冊面/執行面/存放面）必須不存在。"""
    assert not hasattr(Plugin, "add_lifecycle_hook")
    assert not hasattr(Plugin, "_execute_hooks")
    assert not hasattr(Plugin, "_lifecycle_hooks")


def test_start_stop_reload_only_fire_global_hooks(app_factory):
    """R2 連帶：start/stop 不再走 _execute_hooks；六個全域 hook 照常觸發（R3 保留）。"""
    fired: list[str] = []
    app = app_factory(hook_spy=fired)
    cls = _make_plugin_cls(Plugin)
    p = cls(app)
    fired.clear()
    assert p.start() is True
    assert "plugin_before_start" in fired and "plugin_after_start" in fired
    fired.clear()
    assert p.stop() is True
    assert "plugin_before_stop" in fired and "plugin_after_stop" in fired


# ------------------------------------------------------------- R4 metadata 欄位
def test_metadata_dead_fields_removed():
    """R4：三個未映射欄位必須從 dataclass 移除；保留欄位不受影響。"""
    fields = set(PluginMetadata.__dataclass_fields__)
    assert "auto_enable" not in fields
    assert "min_python_version" not in fields
    assert "config_schema" not in fields
    for kept in ("name", "version", "description", "author", "dependencies",
                 "optional_dependencies", "security_mode", "provides_security",
                 "load_mode", "entry_point"):
        assert kept in fields, f"保留欄位 {kept} 不得被誤刪"


def test_old_cache_with_removed_keys_still_loads(tmp_path, monkeypatch):
    """R4 向後兼容：舊 plugin_cache.json 含已刪欄位的多餘鍵，_make_meta
    的 field_names 過濾必須讓其無痛載入（不拋错、多餘鍵被丟棄）。"""
    import funlab.core.plugin_manager as pm
    from importlib.metadata import EntryPoint

    ep = EntryPoint(name="Real", value="realpkg.module:Real", group="g")
    monkeypatch.setattr(pm, "entry_points", lambda group: [ep])
    loader = PluginLoader(tmp_path)
    key = loader.cache.get_cache_key("g")
    loader.cache.save_cache(key, {"Real": {
        "name": "Real", "version": "1.0.0", "description": "", "author": "",
        "dependencies": [], "optional_dependencies": [], "security_mode": "public",
        "provides_security": False, "load_mode": "startup",
        "entry_point": "realpkg.module:Real",
        # 廢欄舊鍵：
        "auto_enable": True, "min_python_version": "3.11", "config_schema": {}}})

    found = loader.discover_plugins("g", force_refresh=False)
    assert "Real" in found
    meta = found["Real"]
    assert meta.load_mode == "startup"
    assert not hasattr(meta, "auto_enable")
    assert not hasattr(meta, "min_python_version")
    assert not hasattr(meta, "config_schema")


# ---------------------------------------------------------- R5 SecurityPlugin
def test_security_plugin_class_removed():
    """R5：SecurityPlugin 子類必須不存在；ISecurityProvider 協定保留（紅線）。"""
    import funlab.core.plugin as plugin_mod
    assert not hasattr(plugin_mod, "SecurityPlugin")
    assert hasattr(plugin_mod, "ISecurityProvider")


def test_isecurity_provider_structural_match_untouched():
    """紅線複核：任何暴露 login_manager property 的物件仍結構匹配 ISecurityProvider。"""
    class _Provider:
        @property
        def login_manager(self):
            return object()

    assert isinstance(_Provider(), ISecurityProvider)


# ---------------------------------------------------- plugin_service_init hook
def test_service_plugin_no_longer_calls_service_init_hook(app_factory):
    """第 4 點：ServicePlugin.__init__ 不再觸發 plugin_service_init；
    plugin_after_init（紅線外的既有行為）照常。"""
    fired: list[str] = []
    app = app_factory(hook_spy=fired)
    cls = _make_plugin_cls(ServicePlugin)
    cls(app)
    assert "plugin_service_init" not in fired
    assert "plugin_after_init" in fired


# ---------------------------------------------------------------- test helpers
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
    return type("_TestPluginB", (base_cls,), attrs)


import pytest


@pytest.fixture
def app_factory():
    def _make(hook_spy=None):
        app = MagicMock()
        app.extensions = {}
        app.plugins = {}
        if hook_spy is None:
            hm = MagicMock()
            hm.call_hook = MagicMock()
            hm.register_hook = MagicMock()
            app.hook_manager = hm
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

    return _make
