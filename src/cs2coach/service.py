"""加载 demo，按 Source 2 时间与身份语义构建可审计分析结果。"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pandas as pd
from demoparser2 import DemoParser

from cs2coach.features.analyze import AnalysisConfig, analyze_features, build_rounds, number, steamid
from cs2coach.parsing.loader import resolve_demo
from cs2coach.report.models import AnalysisReport

GAME_RULES = "CCSGameRulesProxy.CCSGameRules."
ROUND_STATUS = GAME_RULES + "m_iRoundWinStatus"
ROUND_REASON = GAME_RULES + "m_eRoundWinReason"


class AnalysisError(Exception):
    """可显示给 CLI 用户的 demo 或分析错误。"""


def _table(parser: DemoParser, event: str) -> pd.DataFrame:
    try:
        value = parser.parse_event(event)
    except Exception as exc:
        raise AnalysisError(f"解析事件 {event} 失败：{exc}") from exc
    if value is None or isinstance(value, list):
        return pd.DataFrame()
    if not isinstance(value, pd.DataFrame):
        return pd.DataFrame(value)
    return value


def _tick_rate(state: pd.DataFrame, fallback: float = 64.0) -> tuple[float, str]:
    if "game_time" not in state or "tick" not in state:
        return fallback, "fallback"
    ordered = state[["tick", "game_time"]].dropna().drop_duplicates("tick").sort_values("tick")
    dt = pd.to_numeric(ordered["game_time"], errors="coerce").diff()
    ticks = pd.to_numeric(ordered["tick"], errors="coerce").diff()
    rates = ticks[dt.gt(0)] / dt[dt.gt(0)]
    rates = rates[(rates >= 16) & (rates <= 256)]
    if rates.empty:
        return fallback, "fallback"
    return float(rates.median()), "estimated_from_game_time"


def _get_player(info: pd.DataFrame, target_id: int) -> tuple[str, list[int]]:
    if info.empty or "steamid" not in info:
        raise AnalysisError("demo 没有可用的玩家 SteamID 列；无法按 SteamID 安全选择玩家")
    ids = [x for x in info["steamid"].map(steamid).tolist() if x is not None]
    if target_id not in ids:
        available = ", ".join(str(x) for x in ids)
        raise AnalysisError(f"demo 中找不到玩家 SteamID64 {target_id}。可用 SteamID：{available or '无'}")
    row = info[info["steamid"].map(steamid) == target_id].iloc[0]
    name_col = next((c for c in ("name", "player_name", "user_name") if c in row.index), None)
    name = str(row[name_col]).strip() if name_col else str(target_id)
    return name, sorted(set(ids))


def _safe_value(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, (list, dict, tuple)):
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    if hasattr(value, "tolist"):
        try:
            return _safe_value(value.tolist())
        except (ValueError, AttributeError):
            pass
    if isinstance(value, (list, tuple)):
        return [_safe_value(x) for x in value]
    if isinstance(value, dict):
        return {str(k): _safe_value(v) for k, v in value.items()}
    return value


def _config(path: Path | None) -> AnalysisConfig:
    config_path = path or Path("config/default.toml")
    if not config_path.is_file():
        if path is not None:
            raise AnalysisError(f"配置文件不存在：{config_path}")
        return AnalysisConfig()
    try:
        with config_path.open("rb") as stream:
            raw = tomllib.load(stream)
        values = raw.get("analysis", {})
        config = AnalysisConfig(**values)
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
        raise AnalysisError(f"读取分析配置失败：{exc}") from exc
    if config.trade_window_seconds <= 0 or not 0 < config.stop_speed_ratio <= 1:
        raise AnalysisError("配置无效：trade_window_seconds 必须为正数，stop_speed_ratio 必须在 (0, 1] 范围内")
    if not 0 < config.crouch_amount_threshold <= 1 or config.min_pattern_samples < 1:
        raise AnalysisError("配置无效：crouch_amount_threshold 必须在 (0, 1] 范围内，min_pattern_samples 至少为 1")
    if config.pre_event_seconds < 0 or config.min_flash_duration_seconds <= 0:
        raise AnalysisError("配置无效：pre_event_seconds 不能为负数，min_flash_duration_seconds 必须为正数")
    return config


def analyze_demo(demo_path: Path, target_id: int, *, demo_member: str | None = None,
                 config_path: Path | None = None) -> dict[str, Any]:
    config = _config(config_path)
    resolved = resolve_demo(demo_path, member=demo_member)
    parser = DemoParser(str(resolved))
    try:
        header = parser.parse_header()
        player_info = parser.parse_player_info()
        listed_events = set(parser.list_game_events())
    except Exception as exc:
        raise AnalysisError(f"读取 demo 元数据失败：{exc}") from exc
    target_name, _ = _get_player(player_info, target_id)

    events = {name: _table(parser, name) for name in (
        "player_death", "player_hurt", "weapon_fire", "flashbang_detonate", "round_freeze_end",
        "round_end", "server_cvar",
    )}
    required_event_columns = {
        "player_death": {"tick", "user_steamid", "attacker_steamid"},
        "weapon_fire": {"tick", "user_steamid", "weapon"},
        "round_freeze_end": {"tick"},
    }
    for name, required in required_event_columns.items():
        actual = set(events[name].columns)
        if events[name].empty and name not in listed_events:
            raise AnalysisError(f"demo 缺少必要事件 {name}，无法按已验证规则分析")
        if actual and not required.issubset(actual):
            raise AnalysisError(f"事件 {name} 缺少必要字段：{', '.join(sorted(required - actual))}")
    for name, required in {
        "player_hurt": {"tick", "user_steamid", "attacker_steamid", "dmg_health"},
        "flashbang_detonate": {"tick", "user_steamid"},
    }.items():
        actual = set(events[name].columns)
        if not events[name].empty and not required.issubset(actual):
            raise AnalysisError(f"事件 {name} 缺少必要字段：{', '.join(sorted(required - actual))}")
    all_ticks = [int(value) for frame in events.values() if "tick" in frame
                 for value in pd.to_numeric(frame["tick"], errors="coerce").dropna().tolist()]
    if not all_ticks:
        raise AnalysisError("demo 中没有可用于建立比赛时间线的事件")
    max_tick = max(all_ticks)

    # 单次全时间轴读取比赛规则属性，按胜负状态 0→非 0 切分回合。
    round_state_ticks = list(range(max_tick + 65))
    try:
        round_state = parser.parse_ticks([ROUND_STATUS, ROUND_REASON, "total_rounds_played", "is_warmup_period", "game_time"],
                                         players=[target_id], ticks=round_state_ticks)
    except Exception as exc:
        raise AnalysisError(f"读取比赛规则 tick 状态失败：{exc}") from exc
    if not isinstance(round_state, pd.DataFrame) or round_state.empty:
        raise AnalysisError("未能读取目标玩家对应的比赛规则 tick 状态")
    if "tick" not in round_state:
        raise AnalysisError("比赛规则数据没有 tick 列")
    status_col = ROUND_STATUS if ROUND_STATUS in round_state else next((c for c in round_state if c.endswith("m_iRoundWinStatus")), None)
    if status_col is None:
        raise AnalysisError(f"解析器没有返回必要字段 {ROUND_STATUS}；拒绝生成不完整回合结果")
    if ROUND_STATUS not in round_state and status_col != ROUND_STATUS:
        round_state = round_state.rename(columns={status_col: ROUND_STATUS})
    tick_rate, tick_rate_source = _tick_rate(round_state)
    freeze_values = pd.to_numeric(events["round_freeze_end"].get("tick", pd.Series(dtype="float64")), errors="coerce").dropna()
    rounds = build_rounds(round_state, [int(x) for x in freeze_values])

    buytime_seconds = None
    cvars = events["server_cvar"]
    if {"name", "value"}.issubset(cvars.columns):
        for _, row in cvars.iterrows():
            if str(row["name"]).strip().lower() == "mp_buytime":
                buytime_seconds = number(row["value"])
                if buytime_seconds is not None and buytime_seconds > 0:
                    break
    buy_ticks = [int(tick) + round(buytime_seconds * tick_rate) for tick in freeze_values] if buytime_seconds else []

    # 只采样分析实际消费的 tick，并合成一次跨玩家状态查询。
    selected = set()
    for frame_name in ("player_death", "flashbang_detonate"):
        frame = events[frame_name]
        for value in pd.to_numeric(frame.get("tick", pd.Series(dtype="float64")), errors="coerce").dropna().tolist():
            tick = int(value)
            selected.update((tick - 1, tick))
    fires = events["weapon_fire"]
    if "user_steamid" in fires:
        target_fires = fires[fires["user_steamid"].map(steamid) == target_id]
        for value in pd.to_numeric(target_fires.get("tick", pd.Series(dtype="float64")), errors="coerce").dropna().tolist():
            tick = int(value)
            selected.update((tick - 1, tick))
    for value in freeze_values.tolist():
        selected.add(int(value))
    for tick in buy_ticks:
        selected.add(tick)
    sample_ticks = sorted(t for t in selected if 0 <= t <= max_tick + 64)
    tick_properties = ["X", "Y", "team_num", "is_alive", "is_airborne", "duck_amount", "is_scoped",
                       "flash_duration", "inventory", "current_equip_value", "balance", "start_balance",
                       "cash_spent_this_round", "round_start_equip_value", "has_helmet", "has_defuser", "armor_value",
                       "max_speed"]
    try:
        tick_data = parser.parse_ticks(tick_properties, ticks=sample_ticks)
    except Exception as exc:
        raise AnalysisError(f"读取事件附近的玩家状态失败：{exc}") from exc
    if not isinstance(tick_data, pd.DataFrame):
        tick_data = pd.DataFrame()
    if not tick_data.empty and not {"steamid", "tick"}.issubset(tick_data.columns):
        raise AnalysisError("事件附近的 tick 数据缺少 SteamID 或 tick 主键")

    # 仅将 parser 实际返回的列交给特征层；缺失列在 data_quality 中显式记录。
    expected = set(tick_properties)
    returned = set(tick_data.columns)
    missing = sorted(expected - returned)
    features = analyze_features(
        target_id=target_id, target_name=target_name, tick_rate=tick_rate, rounds=rounds,
        deaths=events["player_death"], hurts=events["player_hurt"], fires=events["weapon_fire"],
        flash_detonations=events["flashbang_detonate"], ticks=tick_data, config=config, buy_ticks=buy_ticks,
    )
    stat = resolved.stat()
    quality = {
        "listed_events": sorted(listed_events),
        "queried_event_counts": {k: len(v) for k, v in events.items()},
        "death_event_identity_coverage": float(events["player_death"]["user_steamid"].map(steamid).notna().mean()) if not events["player_death"].empty else 1.0,
        "queried_ticks": len(sample_ticks),
        "returned_tick_properties": sorted(expected & returned),
        "missing_tick_properties": missing,
        "target_tick_state_coverage": float(tick_data.loc[tick_data["steamid"].map(steamid) == target_id, "tick"].nunique() / max(len(sample_ticks), 1)) if not tick_data.empty else 0.0,
        "mp_buytime_seconds": buytime_seconds,
        "round_count": len(rounds),
        "rounds_without_end_state": sum(x["end_tick"] is None for x in rounds),
        "limitations": [],
    }
    if "X" in missing or "Y" in missing:
        quality["limitations"].append("缺少坐标 tick 字段，急停速度无法计算。")
    if "max_speed" in missing:
        quality["limitations"].append("解析器未返回 max_speed；无法按武器当前最大速度计算移动比例或急停候选。")
    if "is_airborne" in missing or "duck_amount" in missing:
        quality["limitations"].append("缺少腾空/下蹲状态；移动开火样本保留原始速度，但不生成站立地面急停候选。")
    if "flash_duration" in missing:
        quality["limitations"].append("缺少 flash_duration；无法重建目标玩家受闪状态。")
    if quality["death_event_identity_coverage"] < 1:
        quality["limitations"].append("部分死亡事件缺少可解析 SteamID；相应击杀、死亡或补枪统计可能不完整。")
    if features["metrics"]["unclassified_attacker_events"]:
        quality["limitations"].append("部分攻击者事件缺少阵营状态，已从确认击杀数中分离。")
    if quality["target_tick_state_coverage"] < 0.8:
        quality["limitations"].append("目标玩家在事件相关 tick 上的状态覆盖率低于 80%；移动、经济和闪光类结论不完整。")
    if buytime_seconds is None:
        quality["limitations"].append("demo 未提供有效 mp_buytime；未推算购买窗口经济快照。")
    if quality["rounds_without_end_state"]:
        quality["limitations"].append("存在未配对回合结束状态；相关事件仍保留 tick，但回合号可能为空。")
    quality["limitations"].extend([
        "tick 位置差分是区间平均速度，不代表 sub-tick 开火瞬时速度。",
        "spotted / 解析字段不能证明玩家屏幕上的真实视线或遮挡情况。",
        "移动开火提示是候选复核项，不等同于确定的枪法错误。",
    ])
    report = {
        "schema_version": "1.0",
        "demo": {"file": Path(demo_path).name, "resolved_file": resolved.name,
                 "file_size_bytes": stat.st_size, "modified_time_ns": stat.st_mtime_ns,
                 "header": _safe_value(header)},
        "timebase": {"tick_rate": round(tick_rate, 4), "source": tick_rate_source,
                     "tick_seconds": round(1 / tick_rate, 8)},
        **features,
        "data_quality": quality,
    }
    return AnalysisReport.model_validate(_safe_value(report)).model_dump(mode="json")
