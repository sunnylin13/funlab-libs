from sqlalchemy import BigInteger, Integer, SmallInteger
from sqlalchemy.orm import Session
from sqlalchemy.dialects import postgresql

_INT_PK_TYPES = (Integer, SmallInteger, BigInteger)


def server_generated_pk(entity):
    """回傳「單欄整數自增 PK 且值為 None」的欄名，否則 None（t_f50d2879）。

    語意：該主鍵值交由資料庫端生成（PG serial/bigserial／SQLite rowid），
    呼叫端必須排除該欄改走純 INSERT——把 None 直放 VALUES 會撞 NOT NULL，
    放进 ON CONFLICT 衝突鍵則語意未定義。非此形態（複合鍵／非整數鍵／
    顯式帶值）一律回 None，既有 upsert 路徑零變動。
    """
    table = getattr(entity, '__table__', None)
    if table is None or table.primary_key is None:
        return None
    pk_cols = list(table.primary_key.columns)
    if len(pk_cols) != 1:
        return None
    col = pk_cols[0]
    if col.autoincrement not in (True, 'auto'):
        return None
    if not isinstance(col.type, _INT_PK_TYPES):
        return None
    if getattr(entity, col.name, None) is not None:
        return None
    return col.name


def upsert_entity(sa_session:Session, entity):
    ''' Provides upsert functionality for the given entity, And for postgresql it uses on_conflict_do_update for better performance.'''
    if sa_session.bind.dialect.name == 'postgresql':
        table = entity.__table__
        auto_pk = server_generated_pk(entity)
        if auto_pk is not None:
            # [t_f50d2879] 自增 PK 值 None＝新增列：排除該欄改純 INSERT，
            # 每列必新增（日誌型表無 upsert 覆寫語意）。
            stmt = postgresql.insert(table).values(
                {c.name: getattr(entity, c.name) for c in table.columns
                 if c.name != auto_pk}
            )
            sa_session.execute(stmt)
            return
        stmt = postgresql.insert(table).values(
            {c.name: getattr(entity, c.name) for c in table.columns}
        ).on_conflict_do_update(
            index_elements=table.primary_key.columns.keys(),
            set_={c.name: getattr(entity, c.name) for c in table.columns if c.name not in table.primary_key.columns}
        )
        sa_session.execute(stmt)
    else:
        sa_session.merge(entity)