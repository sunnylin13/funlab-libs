"""LIB-14：快取中已無對應 entry point 的 plugin 中繼資料必須被淘汰。"""
import funlab.core.plugin_manager as pm
from funlab.core.plugin_manager import PluginLoader


def test_ghost_metadata_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "entry_points", lambda group: [])
    loader = PluginLoader(tmp_path)
    key = loader.cache.get_cache_key("g")
    loader.cache.save_cache(key, {"Ghost": {
        "name": "Ghost", "version": "0.0.0", "description": "", "author": "",
        "dependencies": [], "optional_dependencies": [], "security_mode": "public",
        "provides_security": False, "load_mode": "startup", "auto_enable": True,
        "min_python_version": "3.11", "entry_point": "gone.module:Ghost",
        "config_schema": {}}})

    found = loader.discover_plugins("g", force_refresh=False)
    assert found == {}
    # 重新讀盤：幽靈不得復活
    assert loader.cache.load_cache(key) in ({}, None)


def test_live_metadata_survives_filter(tmp_path, monkeypatch):
    from importlib.metadata import EntryPoint

    ep = EntryPoint(name="Real", value="realpkg.module:Real", group="g")
    monkeypatch.setattr(pm, "entry_points", lambda group: [ep])
    loader = PluginLoader(tmp_path)
    key = loader.cache.get_cache_key("g")
    loader.cache.save_cache(key, {"Real": {
        "name": "Real", "load_mode": "startup", "dependencies": [],
        "optional_dependencies": [], "security_mode": "public",
        "provides_security": False, "entry_point": "realpkg.module:Real"}})

    found = loader.discover_plugins("g", force_refresh=False)
    assert "Real" in found
    assert found["Real"].load_mode == "startup"
