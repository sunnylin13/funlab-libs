from __future__ import annotations

from collections.abc import Iterable
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import threading
from typing import ClassVar


@dataclass
class AbstractMenu(ABC):
    """
    Abstract base class for menu items.

    Attributes:
        title (str): The title of the menu item.
        icon (str): The icon of the menu item.
        badge (str): The badge of the menu item.
        _parent (Menu|MenuBar): The parent menu or menu bar.
        admin_only (bool): Indicates if the menu item is accessible only to administrators.
    """
    title:str
    icon:str = field(default='')
    badge: str = field(default='')
    _parent:Menu|MenuBar = field(init=False, default=None)
    admin_only: bool = field(default=False)
    _lock = threading.RLock()

    @property
    def basic_data(self):
        return self.icon_html() + self.title_html() + self.badge_html() # self._basic_data_template.format(title=self.title, icon=self.icon, badge=self.badge)

    @property
    def level(self):
        if self.parent:
            return 1 + self.parent.level
        else:
            return 1

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, menu:Menu):
        if menu.dummy:
            self._parent = menu.parent
        else:
            self._parent = menu

    @property
    def is_ontop(self):
        return isinstance(self.parent, MenuBar) or (self.parent is None)

    @abstractmethod
    def html(self, layout, user):
        # if has login_manager, then pass in user, each user has their own mainmenu, and determinie memu accessible by user
        raise NotImplementedError

    def is_accessible(self, user):
        if user:
            return (not self.admin_only) or (getattr(user, 'is_admin', False))
        else:
            return False

    def title_html(self):
        if self.title:
            return f'<span class="nav-link-title">{self.title}</span>'
        else:
            return ''

    def icon_html(self):
        if not self.icon:
            return ''
        elif self.icon.startswith('<svg'):
            return f'<span class="nav-link-icon d-md-none d-lg-inline-block">{self.icon}</span>'
        else:  # self.icon.endswith('.svg'):
            return f'<span class="nav-link-icon d-md-none d-lg-inline-block"><img src="{self.icon}" width="24" height="24" alt="{self.title}" class="icon icon-tabler"></span>'

    def badge_html(self):
        if self.badge:
            return f'<span class="badge badge-sm bg-green-lt text-uppercase ms-auto">{self.badge}</span>'
        else:
            return ''

@dataclass
class MenuBar:
    _menu: Menu = field(init=False)
    _virtical_template: ClassVar[str]="""
        <aside class="navbar navbar-vertical navbar-expand-lg" data-bs-theme="{theme}">
            <div class="container-fluid">
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#sidebar-menu" aria-controls="sidebar-menu" aria-expand="false" aria-label="Toggle navigation">
                <span class="navbar-toggler-icon"></span>
            </button>
            <h1 class="navbar-brand navbar-brand-autodark">
                <a href="{href}">
                <img src="{icon}" height="24" alt="{title}" class="navbar-brand-image">
                </a>
            </h1>
            <div class="collapse navbar-collapse" id="sidebar-menu">
                <ul class="navbar-nav pt-lg-3">
                    {sub_menus}
                </ul>
            </div>
            </div>
        </aside>
        """
    _horizontal_template: ClassVar[str]="""
        <div id="navbar-menu" class="collapse navbar-collapse">
            <div class="d-flex flex-column flex-md-row flex-fill align-items-stretch align-items-md-center">
                <ul class="navbar-nav">
                    {sub_menus}
                </ul>
            </div>
        </div>
        """
    def __init__(self, title='', icon='', href='/', theme='light'):
        self._menu = Menu(title=title, icon=icon, badge='mainmenu', dummy=True)
        self.title = title
        self.icon = icon
        self.href = href
        self._menu._parent = self
        self.theme = theme
        self.collapsible = False
        self._menu.collapsible = False

    def append(self, menus:list[AbstractMenu]|AbstractMenu, ask_ord=999)->Menu:

        self._menu.append(menus)
        return self._menu

    def insert(self, idx:int, menus:list[AbstractMenu]|AbstractMenu)->Menu:
        self._menu.insert(idx, menus)
        return self._menu

    def html(self, layout, user=None):
        sub_menus = self._menu.html(layout, user)
        if layout == 'horizontal':
            html = self._horizontal_template.format(title=self.title, icon=self.icon, href=self.href, sub_menus=sub_menus)
        else:
            html = self._virtical_template.format(title=self.title, icon=self.icon, href=self.href, theme=self.theme, sub_menus=sub_menus)
        return html

@dataclass
class Menu(AbstractMenu):
    _menus: list[AbstractMenu] = field(default_factory=lambda: [])
    dummy: bool = False  # if dummy, need not html from self. For used in MenuBar use Menu as top container
    collapsible: bool = True
    expand: bool = None  # show it's menuitem
    drop_style = 'dropdown'  # ["dropdown" v, "dropend" >]
    top_menu_template: ClassVar[str]="""
        <li class="nav-item {drop_style}">
            <a class="nav-link dropdown-toggle {show}" href="#" data-bs-toggle="dropdown" data-bs-auto-close="{auto_close}" role="button">
                {basic_data}
            </a>
            <div class="dropdown-menu {show}">
                {menuitem}
            </div>
        </li>
        """
    menu_template: ClassVar[str]="""
        <div class="{drop_style}">
            <a class="dropdown-item dropdown-toggle" href="#" data-bs-toggle="dropdown" data-bs-auto-close="{auto_close}" role="button">
                {basic_data}
            </a>
            <div class="dropdown-menu {show}">
                <div class="dropdown-menu-columns">
                    <div class="dropdown-menu-column">
                    {menuitem}
                    </div>
                </div>
            </div
        </div>
        """

    @property
    def show(self):
        if self.expand is None:
            if self.is_ontop:
                return 'show'
            else:
                return ''
        elif self.expand:
            return 'show'
        else:
            return ''

    def has_menuitem(self)-> bool:
        for menu in self._menus:
            if isinstance(menu, MenuItem):
                return True
            elif isinstance(menu, Menu):
                return menu.has_menuitem()
            else:
                continue
        return False

    def append(self, menu:AbstractMenu|list[AbstractMenu]):
        if not menu:
            return
        if isinstance(menu, Iterable):
            for m in menu:
                self.append(m)
        else:
            if isinstance(menu, Menu):
                if menu.dummy or (self.title==menu.title and self.icon==menu.icon and self.badge==menu.badge):
                    self.append(menu._menus)
                else:
                    for m in self._menus:
                        if (m.title==menu.title and m.icon==menu.icon and m.badge==menu.badge):
                            m.append(menu._menus)
                            break
                    else:
                        self._menus.append(menu)
            else:  # MenuItem
                self._menus.append(menu)
            menu.parent = self
        return self

    def insert(self, idx:int, menu:AbstractMenu|list[AbstractMenu]):
        if not menu:
            return
        if idx < 0:
            self.append(menu)
            return

        if isinstance(menu, Iterable):
            for m in menu:
                self.insert(idx, m)
                idx += 1
        else:
            if isinstance(menu, Menu):
                if menu.dummy or (self.title==menu.title and self.icon==menu.icon and self.badge==menu.badge):
                    self.insert(idx, menu._menus)
                else:
                    self._menus.insert(idx, menu)
            else:  # MenuItem
                self._menus.insert(idx, menu)
            menu.parent = self
        return self

    def html(self, layout, user=None):
        with self._lock:
            if not self.is_accessible(user):
                return ''
            submenu_htmls = ''
            for menu in self._menus:
                submenu_htmls += menu.html(layout, user)
            drop_style = self.drop_style
            if layout == 'horizontal':
                show = ''
                auto_close = 'outside'
                if not self.is_ontop:
                    drop_style = 'dropend'
            else:
                show = self.show
                auto_close = 'false'
            if self.dummy:
                html = submenu_htmls
            else:
                if self.is_ontop:
                    html = self.top_menu_template.format(basic_data=self.basic_data, menuitem=submenu_htmls,
                                                         drop_style=drop_style, show=show, auto_close=auto_close)
                else:
                    html = self.menu_template.format(basic_data=self.basic_data, menuitem=submenu_htmls,
                                                     drop_style=drop_style, show=show, auto_close=auto_close)
            return html

@dataclass
class MenuItem(AbstractMenu):
    href: str='#'
    top_menuitem_template: ClassVar[str]="""
        <li class="nav-item">
            <a class="nav-link" href="{href}">
                {basic_data}
            </a>
        </li>
        """
    menuitem_template: ClassVar[str]="""
        <a class="dropdown-item" href="{href}">
            {basic_data}
        </a>
        """
    def html(self, layout, user):
        with self._lock:
            if not self.is_accessible(user):
                return ''
            if self.is_ontop:
                html = self.top_menuitem_template.format(href=self.href, basic_data=self.basic_data)
            else:
                html = self.menuitem_template.format(href=self.href, basic_data=self.basic_data)
            return html

@dataclass
class MenuDivider(MenuItem):

    def __init__(self):
        super().__init__(title='')

    def html(self, layout, user):
        if not self.is_accessible(user):
            return ''
        return f'<div class="dropdown-divider">{self.title}</div>'


