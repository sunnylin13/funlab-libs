"""[QA2-補測 t_d721078d] Wave2 diff 缺口補測——funlab-libs。

覆蓋點（covfinal 基線未打的 diff 行）：
- plugin_manager discover_plugins 舊快取 load_mode 映射（234-237）
- unload_plugin app.plugins 映射清理 happy path ＋ except 防護（830-838）
- dbmgr mask_db_url 手動遮罩退回路徑（40-45）
- DataclassJSONEncoder _ts/datetime/date 欄位序列化分支（177-183）
"""
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from funlab.core import DataclassJSONEncoder
from funlab.core.dbmgr import mask_db_url
import funlab.core.plugin_manager as pm
from funlab.core.plugin_manager import (ModernPluginManager, PluginInfo,
                                        PluginMetadata, PluginLoader,
                                        PluginState)


# ---------------------------------------------------------------- plugin_manager
def _legacy_cache_entry(name, **kw):
    d = {"name": name, "version": "0.0.0", "description": "", "author": "",
         "dependencies": [], "optional_dependencies": [], "security_mode": "public",
         "provides_security": False, "auto_enable": True,
         "entry_point": f"pkg.mod:{name}"}
    d.update(kw)
    return d


def _discover_legacy(tmp_path, monkeypatch, cached):
    from importlib.metadata import EntryPoint
    ep = EntryPoint(name="Legacy", value="pkg.mod:Legacy", group="g")
    monkeypatch.setattr(pm, "entry_points", lambda group: [ep])
    loader = PluginLoader(tmp_path)
    loader.cache.save_cache(loader.cache.get_cache_key("g"), {"Legacy": cached})
    return loader.discover_plugins("g", force_refresh=False)


def test_legacy_immediate_load_maps_to_startup(tmp_path, monkeypatch):
    """舊快取無 load_mode、immediate_load=True → 必須映射 'startup'（234-235）。"""
    found = _discover_legacy(tmp_path, monkeypatch,
                             _legacy_cache_entry("Legacy", immediate_load=True, lazy_load=False))
    assert found["Legacy"].load_mode == "startup"


def test_legacy_lazy_false_maps_to_startup(tmp_path, monkeypatch):
    """immediate_load 缺、lazy_load=False → 保守映射 'startup'（236-237）。"""
    found = _discover_legacy(tmp_path, monkeypatch,
                             _legacy_cache_entry("Legacy", lazy_load=False))
    assert found["Legacy"].load_mode == "startup"


def test_legacy_lazy_true_defaults_lazy(tmp_path, monkeypatch):
    """lazy_load=True → 維持 dataclass 預設 'lazy'（238 註釋路徑）。"""
    found = _discover_legacy(tmp_path, monkeypatch,
                             _legacy_cache_entry("Legacy", lazy_load=True))
    assert found["Legacy"].load_mode == "lazy"


class _NamedInstance:
    name = "shown"

    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_unload_removes_app_plugins_mapping():
    """unload_plugin 必須清掉 app.plugins 中指向實例的映射（830-835）。"""
    mgr = ModernPluginManager(MagicMock())
    mgr.app.plugins = {}
    inst = _NamedInstance()
    info = PluginInfo(metadata=PluginMetadata(name="shown"))
    info.state = PluginState.ACTIVE
    info.instance = inst
    mgr.plugins["shown"] = info
    mgr.app.plugins["shown"] = inst

    assert mgr.unload_plugin("shown") is True
    assert inst.stopped is True
    assert "shown" not in mgr.app.plugins       # 832-833 命中後刪除
    assert info.state == PluginState.UNLOADED


def test_unload_tolerates_app_without_plugins_attr():
    """app 無 plugins 屬性（AttributeError）時卸載仍要成功收尾（836-838）。"""
    class BareApp:
        pass

    mgr = ModernPluginManager(BareApp())
    inst = _NamedInstance()
    info = PluginInfo(metadata=PluginMetadata(name="shown"))
    info.state = PluginState.ACTIVE
    info.instance = inst
    mgr.plugins["shown"] = info

    assert mgr.unload_plugin("shown") is True   # except 分支吸收後繼續收尾
    assert info.state == PluginState.UNLOADED


def test_unload_removes_second_mapping_when_keys_differ():
    """注册鍵與 instance.name 不同時，第二條 plugin_name 鍵的映射也要清掉（834-835）。"""
    class OtherName(_NamedInstance):
        name = "instance_name"

    mgr = ModernPluginManager(MagicMock())
    mgr.app.plugins = {"registry_key": None}
    inst = OtherName()
    info = PluginInfo(metadata=PluginMetadata(name="registry_key"))
    info.state = PluginState.ACTIVE
    info.instance = inst
    mgr.plugins["registry_key"] = info
    mgr.app.plugins["registry_key"] = inst      # 第一條鍵（instance_name）不命中 → 832 False

    assert mgr.unload_plugin("registry_key") is True
    assert "registry_key" not in mgr.app.plugins   # 834-835 命中第二條鍵


# ---------------------------------------------------------------- dbmgr
def test_mask_fallback_manual_when_make_url_fails(monkeypatch):
    """make_url 拋例外 → 退回手動遮罩 userinfo（40-45）。"""
    import sqlalchemy.engine as sae

    def _boom(url):
        raise Exception("unparseable")

    monkeypatch.setattr(sae, "make_url", _boom)
    out = mask_db_url("mycustomdrv://bob:secr3t@db.internal:5432/prod")
    assert out == "mycustomdrv://bob:***@db.internal:5432/prod"
    assert "secr3t" not in out


def test_mask_fallback_no_password_returns_unchanged(monkeypatch):
    """退回路徑但 userinfo 無 ':' → 原樣回傳（43 else 路徑）。"""
    import sqlalchemy.engine as sae
    monkeypatch.setattr(sae, "make_url", lambda url: (_ for _ in ()).throw(Exception("x")))
    assert mask_db_url("mycustomdrv://bob@db.internal/prod") == "mycustomdrv://bob@db.internal/prod"


def test_mask_fallback_no_at_sign_returns_unchanged(monkeypatch):
    """退回路徑且無 '@' → 原樣回傳。"""
    import sqlalchemy.engine as sae
    monkeypatch.setattr(sae, "make_url", lambda url: (_ for _ in ()).throw(Exception("x")))
    assert mask_db_url("plainstring") == "plainstring"


# ---------------------------------------------------------------- encoder
def test_encoder_ts_field_midnight_collapses_to_date():
    """_ts 欄位換算後為本地午夜 → 輸出 date（isoformat 的日期形式）（177-181）。"""
    @dataclass
    class Row:
        timestamp: float = 0.0
        created_ts: float = 0.0

    # naive datetime.timestamp() 以本地時區解釋 → 本地午夜的 utc epoch
    ts_midnight = datetime(2026, 9, 1, 0, 0, 0).timestamp()
    out = json.loads(json.dumps(Row(timestamp=ts_midnight, created_ts=ts_midnight),
                                cls=DataclassJSONEncoder))
    assert out["timestamp"] == "2026-09-01"
    assert out["created_ts"] == "2026-09-01"


def test_encoder_ts_field_non_midnight_keeps_time():
    """非午夜 _ts → 完整 isoformat（177-181 的另一分支）。"""
    @dataclass
    class Row:
        event_ts: float = 0.0

    ts = datetime(2026, 9, 1, 13, 45, 30).timestamp()
    out = json.loads(json.dumps(Row(event_ts=ts), cls=DataclassJSONEncoder))
    assert out["event_ts"].startswith("2026-09-01T13:45:30")


def test_encoder_ts_field_none():
    """_ts 為 None → None，不拋例外（178 None 分支）。"""
    @dataclass
    class Row:
        maybe_ts: float = None

    out = json.loads(json.dumps(Row(), cls=DataclassJSONEncoder))
    assert out["maybe_ts"] is None


def test_encoder_datetime_and_date_typed_fields():
    """宣告型別為 datetime/date 的欄位走 .isoformat()（182-183）。"""
    @dataclass
    class Row:
        when: datetime = field(default_factory=lambda: datetime(2026, 9, 1, 8, 0, 0))
        day: date = field(default_factory=lambda: date(2026, 9, 2))

    out = json.loads(json.dumps(Row(), cls=DataclassJSONEncoder))
    assert out["when"] == "2026-09-01T08:00:00"
    assert out["day"] == "2026-09-02"
