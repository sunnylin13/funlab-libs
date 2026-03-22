from abc import ABC, abstractmethod
import logging, os
from dataclasses import is_dataclass
import platform
import signal
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from itertools import count
from threading import Lock
# from cryptography.fernet import Fernet
from flask import Flask, g, request
from flask_login import AnonymousUserMixin, LoginManager, current_user
from funlab.core.notification import INotificationProvider
from funlab.core.plugin_manager import ModernPluginManager
from funlab.core.plugin import Plugin
from funlab.utils import log
from funlab.core import _Configuable, jinja_filters
from funlab.core.config import Config
from funlab.core.dbmgr import DbMgr
from funlab.core.menu import AbstractMenu, Menu, MenuBar
from funlab.core.hook import HookManager
from funlab.utils import vars2env
from flask_caching import Cache
from sqlalchemy import text, inspect
# APP_ENTITIES_REGISTRY is defined in _entity_registry to avoid pulling Flask
# into every entity module.  It is re-exported here for backward compatibility.
from funlab.core._entity_registry import APP_ENTITIES_REGISTRY  # noqa: F401

app_cache:Cache = Cache()

class PollingNotificationProvider(INotificationProvider):
    """In-memory polling-based notification provider (built-in fallback).

    Implements :class:`~funlab.core.notification.INotificationProvider` using
    an in-memory store.  The browser retrieves notifications by polling
    ``/notifications/poll`` periodically.

    Design: non-destructive reads.
    - Notifications persist on the server until explicitly dismissed by the user.
    - ``fetch_unread`` returns all undismissed notifications and tags each one with
      ``is_recovered=True`` if it was already delivered in a previous poll (so the
      browser can suppress the Toast popup for already-seen items on page reload).
        - ``dismiss_all`` / ``dismiss_items`` are called when the user clicks
            "Clear All" or the individual dismiss button.
    """

    def __init__(self, max_global: int = 200, max_per_user: int = 50):
        self._global: deque = deque(maxlen=max_global)
        # per-user: dict[user_id, dict[notif_id, notification]] for O(1) removal
        self._per_user: dict[int, dict[int, dict]] = defaultdict(dict)
        self._per_user_max = max_per_user

        # Tracks the highest notification ID already delivered to each user.
        # Items with id <= _last_delivered[user_id] are "recovered" on next fetch.
        self._last_delivered_global: dict[int, int] = defaultdict(int)
        self._last_delivered_user: dict[int, int] = defaultdict(int)

        # Set of global notification IDs explicitly dismissed per user.
        self._dismissed_global: dict[int, set] = defaultdict(set)

        self._id_counter = count(1)
        self._lock = Lock()

    def _next_id(self) -> int:
        return next(self._id_counter)

    def _build_notification(self, title: str, message: str, priority: str) -> dict:
        return {
            "id": self._next_id(),
            "event_type": "SystemNotification",
            "priority": str(priority).upper(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {"title": title, "message": message},
            "is_recovered": False,   # overridden at fetch time
            "is_persistent": False,
        }

    def add_user(self, user_id: int, title: str, message: str, priority: str = "NORMAL") -> None:
        with self._lock:
            notif = self._build_notification(title, message, priority)
            user_store = self._per_user[user_id]
            # Enforce per-user cap by evicting the oldest item
            if len(user_store) >= self._per_user_max:
                oldest_id = min(user_store)
                del user_store[oldest_id]
            user_store[notif["id"]] = notif

    def add_global(self, title: str, message: str, priority: str = "NORMAL") -> None:
        with self._lock:
            self._global.append(self._build_notification(title, message, priority))

    def fetch_unread(self, user_id: int) -> list[dict]:
        """Non-destructive read: returns all undismissed notifications.

        Items already delivered in a previous fetch are tagged ``is_recovered=True``
        so the browser banner shows them but omits the Toast popup.
        After fetching, ``_last_delivered`` is advanced to the current maximum,
        so subsequent fetches will correctly tag even newer arrivals.
        """
        import copy
        with self._lock:
            last_global = self._last_delivered_global[user_id]
            dismissed   = self._dismissed_global[user_id]

            global_items = []
            new_max_global = last_global
            for item in self._global:
                if item["id"] in dismissed:
                    continue
                notif = copy.copy(item)
                notif["is_recovered"] = (item["id"] <= last_global)
                global_items.append(notif)
                if item["id"] > new_max_global:
                    new_max_global = item["id"]
            self._last_delivered_global[user_id] = new_max_global

            last_user = self._last_delivered_user[user_id]
            user_store = self._per_user.get(user_id, {})
            user_items = []
            new_max_user = last_user
            for item in user_store.values():
                notif = copy.copy(item)
                notif["is_recovered"] = (item["id"] <= last_user)
                user_items.append(notif)
                if item["id"] > new_max_user:
                    new_max_user = item["id"]
            self._last_delivered_user[user_id] = new_max_user

            return global_items + user_items

    def dismiss_items(self, user_id: int, item_ids: list[int]) -> None:
        """Explicitly remove specific notifications for a user."""
        with self._lock:
            id_set = set(item_ids)
            # Mark global notifications as dismissed (kept in deque for other users)
            self._dismissed_global[user_id].update(id_set)
            # Remove per-user notifications outright
            user_store = self._per_user.get(user_id, {})
            for nid in id_set:
                user_store.pop(nid, None)

    def dismiss_all(self, user_id: int) -> None:
        """Explicitly remove all notifications for a user."""
        with self._lock:
            # Mark all current global IDs as dismissed
            self._dismissed_global[user_id].update(item["id"] for item in self._global)
            # Clear all per-user notifications
            self._per_user.pop(user_id, None)
            # Reset delivery cursors
            self._last_delivered_global.pop(user_id, None)
            self._last_delivered_user.pop(user_id, None)

    # ------------------------------------------------------------------
    # INotificationProvider interface methods
    # ------------------------------------------------------------------

    def send_user_notification(
        self,
        title: str,
        message: str,
        target_userid: int = None,
        priority: str = 'NORMAL',
        expire_after: int = None,
    ) -> None:
        if target_userid is None:
            self.add_global(title, message, priority)
        else:
            self.add_user(target_userid, title, message, priority)

    def send_global_notification(
        self,
        title: str,
        message: str,
        priority: str = 'NORMAL',
        expire_after: int = None,
    ) -> None:
        self.add_global(title, message, priority)
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
        self.plugins:dict[str, Plugin] = {}
        self.app.json.sort_keys = False  # prevent jsonify sort the key when transfer to html page
        self._cleanup_in_progress = False  # re-entrancy guard
        # Initialize notification provider BEFORE super().__init__()
        # because register_routes() is called during _FlaskBase.__init__()
        # and needs self.notification_provider to be available.
        self.notification_provider: INotificationProvider = PollingNotificationProvider()
        self._init_configuration(configfile, envfile)
        self._init_menu_container()

        # Initialize the modern plugin manager.
        self.plugin_manager = ModernPluginManager(self)

        # Initialize the hook manager.
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

        # Plugins expose entity classes for table creation; create them once here.
        if self.dbmgr:
            self.dbmgr.create_registry_tables(APP_ENTITIES_REGISTRY)

        # After plugin registration completes, trigger the shared prewarm framework.
        # Each plugin should register its own prewarm tasks during initialization.
        self._run_prewarm()

        self._setup_exit_signal_handler(self._cleanup_on_exit)

    def _cleanup_on_exit(self, signal_received, frame):
        """
        cleanup on exit for flask app and plugins
        """
        # Prevent re-entrancy.
        if self._cleanup_in_progress:
            self.mylogger.warning('Cleanup already in progress, ignoring repeated call')
            return

        self._cleanup_in_progress = True

        try:
            self.mylogger.info('Funlab Flask cleanup_on_exit ...')
            # Prefer cleanup through the modern plugin manager.
            if hasattr(self, 'plugin_manager'):
                self.plugin_manager.cleanup()
            else:
                # Legacy fallback cleanup path.
                for plugin in reversed(self.plugins.values()):
                    try:
                        plugin.unload()
                    except Exception as e:
                        self.mylogger.error(f'Error unloading plugin {plugin}: {e}')

            # Flush database state if needed.
            if self.dbmgr:
                try:
                    self.dbmgr.flush_on_shutdown()
                except Exception as e:
                    self.mylogger.error(f'Error flushing DB on shutdown: {e}', exc_info=True)
                finally:
                    self.dbmgr.release()

            self.mylogger.info('Funlab Flask cleanup completed.')
        finally:
            self._cleanup_in_progress = False

        sys.exit(0)

    def _setup_exit_signal_handler(self, signal_handler:callable):
        """Register process-exit signal handlers when supported."""
        # SIGTERM and SIGINT are available on all major platforms.
        signal.signal(signal.SIGTERM, signal_handler)  # e.g. kill <pid> or service-manager stop
        signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C

        # SIGHUP is only available on Unix-like systems.
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
    #     """Legacy plugin registration helper."""
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
    #     if isinstance(plugin, (SecurityPlugin, SecurityPlugin)):
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

            # Log the unhandled exception.
            self.mylogger.error(f'Unhandled exception: {error}', exc_info=True)

            # Return the default error page or JSON response.
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
        """Register plugins through the modern plugin manager."""
        self.login_manager = None
        self.mylogger.info('Funlab Flask registering plugins...')

        # Registration order is determined by plugin dependency declarations.
        self.plugin_manager.register_plugins(
            group='funlab_plugin',
            force_refresh=self.config.get('RESCAN_PLUGINS', False)
        )

        # Configure the default login manager.
        if self.login_manager is None:
            self.login_manager = LoginManager()
            self.login_manager.init_app(self)
            self.login_manager.login_view = 'root_bp.blank'

            # Mark this as a default login manager so a security plugin may replace it.
            self.login_manager._default_user_loader = True

            @self.login_manager.user_loader
            def user_loader(user_id):
                anonymous = AnonymousUserMixin()
                setattr(anonymous, 'name', 'anonymous')
                return anonymous

        # Finalize the admin menu without deleting the backing attribute.
        # Lazy-loaded plugins may still append menu items later.
        self._finalize_admin_menu()

    def _finalize_admin_menu(self):
        """Finalize admin-menu wiring after plugin loading completes."""
        # Only append the admin menu when it actually contains items.
        if hasattr(self, '_adminmenu') and self._adminmenu.has_menuitem():
            self._mainmenu.append(self._adminmenu)

    def _run_prewarm(self) -> None:
        """Trigger all registered deferred-import prewarm tasks.

        Plugins should register tasks from ``register_prewarm_tasks()`` using
        ``funlab.core.prewarm.register_prewarm()``. Blocking tasks run
        synchronously; others run in daemon threads.

        This behaviour can be disabled with ``app_config['PREWARM_ENABLED']``.
        """
        from funlab.core import prewarm as _pw
        prewarm_enabled = self.config.get('PREWARM_ENABLED', True)
        if not prewarm_enabled:
            self.mylogger.info('Prewarm disabled by PREWARM_ENABLED=False')
            return
        n = len(_pw._entries)
        self.mylogger.info('Triggering %d deferred import(s) ...', n)
        _pw.run(app=self)

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