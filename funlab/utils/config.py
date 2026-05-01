"""T-libs-002：統一設定檔載入工具。

提供 :func:`load_config` 做為整個系統的統一設定入口：
- 自動偵測 TOML / YAML 格式
- 以 Pydantic ``BaseModel`` schema 驗證欄位
- 支援 ``${VAR}`` 環境變數覆蓋

Examples::

    from funlab.utils.config import load_config
    from pydantic import BaseModel

    class AppConfig(BaseModel):
        host: str = "localhost"
        port: int = 8080

    cfg = load_config(Path("config.toml"), AppConfig)
    # cfg.host, cfg.port 已通過驗證
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TypeVar

try:
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover
    raise ImportError("funlab.utils.config 需要 pydantic；請執行 `pip install pydantic`。") from exc

__all__ = ["load_config", "ConfigError"]

T = TypeVar("T", bound=BaseModel)

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


class ConfigError(ValueError):
    """設定檔載入或驗證失敗時丟出。"""
    pass


def _expand_env_vars(value: object) -> object:
    """遞迴展開 dict/list/str 中的 ``${VAR}`` 環境變數參照。"""
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            var = m.group(1)
            if var not in os.environ:
                raise ConfigError(f"設定中參照的環境變數 '${{{var}}}' 未設定。")
            return os.environ[var]
        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


def _load_raw(path: Path) -> dict:
    """依副檔名讀取設定檔，回傳原始 dict。"""
    suffix = path.suffix.lower()
    if suffix in (".toml",):
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError("讀取 YAML 設定需要 PyYAML；請執行 `pip install pyyaml`。") from exc
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    raise ConfigError(f"不支援的設定檔格式：'{path.suffix}'（支援 .toml / .yaml / .yml）。")


def load_config(
    path: Path | str,
    schema: type[T],
    *,
    env_override: bool = True,
) -> T:
    """載入 TOML/YAML 設定檔並以 Pydantic schema 驗證。

    Args:
        path:         設定檔路徑（.toml / .yaml / .yml）。
        schema:       Pydantic ``BaseModel`` 子類別，定義欄位與驗證規則。
        env_override: 若為 ``True``（預設），值中的 ``${VAR}`` 會替換為環境變數。

    Returns:
        通過驗證的 ``schema`` 實例。

    Raises:
        ConfigError:       檔案不存在、格式不支援、缺少必填欄位、env 變數未設定。
        pydantic.ValidationError: schema 驗證失敗（欄位型別錯誤等）。
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"設定檔不存在：'{path}'。")

    try:
        raw = _load_raw(path)
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"無法解析設定檔 '{path}'：{exc}") from exc

    if env_override:
        raw = _expand_env_vars(raw)  # type: ignore[assignment]

    try:
        return schema(**raw)
    except Exception as exc:
        raise ConfigError(f"設定檔 '{path}' 驗證失敗：{exc}") from exc
