import sqlalchemy as sa
import pytest
from sqlalchemy.orm import registry

from funlab.core.dbmgr import DbMgr
from funlab.core.config import Config

mapper_registry = registry()


@mapper_registry.mapped
class Widget:
    __tablename__ = "widgets"

    id = sa.Column(sa.Integer, primary_key=True)
    name = sa.Column(sa.String, nullable=False)


def _build_dbmgr(tmp_path) -> DbMgr:
    db_file = tmp_path / "test.db"
    config = Config({"url": f"sqlite:///{db_file}"})
    dbmgr = DbMgr(config)
    dbmgr.create_registry_tables(mapper_registry)
    return dbmgr


def test_session_context_commits_and_cleans(tmp_path):
    dbmgr = _build_dbmgr(tmp_path)

    with dbmgr.session_context() as session:
        session.add(Widget(name="alpha"))

    with dbmgr.session_context() as session:
        names = [row.name for row in session.execute(sa.select(Widget)).scalars().all()]

    assert names == ["alpha"]


def test_session_context_rolls_back_on_exception(tmp_path):
    dbmgr = _build_dbmgr(tmp_path)

    with pytest.raises(RuntimeError):
        with dbmgr.session_context() as session:
            session.add(Widget(name="beta"))
            raise RuntimeError("boom")

    with dbmgr.session_context() as session:
        names = [row.name for row in session.execute(sa.select(Widget)).scalars().all()]

    assert names == []


def test_remove_session_creates_new_session(tmp_path):
    dbmgr = _build_dbmgr(tmp_path)

    first_session = dbmgr.get_db_session()
    dbmgr.remove_session()
    second_session = dbmgr.get_db_session()

    assert first_session is not second_session
