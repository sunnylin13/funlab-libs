"""R9（kanban t_e56e99f5）：plugin cache 目錄可經 app config['PLUGIN_CACHE_DIR'] 配置。

- 已設定 → ModernPluginManager 的 PluginLoader cache 落該目錄。
- 未設定 → 維持 cwd 現況回退，且整個進程只記一次 WARNING。
"""
import logging

import funlab.core.plugin_manager as pm
from funlab.core.plugin_manager import ModernPluginManager


class _RecordingLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *a, **k):
        self.warnings.append(str(msg))

    def info(self, msg, *a, **k):
        pass

    def debug(self, msg, *a, **k):
        pass

    def error(self, msg, *a, **k):
        pass

    def progress(self, msg, *a, **k):
        pass

    def end_progress(self, msg, *a, **k):
        pass


class _StubApp:
    """Minimal app stub: ModernPluginManager only reads .config here."""

    def __init__(self, config=None):
        self.config = dict(config or {})


def _make_manager(app, monkeypatch):
    rec = _RecordingLogger()
    monkeypatch.setattr(pm.log, "get_logger", lambda *a, **k: rec)
    monkeypatch.setattr(pm, "_CWD_FALLBACK_WARNED", False, raising=False)
    mgr = ModernPluginManager(app)
    return mgr, rec


def test_configured_cache_dir_is_used(tmp_path, monkeypatch):
    app = _StubApp({"PLUGIN_CACHE_DIR": str(tmp_path / "pc")})
    mgr, rec = _make_manager(app, monkeypatch)
    assert mgr.plugin_loader.cache.cache_dir == tmp_path / "pc"
    assert (tmp_path / "pc").is_dir()  # PluginCache 建目錄
    assert rec.warnings == []  # 已設定 → 不該有回退警告


def test_unset_falls_back_to_cwd_and_warns_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _StubApp({})
    mgr, rec = _make_manager(app, monkeypatch)
    # 現況不變：cwd/.plugin_cache
    assert mgr.plugin_loader.cache.cache_dir == tmp_path / ".plugin_cache"
    assert len(rec.warnings) == 1
    assert "PLUGIN_CACHE_DIR" in rec.warnings[0]

    # 第二次建立（同進程）不得重複轟炸 log
    rec2 = _RecordingLogger()
    monkeypatch.setattr(pm.log, "get_logger", lambda *a, **k: rec2)
    ModernPluginManager(_StubApp({}))
    assert rec2.warnings == []


def test_explicit_cache_dir_argument_still_wins(tmp_path, monkeypatch):
    """向後兼容：顯式傳入 cache_dir 的既有呼叫路徑優先於 config。"""
    app = _StubApp({"PLUGIN_CACHE_DIR": str(tmp_path / "from_config")})
    explicit = tmp_path / "explicit"
    rec = _RecordingLogger()
    monkeypatch.setattr(pm.log, "get_logger", lambda *a, **k: rec)
    mgr = ModernPluginManager(app, cache_dir=explicit)
    assert mgr.plugin_loader.cache.cache_dir == explicit
