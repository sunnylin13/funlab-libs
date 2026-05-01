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
# T-libs-002：load_config / ConfigError
# ---------------------------------------------------------------------------

def test_load_config_toml(tmp_path):
    """從 TOML 檔載入並以 Pydantic schema 驗證。"""
    from pydantic import BaseModel
    from funlab.utils.config import load_config

    toml_file = tmp_path / "app.toml"
    toml_file.write_text('[app]\nhost = "localhost"\nport = 9090\n', encoding="utf-8")

    class AppSection(BaseModel):
        host: str
        port: int

    class Root(BaseModel):
        app: AppSection

    cfg = load_config(toml_file, Root)
    assert cfg.app.host == "localhost"
    assert cfg.app.port == 9090


def test_load_config_env_override(tmp_path, monkeypatch):
    """${VAR} 替換為環境變數的值。"""
    from pydantic import BaseModel
    from funlab.utils.config import load_config

    monkeypatch.setenv("TEST_HOST", "envhost")
    toml_file = tmp_path / "cfg.toml"
    toml_file.write_text('host = "${TEST_HOST}"\n', encoding="utf-8")

    class Cfg(BaseModel):
        host: str

    cfg = load_config(toml_file, Cfg)
    assert cfg.host == "envhost"


def test_load_config_missing_env_var(tmp_path):
    """${VAR} 參照未設定的環境變數時拋 ConfigError。"""
    from pydantic import BaseModel
    from funlab.utils.config import load_config, ConfigError

    toml_file = tmp_path / "cfg.toml"
    toml_file.write_text('host = "${FUNLAB_NO_SUCH_VAR_XYZ}"\n', encoding="utf-8")

    class Cfg(BaseModel):
        host: str

    # 確保環境變數未設定
    os.environ.pop("FUNLAB_NO_SUCH_VAR_XYZ", None)
    with pytest.raises(ConfigError, match="環境變數"):
        load_config(toml_file, Cfg)


def test_load_config_file_not_found(tmp_path):
    """檔案不存在時拋 ConfigError。"""
    from pydantic import BaseModel
    from funlab.utils.config import load_config, ConfigError

    class Cfg(BaseModel):
        x: int = 0

    with pytest.raises(ConfigError, match="不存在"):
        load_config(tmp_path / "no_such.toml", Cfg)


def test_load_config_unsupported_format(tmp_path):
    """不支援的副檔名時拋 ConfigError。"""
    from pydantic import BaseModel
    from funlab.utils.config import load_config, ConfigError

    bad_file = tmp_path / "cfg.ini"
    bad_file.write_text("[section]\nkey=value\n")

    class Cfg(BaseModel):
        pass

    with pytest.raises(ConfigError, match="不支援"):
        load_config(bad_file, Cfg)
