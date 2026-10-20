"""阈值配置：读取 config/default.toml（诊断程序）和 config/research.toml（研究线），再用用户配置文件逐项覆盖。"""

from __future__ import annotations

import math
import re
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "default.toml"
RESEARCH_CONFIG = DEFAULT_CONFIG.with_name("research.toml")


class ConfigError(Exception):
    pass


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg = _read(DEFAULT_CONFIG)
    research = _read(RESEARCH_CONFIG)
    duplicated = sorted(cfg.keys() & research.keys())
    if duplicated:
        raise ConfigError(f"{RESEARCH_CONFIG.name} 与 {DEFAULT_CONFIG.name} 有重复的表: {', '.join(duplicated)}")
    cfg.update(research)
    if path is not None:
        _merge(cfg, _read(Path(path)))
    _validate_checker(cfg["checker"])
    _validate_training(cfg["training"])
    _validate_visibility(cfg["visibility"])
    _validate_report_card(cfg["report"]["card"])
    _validate_audit(cfg["audit"])
    _validate_review(cfg["review"])
    _validate_problems(cfg["problems"])
    _validate_economy(cfg["economy"])
    _validate_radar(cfg["radar"])
    _validate_ladder(cfg["ladder"])
    _validate_panel(cfg["panel"])
    if type(cfg["baseline"]["min_metric_samples"]) is not int or cfg["baseline"]["min_metric_samples"] < 1:
        raise ConfigError("配置项 baseline.min_metric_samples 应为正整数")
    _validate_tools(cfg["tools"])
    return cfg


def _validate_ladder(cfg: dict[str, Any]) -> None:
    for key in ("min_player_matches", "min_players"):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise ConfigError(f"配置项 ladder.{key} 应为正整数")


def _validate_tools(cfg: dict[str, Any]) -> None:
    if type(cfg["jobs"]) is not int or cfg["jobs"] < 1:
        raise ConfigError("配置项 tools.jobs 应为正整数")


def _validate_panel(cfg: dict[str, Any]) -> None:
    from cs2coach.features.panel import PANEL_METRICS
    from cs2coach.features.problems import PROBLEMS

    for key in ("sniper_min_kills", "flash_match_ticks"):
        if type(cfg[key]) is not int or cfg[key] < 0:
            raise ConfigError(f"配置项 panel.{key} 应为非负整数")
    if type(cfg["small_sample_below"]) is not int or cfg["small_sample_below"] < 1:
        raise ConfigError("配置项 panel.small_sample_below 应为正整数")
    if cfg["sniper_min_kills"] < 1:
        raise ConfigError("配置项 panel.sniper_min_kills 应为正整数")
    if (not isinstance(cfg["sniper_weapons"], list) or not cfg["sniper_weapons"]
            or any(x not in {"awp", "ssg08"} for x in cfg["sniper_weapons"])
            or len(set(cfg["sniper_weapons"])) != len(cfg["sniper_weapons"])):
        raise ConfigError("配置项 panel.sniper_weapons 应为不重复的狙击枪名称")
    keys = {metric.min_samples for metric in PANEL_METRICS}
    samples = cfg["min_samples"]
    if set(samples) != keys or any(type(x) is not int or x < 1 for x in samples.values()):
        raise ConfigError("配置项 panel.min_samples 应包含所有指标的正整数门槛")
    categories = cfg["categories"]
    expected = list(dict.fromkeys(metric.category for metric in PANEL_METRICS))
    if (not isinstance(categories, list) or [x.get("id") for x in categories] != expected
            or any(not isinstance(x.get("name"), str) or not x["name"]
                   or not isinstance(x.get("problems"), list) or not isinstance(x.get("highlights"), list)
                   or any(pid not in PROBLEMS for pid in x["problems"])
                   or any(hid not in {"multikill", "clutch", "flash_assist", "good_trade", "won_duel_seen_first"}
                          for hid in x["highlights"]) for x in categories)):
        raise ConfigError("配置项 panel.categories 顺序或内容无效")


def _validate_radar(cfg: dict[str, Any]) -> None:
    from cs2coach.features.radar import RADAR_METRICS

    if not isinstance(cfg["default_tier"], str) or not cfg["default_tier"]:
        raise ConfigError("配置项 radar.default_tier 应为非空字符串")
    if cfg["best_player_rule"] != "kills_minus_deaths":
        raise ConfigError("配置项 radar.best_player_rule 仅支持 kills_minus_deaths")
    for key in ("max_strengths", "max_problems", "max_examples", "max_highlights"):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise ConfigError(f"配置项 radar.{key} 应为正整数")
    for key in ("strength_min_percentile", "problem_min_excess"):
        if type(cfg[key]) not in (int, float) or not math.isfinite(cfg[key]):
            raise ConfigError(f"配置项 radar.{key} 应为有限数")
    if not 0 <= cfg["strength_min_percentile"] <= 100 or cfg["problem_min_excess"] < 0:
        raise ConfigError("radar 百分位应在 [0, 100] 内，多出次数应非负")
    for key in ("rounds", "deaths", "flashes", "trade_chances", "tier_values"):
        if type(cfg["min_samples"][key]) is not int or cfg["min_samples"][key] < 1:
            raise ConfigError(f"配置项 radar.min_samples.{key} 应为正整数")
    if type(cfg["verdict_weak_percentile"]) not in (int, float) or not 0 < cfg["verdict_weak_percentile"] <= 100:
        raise ConfigError("配置项 radar.verdict_weak_percentile 应在 (0, 100] 内")
    if type(cfg["verdict_min_axes"]) is not int or not 1 <= cfg["verdict_min_axes"] <= 6:
        raise ConfigError("配置项 radar.verdict_min_axes 应为 [1, 6] 内的整数")
    axes = cfg["axes"]
    if (not isinstance(axes, list) or not 3 <= len(axes) <= 8 or any(
            not isinstance(axis, dict) or set(axis) != {"id", "name", "metrics"}
            or any(not isinstance(axis[key], str) or not axis[key] for key in ("id", "name"))
            or not isinstance(axis["metrics"], list) or not axis["metrics"]
            or any(not isinstance(metric, str) or metric not in RADAR_METRICS for metric in axis["metrics"])
            or len(set(axis["metrics"])) != len(axis["metrics"]) for axis in axes)
            or len({axis["id"] for axis in axes}) != len(axes)):
        raise ConfigError("配置项 radar.axes 应为 3–8 个编号唯一且指标有效的维度")


def _validate_review(cfg: dict[str, Any]) -> None:
    from string import Formatter

    for key in ("csgo_dir", "demo_subdir", "playdemo_template", "jump_template", "follow_template"):
        if not isinstance(cfg[key], str):
            raise ConfigError(f"配置项 review.{key} 应为字符串")
    subdir = Path(cfg["demo_subdir"])
    if (not cfg["demo_subdir"] or subdir.is_absolute() or
            any(part in (".", "..") for part in subdir.parts) or ":" in cfg["demo_subdir"] or "\\" in cfg["demo_subdir"]):
        raise ConfigError("配置项 review.demo_subdir 应为 game/csgo 下的相对子目录")
    fields = {"playdemo_template": {"subdir", "demo_stem"},
              "jump_template": {"tick"}, "follow_template": {"steamid"}}
    for key, allowed in fields.items():
        value = cfg[key]
        if key != "follow_template" and not value:
            raise ConfigError(f"配置项 review.{key} 不能为空")
        try:
            used = {field for _, field, _, _ in Formatter().parse(value) if field is not None}
        except ValueError as exc:
            raise ConfigError(f"配置项 review.{key} 模板无效") from exc
        if not used <= allowed or (key != "follow_template" and not used):
            raise ConfigError(f"配置项 review.{key} 占位符无效")
    questions = cfg["questions"]
    if (not isinstance(questions, dict) or not questions.get("generic") or
            any(not isinstance(value, str) or not value.strip() for value in questions.values())):
        raise ConfigError("配置项 review.questions 应包含非空的 generic 问题")


def _validate_problems(cfg: dict[str, Any]) -> None:
    from cs2coach.features.problems import PROBLEMS

    conditions = {"through_smoke", "first_seen_through_smoke", "wallbang", "player_blinded_in_fight",
                  "opponent_blinded_by_own_side", "killed_by_utility", "fired_before_seen", "airborne_near_seen",
                  "turned_into_view"}
    exclusions = cfg["exclusions"]
    if (not isinstance(exclusions, dict) or set(exclusions) != conditions or any(
            not isinstance(ids, list) or
            any(not isinstance(pid, str) or pid not in PROBLEMS for pid in ids) or
            len(ids) != len(set(ids))
            for ids in exclusions.values())):
        raise ConfigError("配置项 problems.exclusions 的条件名或问题编号无效")
    if type(cfg["max_shots_before_seen"]) is not int or cfg["max_shots_before_seen"] < 1:
        raise ConfigError("配置项 problems.max_shots_before_seen 应为正整数")
    for key in ("crosshair_turn_lookback_ticks", "crosshair_turn_min_ticks"):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise ConfigError(f"配置项 problems.{key} 应为正整数")
    if type(cfg["reload_min_ammo_left"]) is not int or cfg["reload_min_ammo_left"] < 1:
        raise ConfigError("配置项 problems.reload_min_ammo_left 应为正整数")
    fraction = cfg["reload_min_ammo_fraction"]
    if type(fraction) not in (int, float) or not math.isfinite(fraction) or not 0 < fraction <= 1:
        raise ConfigError("配置项 problems.reload_min_ammo_fraction 应在 (0, 1] 内")
    thresholds = cfg["moving_shot_thresholds"]
    weapons = {"ak47", "galilar", "m4a1", "m4a1_silencer", "famas", "aug", "sg556", "deagle"}
    if (not isinstance(thresholds, dict) or set(thresholds) != weapons or any(
            type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1
            for value in thresholds.values())):
        raise ConfigError("配置项 problems.moving_shot_thresholds 应包含八种武器的 (0, 1] 速度比")
    distance = cfg["spray_max_distance_m"]
    if type(distance) not in (int, float) or not math.isfinite(distance) or distance <= 0:
        raise ConfigError("配置项 problems.spray_max_distance_m 应为正有限数")
    for key in ("slow_reaction_margin_ratio", "crosshair_margin_ratio", "crosshair_min_distance_m",
                "overpeek_t_min_round_time_s", "highlight_min_lead_s", "crosshair_airborne_lookback_seconds",
                "missed_trade_min_view_seconds", "missed_trade_min_react_seconds", "missed_trade_other_fight_seconds",
                "post_plant_min_alive_seconds", "reload_min_seconds", "no_shot_min_seen_seconds",
                "no_shot_recent_seconds"):
        value = cfg[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ConfigError(f"配置项 problems.{key} 应为非负有限数")
    from cs2coach.parsing.weapons import GRENADES, GUNS
    for key, allowed in (("dry_peek_grenades", GRENADES), ("glock_long_range_opponent_weapons", GUNS)):
        value = cfg[key]
        if not isinstance(value, list) or not value or any(not isinstance(x, str) or x not in allowed for x in value):
            raise ConfigError(f"配置项 problems.{key} 应为非空的武器名列表")
    if type(cfg["missed_trade_exclude_traded_by_other"]) is not bool:
        raise ConfigError("配置项 problems.missed_trade_exclude_traded_by_other 应为布尔值")


def _validate_economy(cfg: dict[str, Any]) -> None:
    from cs2coach.parsing.weapons import GRENADES

    prices = cfg["grenade_prices"]
    if (not isinstance(prices, dict) or set(prices) != GRENADES
            or any(type(value) is not int or value <= 0 for value in prices.values())):
        raise ConfigError("配置项 economy.grenade_prices 应包含六种道具的正整数价格")


def _validate_audit(cfg: dict[str, Any]) -> None:
    from cs2coach.features.problems import DETECTED_PROBLEMS

    known = set(DETECTED_PROBLEMS)
    ratio = cfg["borderline_ratio"]
    if type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0 < ratio < 1:
        raise ConfigError("配置项 audit.borderline_ratio 应在 (0, 1) 内")
    visibility = cfg["visibility_problems"]
    if not isinstance(visibility, list) or not visibility or len(set(visibility)) != len(visibility) or any(p not in known for p in visibility):
        raise ConfigError("配置项 audit.visibility_problems 应为不重复的已实现问题编号")
    notes = cfg["incomplete_visibility_notes"]
    if (not isinstance(notes, list) or not notes or len(set(notes)) != len(notes)
            or any(not isinstance(note, str) or not note for note in notes)):
        raise ConfigError("配置项 audit.incomplete_visibility_notes 应为不重复的非空说明")
    conditions = {"shots_before_seen_positive", "negative_seen_to_shot", "blind_at_seen",
                  "visibility_note_set", "contact_speed_borderline", "stop_borderline",
                  "spray_mostly_unseen", "crosshair_extreme", "through_smoke"}
    rules = cfg["rules"]
    if not isinstance(rules, list) or len(rules) != len(conditions) or {r.get("condition") for r in rules if isinstance(r, dict)} != conditions:
        raise ConfigError("配置项 audit.rules 应包含九种不同条件")
    ids = set()
    for rule in rules:
        if (not isinstance(rule.get("id"), str) or not rule["id"] or rule["id"] in ids
                or not isinstance(rule.get("description"), str) or not rule["description"]
                or not isinstance(rule.get("problems"), list) or not rule["problems"]
                or any(p not in known for p in rule["problems"])):
            raise ConfigError("配置项 audit.rules 的编号、说明或问题清单无效")
        ids.add(rule["id"])
        if rule["condition"] in {"blind_at_seen", "through_smoke"} and rule["problems"] != visibility:
            raise ConfigError("视野审计规则的问题清单必须等于 audit.visibility_problems")
        if rule["condition"] == "visibility_note_set" and rule["problems"] != [p for p in visibility if p != "died_without_seeing"]:
            raise ConfigError("visibility_note_set 不适用于 died_without_seeing")
        if rule["condition"] == "crosshair_extreme" and (type(rule.get("angle_deg")) not in (int, float)
                or not math.isfinite(rule["angle_deg"]) or rule["angle_deg"] <= 0):
            raise ConfigError("配置项 audit.rules.crosshair_extreme.angle_deg 应为正数")


def _validate_report_card(card: dict[str, Any]) -> None:
    from cs2coach.features.problems import PROBLEMS

    cutoffs = card["grade_cutoffs"]
    if (not isinstance(cutoffs, list) or len(cutoffs) != 4 or
            any(type(x) not in (int, float) or not math.isfinite(x) for x in cutoffs) or
            not 0 < cutoffs[0] < cutoffs[1] < cutoffs[2] < cutoffs[3] < 100):
        raise ConfigError("配置项 report.card.grade_cutoffs 应为四个递增的百分位")
    for key in ("min_ranked", "mark_top"):
        if type(card[key]) is not int or card[key] < 1:
            raise ConfigError(f"配置项 report.card.{key} 应为正整数")
    limit = card["highlight_show_ttd_max_ms"]
    if type(limit) not in (int, float) or not math.isfinite(limit) or limit < 0:
        raise ConfigError("配置项 report.card.highlight_show_ttd_max_ms 应为非负有限数")
    priorities = card["highlight_priority"]
    if (not isinstance(priorities, list) or len(set(priorities)) != len(priorities) or
            any(pid not in PROBLEMS or PROBLEMS[pid].category != "highlight" for pid in priorities)):
        raise ConfigError("配置项 report.card.highlight_priority 应为唯一的高光问题编号数组")
    note = card["note"]
    if not isinstance(note, str) or not note or "{trade_window}" not in note or note.count("{") != 1 or note.count("}") != 1:
        raise ConfigError("配置项 report.card.note 只允许包含 {trade_window} 占位符")
    dimensions = card["dimensions"]
    if not isinstance(dimensions, list) or not dimensions or len({d.get("key") for d in dimensions if isinstance(d, dict)}) != len(dimensions):
        raise ConfigError("配置项 report.card.dimensions 应为编号唯一的表数组")
    allowed_fields = {"utility_damage", "aim.headshot_accuracy_pct", "aim.counter_strafing_pct",
                      "aim.crosshair_placement_deg", "aim.crosshair_offset_m", "aim.time_to_damage_ms", "aim.spotted_accuracy_pct",
                      "aim.spray_accuracy_pct"}
    for i, d in enumerate(dimensions):
        if (not isinstance(d, dict) or set(d) != {"key", "label", "field", "direction", "problem"} or
                any(not isinstance(d[k], str) or not d[k] for k in ("key", "label")) or
                d["field"] not in allowed_fields or d["direction"] not in ("higher", "lower") or
                d["problem"] and d["problem"] not in PROBLEMS):
            raise ConfigError(f"配置项 report.card.dimensions[{i}] 无效")


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
    from cs2coach.tools.checker import (DISTANCE_PREDICATES, METRIC_MISMATCH_PREDICATES, PHRASE_SCOPES,
                                        PREDICATES, SEGMENT_PREDICATES, range_phrase_pattern)
    if type(cfg.get("finding_candidates")) is not int or cfg["finding_candidates"] < 1:
        raise ConfigError("配置项 checker.finding_candidates 应为正整数")
    for key in ("teammate_far_distance", "teammate_near_distance", "close_range_m", "long_range_m"):
        if type(cfg.get(key)) not in (int, float) or cfg[key] <= 0:
            raise ConfigError(f"配置项 checker.{key} 应为正数")
    if (type(cfg.get("segment_major_ratio")) not in (int, float)
            or not 0 < cfg["segment_major_ratio"] <= 1):
        raise ConfigError("配置项 checker.segment_major_ratio 应在 (0, 1] 内")
    words = cfg["range_weapon_words"]
    if (not isinstance(words, list) or not words or
            any(not isinstance(word, str) or not word.strip() or word != word.strip() for word in words) or
            len(set(words)) != len(words)):
        raise ConfigError("配置项 checker.range_weapon_words 应为不重复的非空武器名数组")
    aliases = cfg["map_aliases"]
    if (not isinstance(aliases, dict) or any(not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not name for name in names)
            for names in aliases.values())):
        raise ConfigError("配置项 checker.map_aliases 应为地图别名数组表")

    labels = cfg["quantile_labels"]
    if not isinstance(labels, list) or any(type(value) is not int or value < 0 for value in labels):
        raise ConfigError("配置项 checker.quantile_labels 应为非负整数数组")
    for key in ("training_forbidden_patterns", "overview_crosshair_forbidden_patterns"):
        patterns = cfg.get(key)
        if not isinstance(patterns, list) or not patterns or any(not isinstance(p, str) or not p for p in patterns):
            raise ConfigError(f"配置项 checker.{key} 应为非空正则字符串数组")
        try:
            for pattern in patterns:
                re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"配置项 checker.{key} 正则无效：{exc}") from None
    phrases = cfg["phrases"]
    if not isinstance(phrases, list):
        raise ConfigError("配置项 checker.phrases 应为表数组")
    for i, phrase in enumerate(phrases):
        name = f"checker.phrases[{i}]"
        if (not isinstance(phrase, dict) or not {"pattern", "require"} <= set(phrase)
                or not set(phrase) <= {"pattern", "require", "scope"}):
            raise ConfigError(f"{name} 必须包含 pattern、require，可选 scope")
        if (not isinstance(phrase["require"], str)
                or phrase["require"] not in PREDICATES.keys() | DISTANCE_PREDICATES | SEGMENT_PREDICATES | METRIC_MISMATCH_PREDICATES):
            raise ConfigError(f"{name}.require 是未知谓词：{phrase['require']!r}")
        if phrase.get("scope", "any") not in PHRASE_SCOPES:
            raise ConfigError(f"{name}.scope 应为 any 或 all")
        if phrase.get("scope") == "all" and phrase["require"] in DISTANCE_PREDICATES:
            raise ConfigError(f"{name}.scope 不能用于距离谓词")
        if phrase["require"] in SEGMENT_PREDICATES and phrase.get("scope", "any") != "all":
            raise ConfigError(f"{name}.scope 分段主次只能用于 all")
        if not isinstance(phrase["pattern"], str) or not phrase["pattern"]:
            raise ConfigError(f"{name}.pattern 应为非空正则字符串")
        try:
            re.compile(range_phrase_pattern(phrase, cfg))
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
