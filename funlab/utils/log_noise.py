"""funlab.utils.log_noise — 探測路徑抑制窗（ARCH-LOGGING 設計 §4，PR-3）。

規範條文（LOGGING.md §3）：
- 禁止在迴圈／探測路徑中無抑制地逐次 ``log.error``；可自癒失敗用 WARNING＋計數。
- 抑制窗匯總行必含「重複 N 次、時間範圍」，不靜默丟棄。

:class:`SuppressedLogger` 語義（KB dev/designs/ARCH-LOGGING §4 N1/N2）：
- 窗（默認 600 秒）內同 key 同訊息：首筆全量 WARNING，其後只計數。
- 窗結束（下一次呼叫跨窗、或 :meth:`flush`）：匯總一行「重複 N 次、時間範圍」。
- 連續失敗跨越升級閾值（默認 1800 秒）：升回 ERROR 一次（每段故障期一次），
  之後同段不再重複升；成功後呼叫 :meth:`reset` 重新計段。

執行緒安全：內部單把鎖；呼叫端（探測迴圈）多執行程常見。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

DEFAULT_WINDOW_SECONDS = 600.0      # 10 分鐘抑制窗（設計 §4 N1）
DEFAULT_ESCALATE_SECONDS = 1800.0   # 30 分鐘升級閾值（設計 §4 N1）


class _Episode:
    """一段連續失敗的抑制窗狀態（同 key 同訊息）。"""

    __slots__ = ("window_start", "first_at", "last_dup_at", "dup_count",
                 "escalated", "msg")

    def __init__(self, now: float, msg: str) -> None:
        self.window_start = now
        self.first_at = now
        self.last_dup_at: float | None = None
        self.dup_count = 0
        self.escalated = False
        self.msg = msg


class SuppressedLogger:
    """對單一 logger 套抑制窗的包裝（探測／重試路徑專用）。

    Args:
        logger: 實際輸出的 :class:`logging.Logger`。
        window: 抑制窗秒數（默認 600＝10 分鐘）。
        escalate_after: 自故障段起連續失敗超過此秒數時升 ERROR 一次
            （默認 1800＝30 分鐘）。
        clock: 時間來源（單測注入假時鐘；默認 :func:`time.monotonic`）。
    """

    def __init__(self, logger: logging.Logger,
                 window: float = DEFAULT_WINDOW_SECONDS,
                 escalate_after: float = DEFAULT_ESCALATE_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._logger = logger
        self._window = window
        self._escalate_after = escalate_after
        self._clock = clock
        self._lock = threading.Lock()
        self._episodes: dict[tuple, _Episode] = {}

    # ------------------------------------------------------------------
    def warning(self, key: str, msg: str, *args) -> None:
        """記錄一條「預期內可自癒」的失敗（WARNING＋抑制窗）。

        Args:
            key: 抑制主鍵（慣例：provider 名／適配器名）。
            msg: 訊息模板（同 key 同模板共用一個窗）。
            *args: 模板參數。
        """
        rendered = msg % args if args else msg
        now = self._clock()
        sub = (key, rendered)
        summary: str | None = None
        escalate: str | None = None
        first = False

        with self._lock:
            ep = self._episodes.get(sub)
            if ep is None:
                ep = self._episodes[sub] = _Episode(now, rendered)
                first = True
            else:
                if now - ep.window_start >= self._window:
                    # 窗結束：先匯總舊窗，再開新窗
                    summary = self._summary(ep, now)
                    ep.window_start = now
                    ep.dup_count = 0
                    ep.last_dup_at = None
                ep.dup_count += 1
                ep.last_dup_at = now
                if not ep.escalated and now - ep.first_at >= self._escalate_after:
                    ep.escalated = True
                    escalate = (f"{rendered}（連續失敗已逾 "
                                f"{self._escalate_after:.0f} 秒，自 "
                                f"{_hhmmss(ep.first_at)} 起未恢復）")

        if first:
            self._logger.warning(rendered)
        else:
            if summary is not None:
                self._logger.warning(summary)
            if escalate is not None:
                self._logger.error(escalate)

    def flush(self, key: str | None = None) -> None:
        """匯總並清空窗內累計（恢復/上線時呼叫，确保末窗計數不丟）。

        Args:
            key: 只 flush 該主鍵；``None``＝全部。
        """
        now = self._clock()
        out: list[str] = []
        with self._lock:
            for sub in list(self._episodes):
                if key is not None and sub[0] != key:
                    continue
                ep = self._episodes.pop(sub)
                if ep.dup_count:
                    line = self._summary(ep, now)
                    if line is not None:
                        out.append(line)
        for line in out:
            self._logger.warning(line)

    def reset(self, key: str) -> None:
        """故障段結束（探測成功）：清空該 key 全部窗與升級狀態。"""
        with self._lock:
            for sub in [s for s in self._episodes if s[0] == key]:
                del self._episodes[sub]

    # ------------------------------------------------------------------
    @staticmethod
    def _summary(ep: _Episode, now: float) -> str | None:
        if not ep.dup_count:
            return None
        end = ep.last_dup_at if ep.last_dup_at is not None else now
        return (f"{ep.msg}（重複 {ep.dup_count} 次，"
                f"時間範圍 {_hhmmss(ep.first_at)}–{_hhmmss(end)}）")


def _hhmmss(epochish: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(epochish))
