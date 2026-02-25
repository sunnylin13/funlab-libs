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
from funlab.core.plugin_manager import ModernPluginManager
from funlab.core.enhanced_plugin import EnhancedViewPlugin, EnhancedSecurityPlugin
from funlab.utils import log
from funlab.core import _Configuable, jinja_filters
from funlab.core.config import Config
from funlab.core.dbmgr import DbMgr
from funlab.core.menu import AbstractMenu, Menu, MenuBar
from funlab.core.hook import HookManager
from sqlalchemy.orm import registry
from funlab.utils import vars2env
from flask_caching import Cache
from sqlalchemy import text, inspect

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
        self.plugins:dict[str, EnhancedViewPlugin] = {}
        self.app.json.sort_keys = False  # prevent jsonify sort the key when transfer to html page
        self._cleanup_in_progress = False  # 重入保護標記
        self._init_configuration(configfile, envfile)
        self._init_menu_container()

        # 初始化現代化Plugin管理器
        self.plugin_manager = ModernPluginManager(self)

        # 初始化 Hook 管理器
        self.hook_manager = HookManager(self)

        self.register_routes()
        self.register_plugins()
        self.register_menu()
        self.register_request_handler()
        self.register_jinja_filters()

        # append adminmenu to last, and attribute is useless
        # if self._adminmenu.has_menuitem():
        #     self._mainmenu.append(self._adminmenu)
        # del self._adminmenu

        # 這裡應在plugin中提供entity class name to create table, 不需要在這裡create table
        if self.dbmgr:
            self.dbmgr.create_registry_tables(APP_ENTITIES_REGISTRY)
        self.mylogger.info('Funlab Flask created.')

        self._setup_exit_signal_handler(self._cleanup_on_exit)

    def _cleanup_on_exit(self, signal_received, frame):
        """
        cleanup on exit for flask app and plugins
        """
        # 防止重入
        if self._cleanup_in_progress:
            self.mylogger.warning('Cleanup already in progress, ignoring repeated call')
            return

        self._cleanup_in_progress = True

        try:
            self.mylogger.info('Funlab Flask cleanup_on_exit ...')
            # 使用新的Plugin管理器進行清理
            if hasattr(self, 'plugin_manager'):
                self.plugin_manager.cleanup()
            else:
                # 向後兼容的清理方式
                for plugin in reversed(self.plugins.values()):
                    try:
                        plugin.unload()
                    except Exception as e:
                        self.mylogger.error(f'Error unloading plugin {plugin}: {e}')

            # 數據庫清理
            if self.dbmgr:
                with self.dbmgr.session_context() as session:
                    inspector = inspect(session.bind)
                    db_type = inspector.dialect.name  # Database type:'postgresql', 'sqlite', 'mysql', 'mssql', 'oracle'
                    if db_type == 'postgresql':  # only for postgresql, do database data flush
                        # Execute CHECKPOINT: Forces a checkpoint to ensure all dirty pages are written to disk. need superuser privilege, alter role fundlife with superuser;
                        self.mylogger.info('Executing CHECKPOINT...')
                        session.execute(text("CHECKPOINT;"))
                        # Execute pg_switch_wal():Switches to a new WAL file, ensuring the current WAL file is archived.
                        self.mylogger.info('Executing pg_switch_wal()...')
                        session.execute(text("SELECT pg_switch_wal();"))
                    elif db_type == 'mysql':  # for MySQL, do database data flush
                        # Execute FLUSH TABLES: Ensures all tables are flushed to disk.
                        self.mylogger.info('Executing FLUSH TABLES...')
                        session.execute(text("FLUSH TABLES;"))
                        # Execute FLUSH LOGS: Ensures all logs are flushed to disk.
                        self.mylogger.info('Executing FLUSH LOGS...')
                        session.execute(text("FLUSH LOGS;"))
                    elif db_type == 'sqlite':  # for SQLite, do database data flush
                        # Execute PRAGMA wal_checkpoint: Forces a checkpoint to ensure all dirty pages are written to disk.
                        self.mylogger.info('Executing PRAGMA wal_checkpoint...')
                        session.execute(text("PRAGMA wal_checkpoint(FULL);"))
                    else:
                        self.mylogger.warning(f'No cleanup handling defined for database type: {db_type}')
                self.dbmgr.release()

            self.mylogger.info('Funlab Flask cleanup completed.')
        finally:
            self._cleanup_in_progress = False

        sys.exit(0)

    def _setup_exit_signal_handler(self, signal_handler:callable):
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

    @abstractmethod
    def register_routes(self):
        """
        Abstract method, must be implemented to register the root routes for the application.
        """
        ...

    @abstractmethod
    def register_menu(self):
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

    # def register_plugin_deprecated(self, plugin_cls:type[ViewPlugin])->ViewPlugin:
    #     """向後兼容的plugin註冊方法"""
    #     def init_plugin_object(plugin_cls):
    #         plugin: ViewPlugin = plugin_cls(self)
    #         self.plugins[plugin.name] = plugin
    #         if blueprint:=plugin.blueprint:
    #             self.register_blueprint(blueprint)
    #         # create sqlalchemy registry db table for each plugin
    #         if plugin.entities_registry:
    #             self.dbmgr.create_registry_tables(plugin.entities_registry)
    #         return plugin

    #     plugin = init_plugin_object(plugin_cls)
    #     if isinstance(plugin, (SecurityPlugin, EnhancedSecurityPlugin)):
    #         if self.login_manager is not None:
    #             msg = f"There is SecurityPlugin has been installed for login_manager. {plugin_cls} skipped."
    #             self.mylogger.warning(msg)
    #         else:
    #             self.login_manager = plugin.login_manager
    #             self.login_manager.init_app(self)
    #             # set login_view for each plugin
    #             if plugin.login_view:
    #                 self.login_manager.blueprint_login_views[plugin.bp_name] = plugin.login_view

    #     return plugin

    def register_request_handler(self):
        @self.teardown_appcontext
        def shutdown_session(exception=None):
            self.mylogger.debug('Funlab Flask application context exited.')
            if self.dbmgr:
                self.dbmgr.remove_session()

        @self.before_request
        def set_global_variables():
            # Controller Hook: before_request
            if hasattr(self, 'hook_manager'):
                self.hook_manager.call_hook('controller_before_request')

            g.mainmenu = self._mainmenu.html(layout=request.args.get('layout', 'vertical'), user=current_user)
            g.usermenu = self._usermenu.html(layout='vertical', user=current_user)

        @self.after_request
        def after_request_handler(response):
            # Controller Hook: after_request
            if hasattr(self, 'hook_manager'):
                self.hook_manager.call_hook('controller_after_request', response=response)
            return response

        @self.errorhandler(Exception)
        def handle_error(error):
            # Controller Hook: error_handler
            if hasattr(self, 'hook_manager'):
                self.hook_manager.call_hook('controller_error_handler', error=error)

            # 記錄錯誤
            self.mylogger.error(f'Unhandled exception: {error}', exc_info=True)

            # 返回預設錯誤頁面或 JSON
            if request.is_json:
                from flask import jsonify
                return jsonify({'error': str(error)}), 500
            else:
                from flask import render_template
                try:
                    return render_template('error-500.html', error=error), 500
                except Exception:
                    return f'Internal Server Error: {error}', 500

        @self.context_processor
        def inject_sse_client_script():
            """Expose SSE state to templates via context variable.

            Only the boolean ``sse_enabled`` flag is returned here.
            All CSS / JavaScript now lives in static files loaded by
            ``templates/includes/notification_init.html``.
            """
            if not current_user.is_authenticated:
                return {}
            notification_provider = getattr(self, 'notification_provider', None)
            sse_enabled = notification_provider.supports_realtime if notification_provider else False
            return dict(sse_enabled=sse_enabled)

    def register_jinja_filters(self):
        if hasattr(self, 'hook_manager'):
            self.jinja_env.globals['call_hook'] = self.hook_manager.render_hook
        for module in jinja_filters.__all__:
            filter_func = getattr(jinja_filters, module)
            if callable(filter_func):
                self.add_template_filter(filter_func)

    def register_plugins(self):
        """使用現代化Plugin管理器註冊plugins"""
        self.login_manager = None
        self.mylogger.info('Funlab Flask registering plugins with modern plugin manager...')

        # 使用新的Plugin管理器（載入順序由各plugin的dependencies宣告決定）
        self.plugin_manager.register_plugins(
            group='funlab_plugin',
            force_refresh=self.config.get('FORCE_PLUGIN_REFRESH', False)
        )

        # 處理login manager設置
        if self.login_manager is None:
            self.login_manager = LoginManager()
            self.login_manager.init_app(self)
            self.login_manager.login_view = 'root_bp.blank'

            # ✅ 添加標記，表示這是默認設置，可以被SecurityPlugin覆蓋
            self.login_manager._default_user_loader = True

            @self.login_manager.user_loader
            def user_loader(user_id):
                anonymous = AnonymousUserMixin()
                setattr(anonymous, 'name', 'anonymous')
                return anonymous

        # 在這裡處理adminmenu的最終設置，但不刪除_adminmenu屬性
        # 因為lazy loading的擴充功能可能還會需要它
        self._finalize_admin_menu()

        # 預載入關鍵擴充功能以確保模板路徑可用
        self._preload_critical_plugins()

    def _preload_critical_plugins(self):
        """預載入關鍵擴充功能以確保模板和路由可用"""
        try:
            # 檢查FOOTER_PAGE配置，如果指向擴充功能模板則預載入相關擴充功能
            footer_page = self.config.get('FOOTER_PAGE')
            if footer_page and footer_page.startswith('fundmgr_'):
                self.mylogger.info('Preloading FundMgrView dependencies for footer template')
                # FundMgrView依賴QuoteService，先嘗試載入依賴
                quote_service = self.plugin_manager.get_plugin('QuoteService')
                if quote_service:
                    self.plugin_manager.get_plugin('FundMgrView')
                else:
                    self.mylogger.warning('QuoteService failed to load, skipping FundMgrView preload')

            # 檢查HOME_ENTRY配置，如果指向擴充功能路由則預載入相關擴充功能
            home_entry = self.config.get('HOME_ENTRY')
            if home_entry and home_entry.startswith('fundmgr_'):
                self.mylogger.info('Preloading FundMgrView dependencies for home entry')
                # FundMgrView依賴QuoteService，先嘗試載入依賴
                quote_service = self.plugin_manager.get_plugin('QuoteService')
                if quote_service:
                    self.plugin_manager.get_plugin('FundMgrView')
                else:
                    self.mylogger.warning('QuoteService failed to load, skipping FundMgrView preload')

        except Exception as e:
            self.mylogger.error(f'Error during critical plugin preloading: {e}')
            # 繼續啟動，不因為擴充功能問題阻止應用啟動

    def _finalize_admin_menu(self):
        """在所有擴充功能載入後完成admin menu的設置"""
        # 只有在_adminmenu有菜單項時才添加到主菜單
        if hasattr(self, '_adminmenu') and self._adminmenu.has_menuitem():
            self._mainmenu.append(self._adminmenu)

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