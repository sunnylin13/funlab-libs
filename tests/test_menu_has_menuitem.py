"""LIB-08：has_menuitem 必須檢查所有子節點，不得因空子 Menu 提前返回。"""
from funlab.core.menu import Menu, MenuItem


def test_menuitem_after_empty_submenu_counts():
    root = Menu(title="root")
    root.append(Menu(title="empty-sub"))
    root.append(MenuItem(title="real", href="/x"))
    assert root.has_menuitem() is True


def test_all_empty_returns_false():
    root = Menu(title="root")
    root.append(Menu(title="a"))
    root.append(Menu(title="b"))
    assert root.has_menuitem() is False


def test_direct_menuitem_counts():
    root = Menu(title="root")
    root.append(MenuItem(title="only", href="/"))
    assert root.has_menuitem() is True
