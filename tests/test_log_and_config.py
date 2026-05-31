"""T-libs-001 + T-libs-002 單元測試：setup_logging / LogConfig 與 load_config / ConfigError。"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# T-libs-001：LogConfig + setup_logging
# ---------------------------------------------------------------------------

def test_logconfig_defaults():
    """LogConfig 預設值正確。"""
    from funlab.utils.log import LogConfig, LogType, LogFmtType
    cfg = LogConfig()
    assert cfg.level == logging.INFO
    assert cfg.logtype == LogType.STDOUT
    assert isinstance(cfg.fmt, LogFmtType)
    assert cfg.filename is None


def test_setup_logging_stdout(capfd):
    """setup_logging 以 STDOUT 模式設定後，root logger 可輸出。"""
    from funlab.utils.log import setup_logging, LogConfig, LogType, LogFmtType
    cfg = LogConfig(level=logging.WARNING, logtype=LogType.STDOUT, fmt=LogFmtType.SHORT)
    setup_logging(cfg)
    root = logging.getLogger()
    assert root.level == logging.WARNING
    # 至少有一個 handler
    assert len(root.handlers) >= 1


def test_setup_logging_off():
    """LogType.OFF 模式只加 NullHandler。"""
    from funlab.utils.log import setup_logging, LogConfig, LogType
    setup_logging(LogConfig(logtype=LogType.OFF))
    root = logging.getLogger()
    assert any(isinstance(h, logging.NullHandler) for h in root.handlers)


def test_setup_logging_none_uses_defaults():
    """傳入 None 時使用預設設定不會拋例外。"""
    from funlab.utils.log import setup_logging
    setup_logging(None)  # 不應拋例外


def test_setup_logging_file(tmp_path):
    """LogType.FILE 模式會建立 FileHandler 並寫入檔案。"""
    from funlab.utils.log import setup_logging, LogConfig, LogType, LogFmtType
    log_file = str(tmp_path / "test.log")
    setup_logging(LogConfig(level=logging.DEBUG, logtype=LogType.FILE, filename=log_file))
    root = logging.getLogger()
    assert any(isinstance(h, logging.FileHandler) for h in root.handlers)
    # 清理 handler
    for h in root.handlers.copy():
        if isinstance(h, logging.FileHandler):
            h.close()
            root.removeHandler(h)


# ---------------------------------------------------------------------------
# T-libs-002：Config / ConfigError（直接使用 funlab.core.config.Config）
# ---------------------------------------------------------------------------

def test_config_toml(tmp_path):
    """從 TOML 檔載入並以 Config 物件存取值。"""
    from funlab.core.config import Config

    toml_file = tmp_path / "app.toml"
    toml_file.write_text('[app]\nhost = "localhost"\nport = 9090\n', encoding="utf-8")

    cfg = Config(toml_file)
    assert isinstance(cfg, Config)
    assert cfg.app["host"] == "localhost"
    assert cfg.app["port"] == 9090


def test_config_env_var_syntax(tmp_path):
    """{{ENV_VAR:NAME}} 語法：從 env dict 替換變數。"""
    from funlab.core.config import Config

    toml_file = tmp_path / "cfg.toml"
    toml_file.write_text('host = "{{ENV_VAR:TEST_HOST}}"\n', encoding="utf-8")

    cfg = Config(toml_file, env_file_or_values={"TEST_HOST": "envhost"})
    assert cfg.host == "envhost"


def test_config_missing_env_var(tmp_path):
    """{{ENV_VAR:NAME}} 參照未提供的變數時拋例外。"""
    from funlab.core.config import Config

    toml_file = tmp_path / "cfg.toml"
    toml_file.write_text('host = "{{ENV_VAR:FUNLAB_NO_SUCH_VAR_XYZ}}"\n', encoding="utf-8")

    with pytest.raises(Exception, match="envfile"):
        Config(toml_file)


def test_config_file_not_found(tmp_path):
    """檔案不存在時拋 FileNotFoundError。"""
    from funlab.core.config import Config

    with pytest.raises(FileNotFoundError):
        Config(tmp_path / "no_such.toml")


def test_config_error_is_exported_from_utils(tmp_path):
    """ConfigError 可從 funlab.utils 直接 import。"""
    from funlab.utils import ConfigError
    from funlab.core.config import ConfigError as CE2
    assert ConfigError is CE2


# ---------------------------------------------------------------------------
# P1-A 修正驗證：get_logger() 標準 logging hierarchy 整合
# ---------------------------------------------------------------------------

def test_get_logger_returns_custom_logger():
    """get_logger 必須回傳 CustomLogger 實例，而非普通 Logger。"""
    from funlab.utils.log import get_logger, CustomLogger
    logger = get_logger("test.hierarchy.type")
    assert isinstance(logger, CustomLogger)


def test_get_logger_same_object_as_standard_getlogger():
    """logging.getLogger(name) 與 log.get_logger(name) 必須回傳同一物件。"""
    import logging
    from funlab.utils.log import get_logger
    name = "test.hierarchy.identity"
    logger_a = get_logger(name, level=logging.INFO)
    logger_b = logging.getLogger(name)
    assert logger_a is logger_b, (
        "get_logger() 應透過 logging.getLogger() 建立，使其在 logging hierarchy 中可被查找"
    )


def test_get_logger_default_level_is_info():
    """get_logger 預設 level 應為 INFO（已從舊版 ERROR 修正）。"""
    import logging
    from funlab.utils.log import get_logger
    logger = get_logger("test.hierarchy.level_default")
    assert logger.level == logging.INFO


def test_get_logger_propagate_false_when_has_handlers():
    """有真實 handler 時 propagate 應為 False，避免與 root handler 重複輸出。"""
    import logging
    from funlab.utils.log import get_logger, LogType
    logger = get_logger("test.hierarchy.propagate_false", logtype=LogType.STDOUT, level=logging.INFO)
    assert logger.propagate is False


def test_get_logger_propagate_true_when_off():
    """logtype=OFF 僅加 NullHandler，propagate 應保持 True。"""
    import logging
    from funlab.utils.log import get_logger, LogType
    logger = get_logger("test.hierarchy.propagate_off", logtype=LogType.OFF, level=logging.INFO)
    assert logger.propagate is True


def test_get_logger_upgrades_plain_logger_inplace():
    """若 logging.getLogger() 預先建立了普通 Logger，get_logger 應升級為 CustomLogger。"""
    import logging
    from funlab.utils.log import get_logger, CustomLogger
    name = "test.hierarchy.upgrade"
    plain = logging.getLogger(name)
    assert not isinstance(plain, CustomLogger)
    upgraded = get_logger(name, level=logging.INFO)
    assert isinstance(upgraded, CustomLogger)
    assert upgraded is plain


def test_get_logger_idempotent_same_object():
    """多次呼叫同名 get_logger 應回傳同一物件（更新其設定）。"""
    import logging
    from funlab.utils.log import get_logger
    name = "test.hierarchy.idempotent"
    a = get_logger(name, level=logging.DEBUG)
    b = get_logger(name, level=logging.WARNING)
    assert a is b
    assert b.level == logging.WARNING


def test_get_logger_has_progress_attrs():
    """get_logger 回傳的 CustomLogger 應具備 progress_states 等自訂屬性。"""
    from funlab.utils.log import get_logger
    logger = get_logger("test.hierarchy.attrs", max_progress=5)
    assert hasattr(logger, "_progress_states")
    assert hasattr(logger, "_max_progress")
    assert logger._max_progress == 5
