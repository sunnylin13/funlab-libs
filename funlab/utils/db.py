from sqlalchemy.orm import Session
from sqlalchemy.dialects import postgresql

def upsert_entity(sa_session:Session, entity):
    ''' Provides upsert functionality for the given entity, And for postgresql it uses on_conflict_do_update for better performance.'''
    if sa_session.bind.dialect.name == 'postgresql':
        table = entity.__table__
        stmt = postgresql.insert(table).values(
            {c.name: getattr(entity, c.name) for c in table.columns}
        ).on_conflict_do_update(
            index_elements=table.primary_key.columns.keys(),
            set_={c.name: getattr(entity, c.name) for c in table.columns if c.name not in table.primary_key.columns}
        )
        sa_session.execute(stmt)
    else:
        sa_session.merge(entity)