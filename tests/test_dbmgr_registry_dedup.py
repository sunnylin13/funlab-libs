"""LIB-17：create_registry_tables 去重以物件身份為準，不受 id 重用影響。"""
import gc

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import registry as sa_registry

from funlab.core.config import Config
from funlab.core.dbmgr import DbMgr


def _mgr(tmp_path):
    return DbMgr(Config({"url": f"sqlite:///{tmp_path / 'r.db'}"}))


def test_same_registry_creates_once(tmp_path):
    mgr = _mgr(tmp_path)
    reg = sa_registry()
    Base = reg.generate_base()

    class T(Base):
        __tablename__ = "t_once"
        id = sa.Column(sa.Integer, primary_key=True)

    mgr.create_registry_tables(reg)
    mgr.create_registry_tables(reg)  # must not raise / re-create


def test_recycled_registry_gets_created(tmp_path):
    from sqlalchemy import inspect as sa_inspect
    mgr = _mgr(tmp_path)

    def make(table_name):
        reg = sa_registry()
        Base = reg.generate_base()
        cls = type("M", (Base,), {"__tablename__": table_name,
                                  "id": sa.Column(sa.Integer, primary_key=True)})
        mgr.create_registry_tables(reg)
        del reg, Base, cls
        gc.collect()

    make("t_a")
    make("t_b")
    names = set(sa_inspect(mgr.get_db_engine()).get_table_names())
    assert {"t_a", "t_b"} <= names
