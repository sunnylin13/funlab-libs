
from __future__ import annotations

import contextlib
import importlib
import logging
import threading
import tomllib
from typing import Any, Dict, Generator, Optional

import sqlalchemy as sa
from sqlalchemy.future import Engine
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from funlab.utils import lang, log
from funlab.core.config import Config

mylogger = log.get_logger(__name__, level=logging.INFO)

class NoDatabaseSessionExcption(Exception):
    pass

class NoDBUrlDefined(Exception):
    pass
class DbMgr:
    """Thread-safe database manager for SQLAlchemy Engine/Session handling."""

    def __init__(
        self,
        conf: Config | dict,
        *,
        engine: Optional[Engine] = None,
        engine_options: Optional[Dict[str, Any]] = None,
        session_options: Optional[Dict[str, Any]] = None,
    ) -> None:
        if isinstance(conf, dict):
            self.config = Config(conf)
        else:
            self.config = conf

        if not self.config.get('url', case_insensitive=True):
            raise Exception("No database 'url' data in provided Config object.")

        self._engine: Optional[Engine] = engine
        self._engine_options = engine_options or {}
        self._session_options = session_options or {}
        self._scoped_session: Optional[scoped_session] = None
        self.__lock = threading.Lock()

    def get_db_url(self) -> str:
        """Fetch the configured database URL or raise if missing."""
        try:
            url: str = self.config.get('url', case_insensitive=True)
            return url
        except Exception as e:
            raise NoDBUrlDefined("No 'database' or related URL is defined in config.toml. Check!") from e

    def _create_sa_engine(self, create_table_entityclasses: Optional[list] = None) -> Engine:
        def _resolve_reference(reference: str) -> Any:
            parts = reference.split(".")
            if len(parts) < 2:
                raise ValueError(f"Invalid reference '{reference}'. Expected 'module.attr'.")
            module_path = ".".join(parts[:-1])
            attr_name = parts[-1]
            module = importlib.import_module(module_path)
            try:
                return getattr(module, attr_name)
            except AttributeError as exc:
                raise ValueError(f"Reference '{reference}' not found.") from exc

        def eval_kwargs(kwargs: dict) -> dict:
            new_kwargs = {}
            for key, value in kwargs.items():
                if not isinstance(value, str) and value.__class__.__module__.startswith("toml"):
                    value = str(value)
                if isinstance(value, str) and value.startswith("@"):
                    value = _resolve_reference(value[1:])
                new_kwargs[key] = value
            return new_kwargs

        db_url = self.config.get('url', case_insensitive=True)
        connect_args = None

        if connect_args := self.config.get('connect_args', {}):
            connect_args = eval_kwargs(connect_args)

        kwargs: Dict[str, Any] = eval_kwargs(self.config.get('kwargs', {})) if self.config.get('kwargs', {}) else {}
        kwargs.update(self._engine_options)

        if connect_args:
            kwargs['connect_args'] = connect_args

        sa_engine = sa.create_engine(db_url, future=True, **kwargs)
        if create_table_entityclasses:
            for entityclass in create_table_entityclasses:
                entityclass.__table__.create(bind=sa_engine, checkfirst=True)
        return sa_engine

    def get_db_engine(self) -> Engine:
        # db engine/connection shared to all thread
        if not self._engine:
            with self.__lock:
                if not self._engine:
                    self._engine = self._create_sa_engine()
        return self._engine

    def _get_db_session_factory(self) -> scoped_session:
        if not self._scoped_session:
            with self.__lock:
                if not self._scoped_session:
                    maker = sessionmaker(
                        bind=self.get_db_engine(),
                        autoflush=False,
                        expire_on_commit=False,
                        future=True,
                        **self._session_options,
                    )
                    self._scoped_session = scoped_session(maker)
        return self._scoped_session

    def get_db_session(self) -> Session:
        return self._get_db_session_factory()()

    def release(self):
        """Release the database connection and remove current thread session."""
        try:
            self.remove_session()
            with self.__lock:
                self._scoped_session = None
            if self._engine:
                self._engine.dispose()
        except Exception as err:
            mylogger.error(f'DbMgr __del__ exception:{err}')

    def remove_session(self) -> None:
        """Remove the current thread's session.

        Note: This only affects the calling thread when using scoped_session.
        """
        if self._scoped_session:
            try:
                self._scoped_session.remove()
            except RuntimeError as e:
                mylogger.error(f'DbMgr remove_session RuntimeError:{e}')
                raise e

    @contextlib.contextmanager
    def session_context(self)-> Generator[Session, None, None]:
        """
        Context manager for handling database sessions.

        Yields:
            Session: Database session object.

        Raises:
            Exception: Any exception raised during the session context.
        """
        session = self.get_db_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            # When an exception occurs, handle session cleaning,
            # but raise the Exception afterwards so that caller can handle it.
            raise
        finally:
            # source: https://stackoverflow.com/questions/21078696/why-is-my-scoped-session-raising-an-attributeerror-session-object-has-no-attr
            self.remove_session()

    def create_registry_tables(self, sa_registry):
        sa_registry.metadata.create_all(self.get_db_engine())

    def create_entity_table(self, entities_class:str):
        *module, classname = entities_class.split('.')
        module = '.'.join(module)
        try:
            entity_class = lang.get_class(classname, module)
            entity_class.__table__.create(bind=self.get_db_engine(), checkfirst=True)
        except:
            raise Exception(f'Not found entity class {classname} from module {module} for parameter:{entities_class}')



