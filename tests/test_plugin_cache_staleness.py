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


def test_entry_points_do_not_leak_across_groups(tmp_path, monkeypatch):
    """R11 回歸：_entry_points 不得跨 group 累積。group A 的 entry point 在隨後
    discover group B 時不得殘留，否則 B 的 live_names 判定會把 A 的名字誤當活著，
    讓 B 快取中的幽靈列跨組復活。"""
    from importlib.metadata import EntryPoint

    ep_a = EntryPoint(name="Alpha", value="apkg.module:Alpha", group="A")
    ep_b_none_live = []  # group B 現況：無任何 live entry point

    def fake_entry_points(group):
        return [ep_a] if group == "A" else ep_b_none_live

    monkeypatch.setattr(pm, "entry_points", fake_entry_points)
    loader = PluginLoader(tmp_path)

    # 先發現 group A（填入 _entry_points），並把 Alpha 預寫進 B 的快取作幽靈列
    loader.discover_plugins("A", force_refresh=True)
    assert "Alpha" in loader._entry_points
    key_b = loader.cache.get_cache_key("B")
    loader.cache.save_cache(key_b, {"Alpha": {
        "name": "Alpha", "load_mode": "startup", "dependencies": [],
        "optional_dependencies": [], "security_mode": "public",
        "provides_security": False, "entry_point": "apkg.module:Alpha"}})

    # 再發現 group B：Alpha 對 B 而言不存在，必須被視為幽靈剔除，
    # 且不得殘留在 _entry_points（跨組污染 load_plugin_class 的 ep 查找）
    found_b = loader.discover_plugins("B", force_refresh=False)
    assert found_b == {}
    assert "Alpha" not in loader._entry_points
    assert loader.cache.load_cache(key_b) in ({}, None)


def test_mixed_discovery_failure_ends_progress_once(tmp_path, monkeypatch):
    """R12 回歸：metadata 抽取失敗的路徑只記 error＋continue，不得在迴圈內
    end_progress 後於函式尾再 end 一次（duplicate end）。"""
    from importlib.metadata import EntryPoint

    ep_ok = EntryPoint(name="Ok", value="okpkg.module:Ok", group="g")
    ep_bad = EntryPoint(name="Bad", value="badpkg.module:Bad", group="g")
    monkeypatch.setattr(pm, "entry_points", lambda group: [ep_ok, ep_bad])

    ends = []
    loader = PluginLoader(tmp_path)
    monkeypatch.setattr(
        loader.logger, "end_progress",
        lambda *a, **k: ends.append((a, k)))

    def _extract(entry_point):
        if entry_point.name == "Bad":
            raise RuntimeError("boom")
        return pm.PluginMetadata(name=entry_point.name,
                                 entry_point=f"{entry_point.module}:{entry_point.attr}")

    monkeypatch.setattr(loader, "_extract_metadata", _extract)

    found = loader.discover_plugins("g", force_refresh=True)
    assert "Ok" in found and "Bad" not in found
    assert len(ends) == 1, f"end_progress 應恰好一次，實際 {len(ends)} 次: {ends}"


def test_dead_async_loader_api_removed(tmp_path):
    """R8 回歸：無消費者的 async loader 死碼（ThreadPoolExecutor /
    load_plugin_async / _load_stats）已刪除；shutdown() 保留為明確 no-op。"""
    loader = PluginLoader(tmp_path)
    assert not hasattr(loader, "_executor")
    assert not hasattr(loader, "load_plugin_async")
    # shutdown() 仍可呼叫（呼叫鏈 appbase cleanup 保留），但不得再引用 executor
    loader.shutdown()

    from unittest.mock import MagicMock
    from funlab.core.plugin_manager import ModernPluginManager
    app = MagicMock()
    app.plugins = {}
    mgr = ModernPluginManager(app)
    assert not hasattr(mgr, "_load_stats")
