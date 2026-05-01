"""T-libs-003 + T-libs-004 單元測試：PluginCycleError 與 Notifier Protocol。"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# T-libs-003：PluginManager 拓撲排序與 PluginCycleError
# ---------------------------------------------------------------------------

def _make_metadata(name: str, deps: list[str] | None = None):
    from funlab.core.plugin_manager import PluginMetadata
    return PluginMetadata(name=name, dependencies=deps or [])


def test_topological_order_a_b_c():
    """A→B→C 依賴鏈應以 C、B、A 順序載入（依賴先於被依賴方）。"""
    from funlab.core.plugin_manager import PluginDependencyResolver
    plugins = {
        "A": _make_metadata("A", ["B"]),
        "B": _make_metadata("B", ["C"]),
        "C": _make_metadata("C"),
    }
    resolver = PluginDependencyResolver()
    order = resolver.resolve_load_order(plugins)
    assert order.index("C") < order.index("B") < order.index("A")


def test_no_dependency_order_is_deterministic():
    """無依賴時以字母排序，結果應確定性。"""
    from funlab.core.plugin_manager import PluginDependencyResolver
    plugins = {
        "Z": _make_metadata("Z"),
        "A": _make_metadata("A"),
        "M": _make_metadata("M"),
    }
    resolver = PluginDependencyResolver()
    order = resolver.resolve_load_order(plugins)
    assert order == ["A", "M", "Z"]


def test_cycle_raises_plugin_cycle_error():
    """A→B→A 循環依賴應丟出 PluginCycleError。"""
    from funlab.core.plugin_manager import PluginDependencyResolver, PluginCycleError
    plugins = {
        "A": _make_metadata("A", ["B"]),
        "B": _make_metadata("B", ["A"]),
    }
    resolver = PluginDependencyResolver()
    with pytest.raises(PluginCycleError):
        resolver.resolve_load_order(plugins)


def test_plugin_cycle_error_is_value_error():
    """PluginCycleError 應繼承 ValueError（向後相容）。"""
    from funlab.core.plugin_manager import PluginCycleError
    err = PluginCycleError("MyPlugin")
    assert isinstance(err, ValueError)
    assert "MyPlugin" in str(err)


def test_missing_hard_dep_skips_plugin():
    """缺少必要依賴的 plugin 應從載入順序中移除。"""
    from funlab.core.plugin_manager import PluginDependencyResolver
    plugins = {
        "A": _make_metadata("A", ["MissingDep"]),
        "B": _make_metadata("B"),
    }
    resolver = PluginDependencyResolver()
    order = resolver.resolve_load_order(plugins)
    assert "A" not in order
    assert "B" in order


# ---------------------------------------------------------------------------
# T-libs-004：Notifier Protocol + NotificationMessage
# ---------------------------------------------------------------------------

def test_notification_message_defaults():
    """NotificationMessage 預設值正確。"""
    from funlab.core.notification import NotificationMessage
    msg = NotificationMessage(title="測試", message="內容")
    assert msg.priority == "NORMAL"
    assert msg.target_userid is None
    assert msg.expire_after is None


def test_notification_message_custom_fields():
    """NotificationMessage 自訂欄位正確設定。"""
    from funlab.core.notification import NotificationMessage
    msg = NotificationMessage(
        title="警告",
        message="磁碟空間不足",
        priority="HIGH",
        target_userid=42,
        expire_after=3600,
    )
    assert msg.priority == "HIGH"
    assert msg.target_userid == 42
    assert msg.expire_after == 3600


def test_notifier_protocol_isinstance():
    """實作 send 方法的物件應通過 isinstance(obj, Notifier) 檢查。"""
    from funlab.core.notification import Notifier, NotificationMessage

    class MyNotifier:
        def send(self, message: NotificationMessage) -> None:
            pass

    assert isinstance(MyNotifier(), Notifier)


def test_object_without_send_not_notifier():
    """未實作 send 方法的物件不符合 Notifier Protocol。"""
    from funlab.core.notification import Notifier

    class NotANotifier:
        def emit(self, msg):
            pass

    assert not isinstance(NotANotifier(), Notifier)


def test_notifier_protocol_is_runtime_checkable():
    """Notifier 必須可在執行期做 isinstance 檢查。"""
    from funlab.core.notification import Notifier
    # runtime_checkable 的直接驗證：對任意物件做 isinstance 不拋 TypeError
    try:
        result = isinstance(object(), Notifier)
    except TypeError:
        pytest.fail("Notifier 未宣告為 @runtime_checkable，isinstance 拋出 TypeError")
