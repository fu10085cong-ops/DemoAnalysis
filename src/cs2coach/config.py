"""阈值配置：读取 config/default.toml，再用用户配置文件逐项覆盖。"""

from __future__ import annotations

import math
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
    _validate_training(cfg["training"])
    _validate_visibility(cfg["visibility"])
    return cfg


def _validate_visibility(cfg: dict[str, Any]) -> None:
    """身体采样点和烟雾胸口高度必须是有限的合法数值。"""
    def number(value: object) -> bool:
        return type(value) in (int, float) and math.isfinite(value)

    points = cfg["body_points"]
    if (not isinstance(points, list) or not points or any(
            not isinstance(point, list) or len(point) != 3 or
            not all(number(value) for value in point) or point[0] < 0 or point[1] < 0
            for point in points)):
        raise ConfigError("配置项 visibility.body_points 应为非空的 [站立高度, 下蹲高度, 横向偏移] 数组，高度不得为负")
    chest = cfg["chest_height"]
    if (not isinstance(chest, list) or len(chest) != 2 or
            not all(number(value) and value >= 0 for value in chest)):
        raise ConfigError("配置项 visibility.chest_height 应为两个非负高度")


def _validate_training(cfg: dict[str, Any]) -> None:
    ids = cfg["extra_pro_steamids"]
    if not isinstance(ids, list) or any(type(sid) is not int or sid <= 0 for sid in ids):
        raise ConfigError("配置项 training.extra_pro_steamids 应为正整数数组")
    from cs2coach.features.problems import PROBLEMS
    latin = cfg["allowed_latin"]
    if not isinstance(latin, list) or any(not isinstance(v, str) or not v for v in latin):
        raise ConfigError("配置项 training.allowed_latin 应为非空字符串数组")
    drills = cfg["drills"]
    if not isinstance(drills, list) or len({d.get("id") for d in drills if isinstance(d, dict)}) != len(drills):
        raise ConfigError("配置项 training.drills 应为编号唯一的表数组")
    for i, drill in enumerate(drills):
        if (not isinstance(drill, dict) or not {"id", "label", "problems"} <= drill.keys()
                or set(drill) - {"id", "label", "problems", "min_aim_problems"}
                or any(not isinstance(drill[k], str) or not drill[k] for k in ("id", "label"))
                or not isinstance(drill["problems"], list)
                or any(p not in PROBLEMS for p in drill["problems"])
                or ("min_aim_problems" in drill and
                    (type(drill["min_aim_problems"]) is not int or drill["min_aim_problems"] < 2))):
            raise ConfigError(f"配置项 training.drills[{i}] 无效")


def _validate_checker(cfg: dict[str, Any]) -> None:
    """敏感说法允许配置扩展，但谓词名称和正则必须能被检查器执行。"""
    from cs2coach.tools.checker import DISTANCE_PREDICATES, PHRASE_SCOPES, PREDICATES
    for key in ("teammate_far_distance", "teammate_near_distance"):
        if type(cfg.get(key)) not in (int, float) or cfg[key] <= 0:
            raise ConfigError(f"配置项 checker.{key} 应为正数")
    aliases = cfg["map_aliases"]
    if (not isinstance(aliases, dict) or any(not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not name for name in names)
            for names in aliases.values())):
        raise ConfigError("配置项 checker.map_aliases 应为地图别名数组表")

    labels = cfg["quantile_labels"]
    if not isinstance(labels, list) or any(type(value) is not int or value < 0 for value in labels):
        raise ConfigError("配置项 checker.quantile_labels 应为非负整数数组")
    phrases = cfg["phrases"]
    if not isinstance(phrases, list):
        raise ConfigError("配置项 checker.phrases 应为表数组")
    for i, phrase in enumerate(phrases):
        name = f"checker.phrases[{i}]"
        if (not isinstance(phrase, dict) or not {"pattern", "require"} <= set(phrase)
                or not set(phrase) <= {"pattern", "require", "scope"}):
            raise ConfigError(f"{name} 必须包含 pattern、require，可选 scope")
        if (not isinstance(phrase["require"], str)
                or phrase["require"] not in PREDICATES.keys() | DISTANCE_PREDICATES):
            raise ConfigError(f"{name}.require 是未知谓词：{phrase['require']!r}")
        if phrase.get("scope", "any") not in PHRASE_SCOPES:
            raise ConfigError(f"{name}.scope 应为 any 或 all")
        if phrase.get("scope") == "all" and phrase["require"] in DISTANCE_PREDICATES:
            raise ConfigError(f"{name}.scope 不能用于距离谓词")
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
