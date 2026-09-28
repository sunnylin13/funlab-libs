"""LIB-12：dismiss 記錄必須有界（全域佇列淘汰後不再記憶已死 id）。"""
from funlab.core.appbase import PollingNotificationProvider


def test_dismissed_global_set_stays_bounded():
    p = PollingNotificationProvider(max_global=200)
    for i in range(10_000):
        p.add_global("t", f"m{i}")
        p.dismiss_all(1)
    assert len(p._dismissed_global[1]) <= 200


def test_dismiss_items_still_hides_live_global():
    p = PollingNotificationProvider(max_global=200)
    p.add_global("t", "one")
    nid = p._global[-1]["id"]
    p.dismiss_items(1, [nid])
    assert p.fetch_unread(1) == []


def test_dismiss_all_hides_current_globals():
    p = PollingNotificationProvider(max_global=200)
    for i in range(10):
        p.add_global("t", f"m{i}")
    p.dismiss_all(1)
    assert p.fetch_unread(1) == []
