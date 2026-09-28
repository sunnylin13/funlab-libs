
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


def mask_db_url(url: str) -> str:
    """回傳把密碼取代為 '***' 的 URL，供安全 log 使用。

    優先使用 SQLAlchemy 的 render_as_string(hide_password=True)；
    無法解析的字串退回手動遮罩 userinfo 段（``//user:pass@`` → ``//user:***@``），
    再不行原樣回傳（此時字串本來就不含 URL 結構）。
    """
    try:
        from sqlalchemy.engine import make_url
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        at = url.find('@')
        if at > 0:
            head_cut = url.find('//')
            head = url[:head_cut + 2] if head_cut >= 0 else ''
            userinfo = url[len(head):at]
            if ':' in userinfo:
                user = userinfo.split(':', 1)[0]
                return f'{head}{user}:***{url[at:]}'
        return url


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
        # Per-thread session_context nesting depth (threading.local 執行緒隔離)。
        self._session_state = threading.local()
        # RLock prevents deadlock when session factory creation calls get_db_engine().
        self.__lock = threading.RLock()

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
    def session_context(self, nested: bool = False) -> Generator[Session, None, None]:
        """Re-entrancy-safe database session context manager.

        巢狀語意（同一執行緒）：
        - 最外層（depth 0→1）：負責 commit / rollback / remove_session。
        - 內層（depth >= 2，``nested=False``）：只轉發同一個 session，完全不提交、
          不移除；內層例外原樣上拋，由最外層決定回滾整個交易。
        - 內層 ``nested=True``：使用 SAVEPOINT（``Session.begin_nested()``），
          內層回滾只撤銷存點以後的寫入，不影響外層。

        Raises:
            Exception: 任何在 context 內抛出的例外都會原樣上拋。
        """
        state = self._session_state
        depth = getattr(state, 'depth', 0)
        outermost = depth == 0
        session = self.get_db_session()

        if not outermost and nested:
            savepoint = session.begin_nested()
            try:
                yield session
                savepoint.commit()
            except Exception:
                savepoint.rollback()
                raise
            return

        state.depth = depth + 1
        try:
            yield session
            if outermost:
                session.commit()
        except Exception:
            if outermost:
                session.rollback()
            # When an exception occurs, the outermost context owns rollback;
            # re-raise so the caller can handle it.
            raise
        finally:
            state.depth = getattr(state, 'depth', 1) - 1
            if state.depth == 0:
                # source: https://stackoverflow.com/questions/21078696/why-is-my-scoped-session-raising-an-attributeerror-session-object-has-no-attr
                self.remove_session()

    def flush_on_shutdown(self) -> None:
        """Flush pending writes to disk for supported databases.

        Executes database-specific checkpoint/flush commands to ensure
        all dirty pages or WAL buffers are safely persisted before shutdown.
        """
        try:
            with self.session_context() as session:
                db_type = self.get_db_engine().dialect.name
                if db_type == 'postgresql':
                    mylogger.info('Executing CHECKPOINT...')
                    session.execute(sa.text("CHECKPOINT;"))
                    mylogger.info('Executing pg_switch_wal()...')
                    session.execute(sa.text("SELECT pg_switch_wal();"))
                elif db_type == 'mysql':
                    mylogger.info('Executing FLUSH TABLES...')
                    session.execute(sa.text("FLUSH TABLES;"))
                    mylogger.info('Executing FLUSH LOGS...')
                    session.execute(sa.text("FLUSH LOGS;"))
                elif db_type == 'sqlite':
                    mylogger.info('Executing PRAGMA wal_checkpoint...')
                    session.execute(sa.text("PRAGMA wal_checkpoint(FULL);"))
                else:
                    mylogger.warning(f'No flush handling defined for database type: {db_type}')
        except Exception as e:
            mylogger.error(f'Error during flush_on_shutdown: {e}', exc_info=True)

    def create_registry_tables(self, sa_registry):
        # Prevent concurrent create_all calls and avoid repeated creation for
        # the same registry which can lead to "deque mutated during iteration"
        # when SQLAlchemy dispatch listeners are modified during startup.
        with self.__lock:
            # initialize created registries tracking set lazily
            if not hasattr(self, '_created_registries'):
                self._created_registries = set()
            rid = id(sa_registry)
            if rid in self._created_registries:
                return
            sa_registry.metadata.create_all(self.get_db_engine())
            self._created_registries.add(rid)

    def create_entity_table(self, entities_class:str):
        *module, classname = entities_class.split('.')
        module = '.'.join(module)
        try:
            entity_class = lang.get_class(classname, module)
            with self.__lock:
                entity_class.__table__.create(bind=self.get_db_engine(), checkfirst=True)
        except Exception as exc:
            raise Exception(
                f'Not found entity class {classname} from module {module} for parameter:{entities_class}'
            ) from exc



