"""LIB-10：create_entity_table 失敗時必須保留原始例外鏈。"""
import pytest

from funlab.core.config import Config
from funlab.core.dbmgr import DbMgr


def test_missing_module_preserves_cause(tmp_path):
    mgr = DbMgr(Config({"url": f"sqlite:///{tmp_path / 'e.db'}"}))
    with pytest.raises(Exception) as ei:
        mgr.create_entity_table("no.such.module.Entity")
    assert "no.such.module" in str(ei.value) or "Entity" in str(ei.value)
    assert ei.value.__cause__ is not None
    assert isinstance(ei.value.__cause__, (ModuleNotFoundError, ValueError, AttributeError))
