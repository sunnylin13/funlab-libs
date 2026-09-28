"""LIB-16：run() 後註冊必須留下 WARNING，且既有首次 run 語意不變。"""
import logging

import pytest

import funlab.core.prewarm as pw


@pytest.fixture(autouse=True)
def _clean():
    pw.reset()
    yield
    pw.reset()


def test_late_register_warns(caplog):
    pw.register("early", lambda: None, blocking=True)
    pw.run()
    with caplog.at_level(logging.WARNING, logger="funlab.core.prewarm"):
        pw.register("late", lambda: None, blocking=True)
    assert any("AFTER prewarm.run()" in r.message for r in caplog.records)
    assert pw.status()["late"]["status"] == "pending"


def test_normal_register_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="funlab.core.prewarm"):
        pw.register("ok", lambda: None)
    assert not any("AFTER prewarm.run()" in r.message for r in caplog.records)
