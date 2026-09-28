"""LIB-11：dispatch 使用註冊快照；回呼內再註冊不得污染當次 dispatch。"""
import threading

from funlab.core.hook import HookManager


def test_self_registering_callback_fires_once_per_dispatch():
    fired = []
    hm = HookManager(app=None)

    def self_spawner(ctx):
        fired.append(1)
        if len(fired) < 5:
            hm.register_hook("h", self_spawner)

    hm.register_hook("h", self_spawner)
    hm.call_hook("h")
    assert len(fired) == 1


def test_priority_order_still_applies():
    order = []
    hm = HookManager(app=None)
    hm.register_hook("h", lambda ctx: order.append("late"), priority=200)
    hm.register_hook("h", lambda ctx: order.append("early"), priority=50)
    hm.call_hook("h")
    assert order == ["early", "late"]


def test_concurrent_register_and_call_no_errors():
    hm = HookManager(app=None)
    hm.register_hook("h", lambda ctx: None)
    errors = []

    def reg_worker():
        try:
            for i in range(300):
                hm.register_hook("h", lambda ctx: None, priority=i)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    def call_worker():
        try:
            for _ in range(300):
                hm.call_hook("h")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=reg_worker), threading.Thread(target=reg_worker),
          threading.Thread(target=call_worker), threading.Thread(target=call_worker)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []
