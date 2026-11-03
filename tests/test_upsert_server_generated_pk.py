# -*- coding: utf-8 -*-
"""[t_f50d2879][HR-7] upsert_entity 對「單欄整數自增 PK＝None」的插入語意。

依據（期望值獨立來源＝卡 body 條文，非照抄實作）：
- finfun-core ErrorLogEntity 鍵收斂後 PK＝單欄 bigserial log_id，寫入端
  log_id 恆為 None（庫端生成）。舊 upsert_entity 以全欄 dict 組
  INSERT .. ON CONFLICT，None 直入 VALUES → PG NOT NULL 違例——
  error_log 逐列 fallback 路徑（_one_by_one_save→upsert_entity）全數失敗。
- 修復語意：自增 PK 值 None → 排除該欄、改純 INSERT（每列必新增，
  日志表無 upsert 語意）；PK 值非 None → 既有 upsert 路徑零變動。

隔離：PG 整合測走環境 UPSERT_PG_DB_URL（未設→skip，比照 W7_AUDIT_QA_DB_URL
慣例）；零正式庫。本檔自含 stub entity（Table＋dataclass），不 import
finfun（依賴方向：finfun→funlab，反向禁止）。
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.skipif(
    not os.environ.get('UPSERT_PG_DB_URL'),
    reason='UPSERT_PG_DB_URL 未設（PG 整合測跳過）')

_meta = sa.MetaData()


class _LogStub:
    __tablename__ = 't_f50d2879_upsert_stub'

    def __init__(self, msg, log_id=None):
        self.log_id = log_id
        self.msg = msg


t_stub = sa.Table(
    _LogStub.__tablename__, _meta,
    sa.Column('log_id', sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column('msg', sa.String, nullable=False),
)


class _CompositeStub:
    """複合鍵＋整數單欄「非自增」對照組：語意零變動釘選。"""

    def __init__(self, a, b, v):
        self.a = a
        self.b = b
        self.v = v


t_comp = sa.Table(
    't_f50d2879_upsert_comp', _meta,
    sa.Column('a', sa.String, primary_key=True),
    sa.Column('b', sa.Integer, primary_key=True),
    sa.Column('v', sa.String, nullable=False),
)

# upsert_entity 只經 __table__＋屬性取值（不依賴 ORM mapper）：以類屬性掛表。
_LogStub.__table__ = t_stub
_CompositeStub.__table__ = t_comp


@pytest.fixture()
def engine():
    eng = create_engine(os.environ['UPSERT_PG_DB_URL'])
    _meta.drop_all(eng, tables=[t_stub, t_comp])
    _meta.create_all(eng, tables=[t_stub, t_comp])
    yield eng
    _meta.drop_all(eng, tables=[t_stub, t_comp])
    eng.dispose()


def test_autoinc_pk_none_double_insert_both_rows_persist(engine):
    """同 msg 兩筆 log_id=None：兩行皆落盤、log_id 庫端各自生成。"""
    from sqlalchemy.orm import Session
    from funlab.utils.db import upsert_entity
    with Session(engine) as s:
        upsert_entity(s, _LogStub('a'))
        upsert_entity(s, _LogStub('a'))  # 修復前：log_id=None 進 VALUES → NOT NULL 違例
        s.commit()
    with engine.connect() as c:
        rows = c.execute(text(
            f'SELECT log_id, msg FROM {t_stub.name} ORDER BY log_id')).all()
    assert [r.msg for r in rows] == ['a', 'a']
    assert rows[0].log_id != rows[1].log_id


def test_autoinc_pk_explicit_value_still_upserts(engine):
    """顯式帶 log_id 值：既有 ON CONFLICT DO UPDATE 路徑零變動。"""
    from sqlalchemy.orm import Session
    from funlab.utils.db import upsert_entity
    with Session(engine) as s:
        upsert_entity(s, _LogStub('v1', log_id=999001))
        upsert_entity(s, _LogStub('v2', log_id=999001))
        s.commit()
    with engine.connect() as c:
        rows = c.execute(text(
            f"SELECT msg FROM {t_stub.name} WHERE log_id=999001")).all()
    assert len(rows) == 1 and rows[0].msg == 'v2'


def test_composite_pk_path_untouched(engine):
    """複合鍵實體：仍走 upsert（衝突覆寫 v），語意零變動。"""
    from sqlalchemy.orm import Session
    from funlab.utils.db import upsert_entity
    with Session(engine) as s:
        upsert_entity(s, _CompositeStub('x', 1, 'old'))
        upsert_entity(s, _CompositeStub('x', 1, 'new'))
        s.commit()
    with engine.connect() as c:
        rows = c.execute(text(
            f"SELECT v FROM {t_comp.name} WHERE a='x' AND b=1")).all()
    assert len(rows) == 1 and rows[0].v == 'new'
