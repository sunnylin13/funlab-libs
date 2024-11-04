from abc import ABC, abstractmethod
import atexit
import logging, os
from dataclasses import is_dataclass
import platform
import signal
import sys
# from cryptography.fernet import Fernet
from flask import Flask, g, request
from flask_login import AnonymousUserMixin, LoginManager, current_user
from funlab.core.plugin import SecurityPlugin, ViewPlugin, load_plugins
from funlab.utils import log
from funlab.core import _Configuable, jinja_filters
from funlab.core.config import Config
from funlab.core.dbmgr import DbMgr
from funlab.core.menu import AbstractMenu, Menu, MenuBar
from sqlalchemy.orm import registry
from funlab.utils import vars2env
from flask_caching import Cache

# 在table, entity間有相關性時, 例user, manager, account, 必需使用同一個registry去宣告entity
# 否則sqlalchemy會因registry資訊不足而有錯誤,
# 例: Foreign key associated with column 'account.manager_id' could not find table 'user' with which to generate a foreign key to target column 'id'
# 並此 registry 可用初如化時create db table
APP_ENTITIES_REGISTRY = registry()

app_cache:Cache = Cache()

class _FlaskBase(_Configuable, Flask, ABC):
    """Base class for Flask application in Funlab.

    Args:
        configfile (str): Path to the configuration file.
        envfile (str): Path to the environment file.
        *args: Variable length argument list for originail Flask.
        **kwargs: Arbitrary keyword arguments for original Flask.

    Attributes:
        _config (Config): Configuration object.
        mylogger (Logger): Logger object.
        dbmgr (DbMgr): Database manager object.
        _mainmenu (MenuBar): Main menu container.
        _usermenu (Menu): User menu container.
        _adminmenu (Menu): Admin menu container.
        plugins (dict): Dictionary of registered plugins.
        login_manager (LoginManager): flask-login's Login manager object.

    """
    def __init__(self, configfile:str, envfile:str, *args, **kwargs):
        Flask.__init__(self, *args, **kwargs)
        self.app.json.sort_keys = False  # prevent jsonify sort the key when transfer to html page
        self._init_configuration(configfile, envfile)

        self._init_menu_container()
        self.register_routes()
        self.plugins:dict[str, ViewPlugin] = {}
        self.search_and_register_plugins()
        self.register_routes_menu()
        self.register_request_handler()
        self.register_jinja_filters()

        # append adminmenu to last, and useless
        if self._adminmenu.has_menuitem():
            self._mainmenu.append(self._adminmenu)
        del self._adminmenu

        # 這裡應在plugin中提供entity class name to create table, 不需要在這裡create table
        if self.dbmgr:
            self.dbmgr.create_registry_tables(APP_ENTITIES_REGISTRY)
        self.mylogger.info('Funlab Flask created.')

        def setup_exit_signal_handler(signal_handler:callable):
            """根據操作系統設置適當的信號處理器"""
            # SIGTERM 和 SIGINT 在所有平台都支持
            signal.signal(signal.SIGTERM, signal_handler)  #  通常由系統管理員或系統服務管理器（如 systemd）發送, In Linux call  kill <pid>
            signal.signal(signal.SIGINT, signal_handler)  #  通過鍵盤中斷（Ctrl+C）發送的信號

            # SIGHUP 只在 Unix-like 系統中設置, 歷史上用於表示終端掛起（Hang Up）
            if platform.system() != "Windows":
                try:
                    signal.signal(signal.SIGHUP, signal_handler)
                    self.mylogger.debug("SIGHUP handler registered")
                except AttributeError:
                    self.mylogger.debug("SIGHUP not available on this system")

        setup_exit_signal_handler(self._cleanup_on_exit)

        @self.teardown_appcontext
        def shutdown_session(exception=None):
            # self.mylogger.info('Funlab Flask application context exiting ...')
            self.dbmgr.remove_thread_sessions()

        # @self.app.context_processor
        # def make_config_available():
        #     return dict(config=self.config)
    def _cleanup_on_exit(self, signal_received, frame):
        """
        cleanup on exit for flask app and plugins
        """
        self.mylogger.info('Funlab Flask cleanup_on_exit ...')
        self.dbmgr.release()
        for plugin in reversed(self.plugins.values()):
            plugin.unload()

        self.mylogger.info('Funlab Flask cleanup completed.')
        sys.exit(0)

    @abstractmethod
    def register_routes(self):
        """
        Abstract method, must be implemented to register the root routes for the application.
        """
        ...

    @abstractmethod
    def register_routes_menu(self):
        """
        Abstract method, must be implemented to register the memu for register routes of the application.
        """
        ...

    def _init_configuration(self, configfile: str, envfile: str):
        """
        Initializes the configuration for the application.

        Args:
            configfile (str): The path to the configuration file.
            envfile (str): The path to the environment file.

        Returns:
            None
        """
        if configfile:
            self._config: Config = Config(configfile, env_file_or_values=envfile)
        else:
            self._config: Config = Config({})

        app_config = self.get_config('config.toml', section=self.__class__.__name__,
                                     ext_config=self._config.get_section_config(section=self.__class__.__name__))
        if (logging_level := app_config.get('LOGGING_LEVEL')):
            self.mylogger = log.get_logger(self.__class__.__name__,
                                           level=logging.getLevelNamesMapping()[logging_level])
        else:
            self.mylogger = log.get_logger(self.__class__.__name__, level=logging.INFO)
        self.mylogger.info('Funlab Flask create started ...')
        # flask's config, different from self._config
        self.config.from_mapping(app_config.as_dict())
        if not self.config['SECRET_KEY']:
            secret_key = os.urandom(24).hex()
            self.config.update({'SECRET_KEY': secret_key} )  # Fernet.generate_key().decode(), })

        self.dbmgr: DbMgr = None
        if db_config := self.app_config.get('DATABASE', None):
            self.dbmgr = DbMgr(db_config)
            dburl = self.dbmgr.get_db_url()
            if (i:=dburl.find('@'))>0:
                dburl = dburl[:i-9] + '*' + dburl[i:]  # hide password
            self.mylogger.info(f'Database:{dburl}')

        # self.cache = Cache(app=self, config=self._config.CACHE)
        app_cache.init_app(app=self, config=self._config.CACHE)
        self.cache:Cache = app_cache  # Cache(self, config=self._config.get('CACHE', {'CACHE_TYPE': 'SimpleCache'}))  # add Flask-Caching support

        if 'ENV' in self._config:
            del self._config.ENV

        if 'DATABASE' in self._config:
            del self._config.DATABASE

    def _init_menu_container(self):
        self._mainmenu: MenuBar = MenuBar(
            title=f"{self.config.get('APP_NAME', '')}",
            icon=f"{self.config.get('APP_LOGO', '')}",
            href='/'
        )
        self._usermenu: Menu = Menu(title="", dummy=True, collapsible=True, expand=False)
        self._adminmenu = Menu(
            title="Admin",
            icon='<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-tool" width="24" height="24" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"></path><path d="M7 10h3v-3l-3.5 -3.5a6 6 0 0 1 8 8l6 6a2 2 0 0 1 -3 3l-6 -6a6 6 0 0 1 -8 -8l3.5 3.5"></path></svg>',
            admin_only=True,
            collapsible=False
        )

    def register_plugin(self, plugin_cls:type[ViewPlugin])->ViewPlugin:
        def init_plugin_object(plugin_cls):
            plugin: ViewPlugin = plugin_cls(self)
            self.plugins[plugin.name] = plugin
            if blueprint:=plugin.blueprint:
                self.register_blueprint(blueprint)
            # create sqlalchemy registry db table for each plugin
            if plugin.entities_registry:
                self.dbmgr.create_registry_tables(plugin.entities_registry)
            return plugin


        plugin = init_plugin_object(plugin_cls)
        if isinstance(plugin, SecurityPlugin):
            if self.login_manager is not None:
                msg = f"There is SecurityPlugin has been installed for login_manager. {plugin_cls} skipped."
                self.mylogger.warning(msg)
            else:
                self.login_manager = plugin.login_manager
                self.login_manager.init_app(self)
                # set login_view for each plugin
                if plugin.login_view:
                    self.login_manager.blueprint_login_views[plugin.bp_name] = plugin.login_view

    def register_request_handler(self):
        @self.teardown_request
        def shutdown_session(exception=None):
            self.dbmgr.remove_thread_sessions()

        @self.before_request
        def set_global_variables():
            g.mainmenu = self._mainmenu.html(layout=request.args.get('layout', 'vertical'), user=current_user)
            g.usermenu = self._usermenu.html(layout='vertical', user=current_user)

    def register_jinja_filters(self):
        for module in jinja_filters.__all__:
            filter_func = getattr(jinja_filters, module)
            if callable(filter_func):
                self.add_template_filter(filter_func)

    def search_and_register_plugins(self):
        self.login_manager = None
        self.mylogger.info('Funlab Flask searching plugins ...')
        plugin_classes:dict = load_plugins(group='funlab_plugin')
        ordered_plugins:list = []
        # add priority plugins first, this for preventing dependency issue
        priority_plugins = self.config.get('PRIORITY_PLUGINS', [])
        for plugin_classname in priority_plugins:
            if plugin_cls:=plugin_classes.pop(plugin_classname, None):
                ordered_plugins.append(plugin_cls)
        # add rest of plugins
        for plugin_cls in plugin_classes.values() :
            ordered_plugins.append(plugin_cls)

        for plugin_cls in ordered_plugins :
            self.mylogger.info(f"Loading plugin: {plugin_cls.__name__} ...", end='')
            self.register_plugin(plugin_cls=plugin_cls)
            self.mylogger.info(f"Plugins {plugin_cls.__name__} loaded.")

        if self.login_manager is None:
            self.login_manager = LoginManager()
            self.login_manager.init_app(self)
            self.login_manager.login_view = 'root_bp.blank'
            @self.login_manager.user_loader
            def user_loader(user_id):
                anonymous =  AnonymousUserMixin()
                setattr(anonymous, 'name', 'anonymous')
                return anonymous

    @property
    def app(self)->Flask:
        """ Your own Flask implementation"""
        return self

    @property
    def app_config(self):
        """ This is Original Flask config, means to differencial my Config class obj """
        return self.config

    def append_mainmenu(self, menus:list[AbstractMenu]|AbstractMenu)->Menu:
        """ For App plugin to append main menu"""
        self._mainmenu.append(menus)

    def insert_mainmenu(self, idx:int, menus:list[AbstractMenu]|AbstractMenu)->Menu:
        """ For App plugin to insert main menu at position of idx"""
        self._mainmenu.insert(idx, menus)

    def append_adminmenu(self, menus:list[AbstractMenu]|AbstractMenu)->Menu:
        """ For App plugin to append admin menu"""
        self._adminmenu.append(menus)

    def insert_adminmenu(self, idx: int, menus:list[AbstractMenu]|AbstractMenu)->Menu:
        """ For App plugin to insert admin menu at position of idx"""
        self._adminmenu.insert(idx, menus)

    def append_usermenu(self, menus:list[AbstractMenu]|AbstractMenu)->Menu:
        """ For App plugin to append user menu"""
        self._usermenu.append(menus)

    def insert_usermenu(self, idx, menus:list[AbstractMenu]|AbstractMenu)->Menu:
        """ For App plugin to insert user menu at position of idx"""
        self._usermenu.insert(idx, menus)

    def get_section_config(self, section:str, default=None, case_insensitive=False, keep_section=False):
        """
        Retrieves the configuration for a specific section.

        Args:
            section (str): The name of the section to retrieve the configuration for.
            default (Any, optional): The default value to return if the section is not found. Defaults to None.
            case_insensitive (bool, optional): Whether to perform a case-insensitive search for the section name. Defaults to False.
            keep_section (bool, optional): Whether to keep the section name in the returned configuration. Defaults to False.

        Returns:
            Any: The configuration for the specified section, or the default value if the section is not found.
        """
        return self._config.get_section_config(section=section, default=default
                                               , case_insensitive=case_insensitive, keep_section=keep_section)

    def get_env_var_value(self, var_name:str):
        return vars2env.get_env_var_value(var_name, self.config['SECRET_KEY'])