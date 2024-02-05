
from __future__ import annotations

import contextlib
import importlib
import threading
import tomllib

import sqlalchemy as sa
from sqlalchemy.future import Engine
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from funlab.utils import lang, log
from funlab.core.config2 import Config

mylogger = log.get_logger(__name__)

class NoDatabaseSessionExcption(Exception):
    pass

class NoDBUrlDefined(Exception):
    pass

class DbMgr:
    def __init__(self, config:Config, default_db='DEFAULT') -> None:
        # if not (db_config:=config.get('DATABASE')):
        #     raise NoDatabaseSessionExcption('No [DATABASE] session defined.')
        if not (db_config:=config.get_section_config(section='database', case_insensitive=True)):
            db_config = config
        db_use = config.get('DB_USE')
        self.config:DbConfig=DbConfig({'DATABASE':db_config})
        self.default_db=db_use if db_use else default_db
        self._db_engines = {}
        self._thread_safe_session_factories = {} #  {self.default_db: scoped_session(sessionmaker(bind=self.get_db_engine()))}
        self.__lock = threading.Lock()

    def __del__(self):
        try:
            self.remove_all_sessions(current_thread_only=False)  # remove all
            for db_engine in self._db_engines.values():
                db_engine.dispose()
            self._db_engines.clear()
        except Exception as err:
            mylogger.error(f'DbMgr __del__ exception:{err}')

    def get_db_url(self, db_use=None)->str:
        try:
            if not db_use:
                db_use = self.default_db
            url:str = self.config.get_dbarg(db_use, arg_name='url')
            return url
        except Exception as e:
            raise NoDBUrlDefined(f"No 'database' or related 'dbuse'={db_use} is defined in config.tomllib. Check!")

    def _create_sa_engine(self, db_use=None, create_table_entityclasses:list=None)-> Engine:
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

        db_url = self.get_db_url(db_use)
        connect_args = None
        if connect_args:=self.config.get_dbarg(db_use, 'connect_args', {}):
            connect_args:dict = eval_kwargs(connect_args)
        if kwargs:=self.config.get_dbarg(db_use, 'kwargs', {}):
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

    def get_db_engine(self, db_use=None)->Engine:
        if not db_use:
            db_use = self.default_db
        # db engine/connection shared to all thread
        db_key = db_use # engine, connection shared between thread, so db_key==db_use, # + str(threading.get_ident())
        if db_key not in self._db_engines:
            self._db_engines[db_key] = self._create_sa_engine(db_use)
        return self._db_engines[db_key]

    def _get_db_session_factory(self, db_use=None):
        if not db_use:
            db_use = self.default_db
        db_key = db_use + str(threading.get_ident())
        with self.__lock:
            if db_key not in self._thread_safe_session_factories:
                self._thread_safe_session_factories[db_key] = scoped_session(sessionmaker(autocommit=False, autoflush=False,
                                                                        bind=self.get_db_engine(db_use)))
        return self._thread_safe_session_factories[db_key]

    def get_db_session(self, db_use=None)->Session:
        if not db_use:
            db_use = self.default_db
        session_factory = self._get_db_session_factory(db_use)
        return session_factory()

    def remove_session(self, db_use):
        with self.__lock:
            db_key = db_use + str(threading.get_ident())
            if (session_factory:= self._get_db_session_factory(db_use)):
                    session_factory.remove()
                    del self._thread_safe_session_factories[db_key]

    def clear(self):
        """Remove all sessions in current thread."""
        self.remove_all_sessions()

    def remove_all_sessions(self, current_thread_only: bool = True) -> None:
        # Remove all sessions in current thread
        if current_thread_only:
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
                            session_factory.remove()
                            # Add the key of the session to the list
                            need_removed.append(db_key)
                    # Loop all the keys of sessions to be removed
                    for db_key in need_removed:
                        # Remove the session from the dictionary
                        del self._thread_safe_session_factories[db_key]
                except RuntimeError as e:
                    mylogger.error(f'DbMgr remove_all_sessions RuntimeError:{e}')
                    raise e
        # Remove all sessions in all threads
        else:
            with self.__lock:
                # Loop all thread-safe sessions
                for db_key, session_factory in self._thread_safe_session_factories.copy().items():
                    # Remove the session
                    session_factory.remove()
                # Clear the dictionary of thread-safe sessions
                self._thread_safe_session_factories.clear()
    @contextlib.contextmanager
    def session_context(self, db_use=None):
        """在session context 下自動commit, flush, rollback

        Args:
            db_use (_type_, optional): Defaults to None to use default_db
            remove_session (bool, optional): 如果是query entities且還繼續使用, e.g. return entity, 不可remove, 會造成entity detached,
            確定是單一scope使用, 即可設true, 需自行call . Defaults to False.
        """
        if db_use is None:
            db_use = self.default_db
        session = self.get_db_session(db_use)
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
            self.remove_session(db_use=db_use)
            # self.__lock.release()

    def create_registry_tables(self, sa_registry, db_use=None):
        if not db_use:
            db_use = self.default_db
        sa_registry.metadata.create_all(self.get_db_engine(db_use))

    def create_entity_table(self, entities_class:str, db_use=None):
        if not db_use:
            db_use = self.default_db
        *module, classname = entities_class.split('.')
        module = '.'.join(module)
        try:
            entity_class = lang.get_class(classname, module)
            entity_class.__table__.create(bind=self.get_db_engine(db_use), checkfirst=True)
        except:
            raise Exception(f'Not found entity class {classname} from module {module} for parameter:{entities_class}')

class DbConfig(Config):
    def __init__(self, config_file_or_values: str | dict = None) -> None:
        super().__init__(config_file_or_values, only_section='DATABASE', case_insensitive=False)

    def get_dbarg(self, db_use, arg_name, default=''):
        """ make config get attr is case insensitive"""
        # return self.get(db_use, {}).get(arg_name.upper(), default)
        if self._case_insensitive:
            db_use = db_use.upper()
            arg_name=arg_name.upper()
        return self.get(db_use, {}).get(arg_name, default)

