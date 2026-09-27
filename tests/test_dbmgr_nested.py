"""LIB-01 回歸：session_context 巢狀使用不得提交/關閉外層交易。"""
import threading

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import registry

from funlab.core.config import Config
from funlab.core.dbmgr import DbMgr

mapper_registry = registry()


@mapper_registry.mapped
class Widget:
    __tablename__ = "widgets_nested"
    id = sa.Column(sa.Integer, primary_key=True)
    name = sa.Column(sa.String, nullable=False)


def _build_dbmgr(tmp_path) -> DbMgr:
    dbmgr = DbMgr(Config({"url": f"sqlite:///{tmp_path / 'n.db'}"}))
    dbmgr.create_registry_tables(mapper_registry)
    return dbmgr


def _names(dbmgr):
    with dbmgr.session_context() as s:
        return sorted(r.name for r in s.execute(sa.select(Widget)).scalars().all())


def test_inner_exit_does_not_commit_outer(tmp_path):
    dbmgr = _build_dbmgr(tmp_path)
    with pytest.raises(RuntimeError):
        with dbmgr.session_context() as outer:
            outer.add(Widget(id=1, name="out"))
            with dbmgr.session_context() as inner:
                inner.add(Widget(id=2, name="in"))
            outer.add(Widget(id=3, name="out2"))
            raise RuntimeError("outer abort")
    assert _names(dbmgr) == []


def test_outer_success_commits_both_levels(tmp_path):
    dbmgr = _build_dbmgr(tmp_path)
    with dbmgr.session_context() as outer:
        outer.add(Widget(id=1, name="out"))
        with dbmgr.session_context() as inner:
            inner.add(Widget(id=2, name="in"))
    assert _names(dbmgr) == ["in", "out"]


def test_nested_savepoint_rollback_keeps_outer(tmp_path):
    dbmgr = _build_dbmgr(tmp_path)
    with dbmgr.session_context() as outer:
        outer.add(Widget(id=1, name="kept"))
        with pytest.raises(ValueError):
            with dbmgr.session_context(nested=True) as inner:
                inner.add(Widget(id=2, name="lost"))
                raise ValueError("inner abort")
        outer.add(Widget(id=3, name="kept2"))
    assert _names(dbmgr) == ["kept", "kept2"]


def test_nested_savepoint_commit_keeps_both(tmp_path):
    """nested=True 正常結束：SAVEPOINT 提交，內外層寫入一起由外層 commit 落地。"""
    dbmgr = _build_dbmgr(tmp_path)
    with dbmgr.session_context() as outer:
        outer.add(Widget(id=1, name="kept"))
        with dbmgr.session_context(nested=True) as inner:
            inner.add(Widget(id=2, name="sp"))
        outer.add(Widget(id=3, name="kept2"))
    assert _names(dbmgr) == ["kept", "kept2", "sp"]


def test_nested_thread_depth_isolation(tmp_path):
    dbmgr = _build_dbmgr(tmp_path)
    failures = []

    def worker(i):
        try:
            with dbmgr.session_context() as outer:
                outer.add(Widget(id=i * 100, name=f"out-{i}"))
                with dbmgr.session_context() as inner:
                    inner.add(Widget(id=i * 100 + 1, name=f"in-{i}"))
                if i % 2 == 0:
                    raise RuntimeError("abort even")
        except RuntimeError:
            pass

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _names(dbmgr) == sorted(
        [f"out-{i}" for i in (1, 3, 5)] + [f"in-{i}" for i in (1, 3, 5)]
    )
