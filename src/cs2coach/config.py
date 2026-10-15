"""阈值配置：读取 config/default.toml，再用用户配置文件逐项覆盖。"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "default.toml"


class ConfigError(Exception):
    pass


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg = _read(DEFAULT_CONFIG)
    if path is not None:
        _merge(cfg, _read(Path(path)))
    _validate_checker(cfg["checker"])
    return cfg


def _validate_checker(cfg: dict[str, Any]) -> None:
    """敏感说法允许配置扩展，但谓词名称和正则必须能被检查器执行。"""
    from cs2coach.tools.checker import PREDICATES

    phrases = cfg["phrases"]
    if not isinstance(phrases, list):
        raise ConfigError("配置项 checker.phrases 应为表数组")
    for i, phrase in enumerate(phrases):
        name = f"checker.phrases[{i}]"
        if not isinstance(phrase, dict) or set(phrase) != {"pattern", "require"}:
            raise ConfigError(f"{name} 必须且只能包含 pattern、require")
        if not isinstance(phrase["require"], str) or phrase["require"] not in PREDICATES:
            raise ConfigError(f"{name}.require 是未知谓词：{phrase['require']!r}")
        if not isinstance(phrase["pattern"], str) or not phrase["pattern"]:
            raise ConfigError(f"{name}.pattern 应为非空正则字符串")
        try:
            re.compile(phrase["pattern"])
        except re.error as exc:
            raise ConfigError(f"{name}.pattern 正则无效：{exc}") from None


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"配置文件格式错误 {path}: {e}") from e


def _merge(base: dict[str, Any], override: dict[str, Any], prefix: str = "") -> None:
    """把 override 合并进 base。只允许覆盖已有的键，拼错的键直接报错，避免静默不生效。"""
    for key, value in override.items():
        name = prefix + key
        if key not in base:
            raise ConfigError(f"未知的配置项: {name}")
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"配置项 {name} 应为表（table）")
            _merge(base[key], value, name + ".")
        else:
            base[key] = value
