"""funlab.utils — 通用工具模組。

公開 API（供外部直接 import）：
	- :mod:`funlab.utils.log`：get_logger、setup_logging、LogConfig
	- :mod:`funlab.utils.config`：load_config、ConfigError
"""
from funlab.utils import log
from funlab.utils.config import load_config, ConfigError
from funlab.utils.log import get_logger, setup_logging, LogConfig

__all__ = [
	"log",
	"get_logger",
	"setup_logging",
	"LogConfig",
	"load_config",
	"ConfigError",
]
