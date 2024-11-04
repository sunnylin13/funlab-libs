
from __future__ import annotations

import contextlib
import importlib
import logging
import threading
import tomllib
from typing import Generator

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
    """Database Manager class for managing database connections and sessions in multi-thread environment of web application.

    Args:
        conf (Config | dict): Configuration object or dictionary containing database connection details.

    Raises:
        Exception: If no 'url' data is provided in the Config object.
        NoDBUrlDefined: If no 'database' or related url is defined in config.toml.

    Attributes:
        config (Config): Configuration object containing database connection details.
        _db_engines (Engine): SQLAlchemy Engine object for database connection.
        _thread_safe_session_factories (dict): Dictionary to store thread-safe session factories.
        __lock (threading.Lock): Lock object for thread safety.
    """

    def __init__(self, conf:Config|dict) -> None:
        if isinstance(conf, dict):
            self.config = Config(conf)
        else:
            self.config = conf
        if not self.config.get('url', case_insensitive=True):
            raise Exception("No database 'url' data in provided Config object.")
        self._db_engines: Engine = None
        self._thread_safe_session_factories = {}
        self.__lock = threading.Lock()

    def get_db_url(self) -> str:
        """
        Retrieves the database URL from the configuration.

        Returns:
            str: The database URL.

        Raises:
            NoDBUrlDefined: If no 'database' or related URL is defined in config.toml.
        """
        try:
            url: str = self.config.get('url', case_insensitive=True)
            return url
        except Exception as e:
            raise NoDBUrlDefined("No 'database' or related URL is defined in config.toml. Check!") from e

    def _create_sa_engine(self, create_table_entityclasses:list=None)-> Engine:
        def eval_kwargs(kwargs:dict):
            new_kwargs = {}
            for key, value in kwargs.items():
                if tomllib.__name__=='tomlkit' and (type(value)==tomllib.items.String):
                    value = str(value)
                if type(value)==str and value.startswith('@'):
                    try:
                        value = eval(value[1:])  # have security issue
                    except NameError as ne:
                        importlib.import_module(ne.name)  #
                        value = eval(value[1:])
                new_kwargs[key] = value
            return new_kwargs

        db_url = self.config.get('url', case_insensitive=True)
        connect_args = None
        if connect_args:=self.config.get('connect_args', {}):
            connect_args:dict = eval_kwargs(connect_args)
        if kwargs:=self.config.get('kwargs', {}):
            kwargs:dict = eval_kwargs(kwargs)
        if connect_args:
            kwargs['connect_args'] = connect_args
            sa_engine =  sa.create_engine(db_url, future=True, **kwargs)
        else:
            sa_engine =  sa.create_engine(db_url, future=True,)
        if create_table_entityclasses:
            for entityclass in create_table_entityclasses:
                entityclass.__table__.create(bind=sa_engine, checkfirst=True)
        return sa_engine

    def get_db_engine(self)->Engine:
        # db engine/connection shared to all thread
        if not self._db_engines:
            self._db_engines = self._create_sa_engine()
        return self._db_engines

    def _get_db_session_factory(self):
        db_key = str(threading.get_ident())
        with self.__lock:
            if db_key not in self._thread_safe_session_factories:
                self._thread_safe_session_factories[db_key] = scoped_session(sessionmaker(autocommit=False, autoflush=False,
                                                                        bind=self.get_db_engine()))
        return self._thread_safe_session_factories[db_key]

    def get_db_session(self)->Session:
        session_factory = self._get_db_session_factory()
        return session_factory()

    def remove_session(self):
        with self.__lock:
            db_key = str(threading.get_ident())
            if (session_factory:= self._get_db_session_factory()):
                    session_factory.remove()
                    del self._thread_safe_session_factories[db_key]

    def release(self):
        """Release the database connection and remove all sessions."""
        try:
            self.remove_all_sessions()  # remove all
            if self._db_engines:
                self._db_engines.dispose()
        except Exception as err:
            mylogger.error(f'DbMgr __del__ exception:{err}')

    def remove_thread_sessions(self)->None:
        """Remove current thread's created db sessions."""
        # Get current thread id
        thread_id = str(threading.get_ident())
        # Define a list to save the keys of sessions to be removed
        # Use the lock to prevent multi-thread issue in web app of RuntimeError: dictionary changed size during iteration
        with self.__lock:
            need_removed = []
            try:
                # Loop all thread-safe sessions
                for db_key, session_factory in self._thread_safe_session_factories.items():
                    # Check if the session is in current thread
                    if db_key.endswith(thread_id):
                        # Remove the session
                        mylogger.debug(f'[Curr Thread]DbMgr remove session:{db_key}')
                        session_factory.remove()
                        # Add the key of the session to the list
                        need_removed.append(db_key)
                # Loop all the keys of sessions to be removed
                for db_key in need_removed:
                    # Remove the session from the dictionary
                    del self._thread_safe_session_factories[db_key]
            except RuntimeError as e:
                mylogger.error(f'DbMgr remove_thread_sessions RuntimeError:{e}')
                raise e

    def remove_all_sessions(self) -> None:
        """Remove all sessions in current thread or all threads.

        Args:
            current_thread_only (bool, optional): If True, remove sessions only in the current thread. If False, remove sessions in all threads. Defaults to True.
        """
        # Remove all sessions in all threads
        with self.__lock:
            # Loop all thread-safe sessions
            for db_key, session_factory in self._thread_safe_session_factories.copy().items():
                # Remove the session
                mylogger.debug(f'[All thread]DbMgr remove session:{db_key}')
                session_factory.remove()
            # Clear the dictionary of thread-safe sessions
            self._thread_safe_session_factories.clear()

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
            # self.__lock.acquire()
            yield session
            # self.__lock.release()
            # self.__lock.acquire()
            session.commit()
            session.flush()
        except Exception:
            session.rollback()
            # When an exception occurs, handle session session cleaning,
            # but raise the Exception afterwards so that user can handle it.
            raise
        finally:
            # source: https://stackoverflow.com/questions/21078696/why-is-my-scoped-session-raising-an-attributeerror-session-object-has-no-attr
            self.remove_session()
            # self.__lock.release()

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



