"""ARCH-LOGGING PR-3（設計 §4 N1/N2）：SuppressedLogger 抑制窗語義單元測試。

seam：funlab.utils.log_noise.SuppressedLogger.warning(key, msg, *args)
規格來源（期望值獨立出處＝KB dev/designs/ARCH-LOGGING §4＋卡 t_242ca7a5 測試規範）：
- 首筆全量 WARNING。
- 窗（默認 600 秒）內同 key 同訊息：計數、不輸出。
- 窗末匯總一行「重複 N 次、時間範圍」（在下一窗開啟時或 flush 時輸出）。
- 連續失敗跨越升級閾值（默認 1800 秒）：升回 ERROR 一次（每段故障期一次）。
- 成功/恢復呼叫 reset(key) 後重新計段（下次失敗＝新首筆）。
clock 由建構注入，測試用假 clock，不 sleep。
"""
from __future__ import annotations

import logging

import pytest

from funlab.utils.log_noise import SuppressedLogger


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, secs: float) -> None:
        self.now += secs


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def texts(self, level: int | None = None):
        return [r.getMessage() for r in self.records if level is None or r.levelno == level]


@pytest.fixture()
def suppressed():
    logger = logging.getLogger("test.log_noise")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    h = ListHandler()
    logger.addHandler(h)
    clock = FakeClock()
    sup = SuppressedLogger(logger, clock=clock)
    yield sup, h, clock
    logger.handlers.clear()


def test_first_message_logged_in_full(suppressed):
    """規格：窗內首筆一律全量 WARNING（設計 §4 抑制窗「首筆全量」）。"""
    sup, h, _ = suppressed
    sup.warning("k1", "探測失敗: %s", "timeout")
    assert h.texts(logging.WARNING) == ["探測失敗: timeout"]


def test_within_window_suppressed(suppressed):
    """規格：同 key 窗內（<600s）僅計數，不逐筆輸出。"""
    sup, h, clock = suppressed
    sup.warning("k1", "探測失敗: %s", "timeout")
    for _ in range(3):
        clock.advance(10)
        sup.warning("k1", "探測失敗: %s", "timeout")
    assert len(h.texts(logging.WARNING)) == 1, "窗內 3 筆不得產生輸出"


def test_window_end_summary(suppressed):
    """規格：窗末匯總一行「重複 N 次、時間範圍」，不靜默丟棄。

    期望值獨立來源：卡 body「窗末匯總『重複 N 次、時間範圍』」。
    窗內共 4 筆重複（首筆後 4 次呼叫）→ 匯總行必含「重複 4 次」。
    """
    sup, h, clock = suppressed
    t0 = clock.now
    sup.warning("k1", "探測失敗: %s", "timeout")
    for _ in range(4):
        clock.advance(100)  # 100×4=400s，仍 < 600s 窗
        sup.warning("k1", "探測失敗: %s", "timeout")
    # 跨窗觸發匯總
    clock.advance(300)  # 距首筆 700s > 600s
    sup.warning("k1", "探測失敗: %s", "timeout")
    summaries = [t for t in h.texts(logging.WARNING) if "重複" in t]
    assert len(summaries) == 1
    assert "重複 4 次" in summaries[0]
    # 時間範圍：含起與末兩個時間戳（HH:MM:SS）
    import re
    assert len(re.findall(r"\d{2}:\d{2}:\d{2}", summaries[0])) == 2, \
        f"匯總行需含起止時間範圍: {summaries[0]!r}"


def test_flush_emits_pending_summary(suppressed):
    """規格：抑制窗未自然跨窗時，flush() 輸出窗內累計（恢復時匯總用）。"""
    sup, h, clock = suppressed
    sup.warning("k1", "探測失敗: %s", "timeout")
    clock.advance(30)
    sup.warning("k1", "探測失敗: %s", "timeout")
    sup.flush()
    assert any("重複 1 次" in t for t in h.texts(logging.WARNING))
    # flush 後窗計數歸零，重複 flush 不再輸出
    h.records.clear()
    sup.flush()
    assert h.texts(logging.WARNING) == []


def test_escalate_to_error_once(suppressed):
    """規格：連續失敗跨越升級閾值（默認 30 分鐘）升回 ERROR 一次；
    同段故障期內不重複升。"""
    sup, h, clock = suppressed
    sup.warning("k1", "探測失敗: %s", "timeout")
    # 每次 550s 觸窗輪替；第 4 次呼叫時距首筆 2200s > 1800s
    for _ in range(4):
        clock.advance(550)
        sup.warning("k1", "探測失敗: %s", "timeout")
    errors = h.texts(logging.ERROR)
    assert len(errors) == 1, f"升級 ERROR 須恰一筆，實際 {len(errors)}"
    # 之後繼續失敗不再重複升 ERROR
    for _ in range(3):
        clock.advance(700)
        sup.warning("k1", "探測失敗: %s", "timeout")
    assert len(h.texts(logging.ERROR)) == 1


def test_reset_starts_new_episode(suppressed):
    """規格：reset(key)（探測成功/恢復）後重新計段——下次失敗＝首筆全量，
    升級計時歸零。"""
    sup, h, clock = suppressed
    sup.warning("k1", "探測失敗: %s", "timeout")
    clock.advance(60)
    sup.warning("k1", "探測失敗: %s", "timeout")
    sup.reset("k1")
    h.records.clear()
    clock.advance(10)
    sup.warning("k1", "探測失敗: %s", "timeout")
    assert h.texts(logging.WARNING) == ["探測失敗: timeout"]


def test_different_keys_independent(suppressed):
    """規格：抑制窗按 (key, 訊息) 分離——不同 provider／不同訊息互不吞。"""
    sup, h, clock = suppressed
    sup.warning("a", "訊息A")
    sup.warning("b", "訊息B")
    sup.warning("a", "訊息A")  # 窗內吞
    msgs = h.texts(logging.WARNING)
    assert msgs == ["訊息A", "訊息B"]
