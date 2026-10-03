"""Q7 裁示（PM 2026-09-28）同步測試：send_user_notification 的 target_userid 必填。

與 SSE 側（funlab-sse PR#4，main@ee73f87）同款 guard：``None`` 於入口顯式拒絕
（log warning＋不產生任何通知），不得降級為廣播；廣播請改用
``send_global_notification``。
"""
from funlab.core.appbase import PollingNotificationProvider
from funlab.core.notification import INotificationProvider
import inspect


class _RecordingProvider(INotificationProvider):
    """最小實作，用於檢查介面簽名与文件（不測行為）。"""

    def __init__(self):
        self.calls = []

    def send_user_notification(self, title, message, target_userid,
                               priority='NORMAL', expire_after=None):
        self.calls.append(('user', target_userid))

    def send_global_notification(self, title, message,
                                 priority='NORMAL', expire_after=None):
        self.calls.append(('global', None))

    def fetch_unread(self, user_id):
        return []

    def dismiss_items(self, user_id, item_ids):
        pass

    def dismiss_all(self, user_id):
        pass


# ---------------------------------------------------------------------------
# 介面層（notification.py）：target_userid 必須為必填（無 None 預設值）
# ---------------------------------------------------------------------------

def test_interface_target_userid_is_required():
    sig = inspect.signature(INotificationProvider.send_user_notification)
    param = sig.parameters["target_userid"]
    assert param.default is inspect.Parameter.empty, (
        "Q7 裁示：target_userid 必填，介面不得保留 None 預設值"
    )


def test_interface_docstring_forbids_none_directs_to_broadcast():
    doc = INotificationProvider.send_user_notification.__doc__ or ""
    assert "send_global_notification" in doc, (
        "docstring 必須明示「廣播請用 send_global_notification」"
    )
    # 不得再出現「None 表示全域/廣播」的舊敘述
    assert "None`` 表示廣播" not in doc and "None 表示廣播" not in doc


# ---------------------------------------------------------------------------
# 實作層（PollingNotificationProvider）：None 顯式拒絕，不降級廣播
# ---------------------------------------------------------------------------

def test_polling_none_target_is_rejected_not_broadcast():
    p = PollingNotificationProvider()
    p.send_user_notification("t", "body", target_userid=None)  # pyright: ignore[reportArgumentType]  # 故意測 None 被拒
    # 任何使用者都不得收到這則「孤列/降級廣播」
    assert p.fetch_unread(1) == []
    assert p.fetch_unread(2) == []


def test_polling_none_rejection_emits_warning(caplog):
    p = PollingNotificationProvider()
    with caplog.at_level("WARNING"):
        p.send_user_notification("t", "body", target_userid=None)  # pyright: ignore[reportArgumentType]  # 故意測 None 被拒
    messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("send_user_notification" in m for m in messages), (
        "拒絕 None 時必須留 warning 痕跡（與 SSE 側同款 guard）"
    )


def test_polling_explicit_target_still_delivers():
    p = PollingNotificationProvider()
    p.send_user_notification("t", "body", target_userid=7)
    items = p.fetch_unread(7)
    assert len(items) == 1
    assert items[0]["payload"]["title"] == "t"
    # 不得洩漏給其他使用者
    assert p.fetch_unread(8) == []


def test_polling_broadcast_route_unchanged():
    """send_global_notification 仍是唯一合法廣播路徑（紅線：不動其行為）。"""
    p = PollingNotificationProvider()
    p.send_global_notification("t", "body")
    assert len(p.fetch_unread(1)) == 1
    assert len(p.fetch_unread(2)) == 1
