"""探索脚本：用 demoparser2 实际解析一个 demo，输出可用的事件 / 字段信息。

用法:
    python scripts/explore_demo.py [demo路径] [--out data/output/explore]

不传路径时取 data/demos/ 下第一个 demo。结果：
    - 控制台打印摘要
    - <out>/<demo名>.json 保存完整结果（事件字段、tick 字段探测、原始 updated fields 等）

注意：demoparser2 的 parse_ticks 对不存在的字段名不会报错而是静默丢弃，
所以这里逐个探测候选字段，以"返回的列里是否真有它、是否有非空值"为准。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from demoparser2 import DemoParser

from cs2coach.console import ensure_utf8_stdio
from cs2coach.parsing.loader import resolve_demo

DEMO_SUFFIXES = (".dem", ".dem.bz2", ".dem.gz", ".zip", ".rar")

# 候选 tick 级字段，按检测项分组。名字来自对 demoparser2 的猜测，必须以探测结果为准。
CANDIDATE_TICK_PROPS: dict[str, list[str]] = {
    "身份/状态": [
        "name", "team_num", "team_name", "health", "is_alive", "armor_value",
        "life_state", "move_type", "last_place_name", "in_buy_zone",
    ],
    "坐标": ["X", "Y", "Z"],
    "视角": ["pitch", "yaw", "aim_punch_angle", "aim_punch_angle_vel"],
    "速度/移动": [
        "velocity", "velocity_X", "velocity_Y", "velocity_Z", "is_airborne",
        "is_walking", "ducked", "in_crouch", "duck_amount", "is_scoped",
        "fall_velocity",
    ],
    "开火": ["shots_fired", "active_weapon", "active_weapon_name", "active_weapon_ammo", "is_in_reload"],
    "可见性 spotted": [
        "spotted", "approximate_spotted_by",
        "CCSPlayerPawn.m_bSpotted", "CCSPlayerPawn.m_bSpottedByMask",
    ],
    "闪光": [
        "flash_duration", "flash_max_alpha",
        "enemies_flashed_this_round", "enemies_flashed_total",
        "CCSPlayerPawn.m_bFlashing",
    ],
    "经济": [
        "balance", "start_balance", "current_equip_value", "round_start_equip_value",
        "cash_spent_this_round", "total_cash_spent", "has_helmet", "has_defuser",
        "CCSPlayerController.CCSPlayerController_ActionTrackingServices.m_iEquipmentValue",
        "CCSPlayerController.CCSPlayerController_ActionTrackingServices.m_iMoneySaved",
    ],
    "道具/背包": ["inventory", "inventory_as_ids"],
    "伤害统计": [
        "CCSPlayerController.CCSPlayerController_ActionTrackingServices.m_iUtilityDamage",
        "CCSPlayerController.CCSPlayerController_ActionTrackingServices.m_iDamage",
    ],
    "比赛规则": [
        "total_rounds_played", "rounds_played_this_phase", "is_freeze_period",
        "is_warmup_period", "is_bomb_planted", "game_time", "round_start_time",
        "is_match_started", "game_phase",
    ],
    "usercmd（通常仅 POV demo）": ["usercmd_viewangle_x", "usercmd_viewangle_y", "usercmd_mouse_dx", "usercmd_buttonstate_1"],
}

# 给关键事件附加的玩家字段，验证 parse_event(player=..., other=...) 是否可用
EVENT_EXTRA_PLAYER = ["X", "Y", "Z", "pitch", "yaw", "velocity_X", "velocity_Y", "team_num"]
EVENT_EXTRA_OTHER = ["total_rounds_played", "is_freeze_period"]
EVENTS_WITH_EXTRA = ["weapon_fire", "player_death", "player_hurt", "player_blind"]


def _jsonable(v: Any) -> Any:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item"):  # numpy 标量
        try:
            return _jsonable(v.item())
        except (ValueError, AttributeError):
            pass
    if hasattr(v, "tolist"):
        return _jsonable(v.tolist())
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return repr(v)


def _sample_values(s: pd.Series, n: int = 5) -> list[Any]:
    vals = s.dropna()
    try:
        uniq = vals.drop_duplicates()
    except TypeError:  # list 类型等不可哈希
        uniq = vals.astype(str).drop_duplicates().map(lambda x: x)
    return [_jsonable(v) for v in uniq.head(n).tolist()]


def _describe_column(s: pd.Series) -> dict[str, Any]:
    info: dict[str, Any] = {
        "dtype": str(s.dtype),
        "non_null_ratio": round(float(s.notna().mean()), 4) if len(s) else 0.0,
        "samples": _sample_values(s),
    }
    if pd.api.types.is_numeric_dtype(s) and s.notna().any() and s.dtype != bool:
        info.update(min=_jsonable(s.min()), max=_jsonable(s.max()), mean=_jsonable(round(float(s.mean()), 3)))
    return info


def parse_event_df(parser: DemoParser, name: str, **kwargs) -> pd.DataFrame:
    """parse_event 在事件不存在时返回空 list 而不是 DataFrame，这里统一成空 DataFrame。"""
    df = parser.parse_event(name, **kwargs)
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


def find_default_demo(demo_dir: Path) -> Path:
    for p in sorted(demo_dir.iterdir()):
        if p.is_file() and p.name.lower().endswith(DEMO_SUFFIXES):
            return p
    raise SystemExit(f"{demo_dir} 下没有找到 demo 文件")


# demo 中缺少这些事件时，demoparser2 会自行合成，但不会出现在 list_game_events() 里
SYNTHESIZED_CANDIDATES = ["round_start", "round_end", "round_officially_ended"]


def explore_events(parser: DemoParser) -> dict[str, Any]:
    out: dict[str, Any] = {}
    listed = set(parser.list_game_events())
    for name in sorted(listed | set(SYNTHESIZED_CANDIDATES)):
        try:
            df = parse_event_df(parser, name)
        except Exception as e:  # noqa: BLE001 - 探索脚本，记录失败原因即可
            out[name] = {"error": f"{type(e).__name__}: {e}"}
            continue
        if name not in listed and df.empty:
            continue
        out[name] = {
            "count": len(df),
            "synthesized": name not in listed,
            "columns": {c: _describe_column(df[c]) for c in df.columns},
        }
    return out


def explore_event_extras(parser: DemoParser) -> dict[str, Any]:
    out: dict[str, Any] = {}
    present = set(parser.list_game_events())
    for name in EVENTS_WITH_EXTRA:
        if name not in present:
            out[name] = {"error": "该 demo 中没有此事件"}
            continue
        try:
            df = parse_event_df(parser, name, player=EVENT_EXTRA_PLAYER, other=EVENT_EXTRA_OTHER)
        except Exception as e:  # noqa: BLE001
            out[name] = {"error": f"{type(e).__name__}: {e}"}
            continue
        out[name] = {"count": len(df), "columns": sorted(df.columns)}
    return out


def sample_ticks(parser: DemoParser, step: int) -> list[int]:
    # 不同来源的 demo 事件集合不同（ESL/HLTV demo 可能没有 round_end），取多个事件的最大 tick 作为终点
    df = parse_event_df(parser, "round_freeze_end")
    last = [parse_event_df(parser, ev) for ev in ("round_end", "player_death", "weapon_fire")]
    max_tick = max((int(e["tick"].max()) for e in last if len(e)), default=int(df["tick"].max()) + 64 * 120)
    start = int(df["tick"].min()) if len(df) else 0
    return list(range(start, max_tick, step))


def probe_tick_props(parser: DemoParser, ticks: list[int]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for group, props in CANDIDATE_TICK_PROPS.items():
        for prop in props:
            try:
                df = parser.parse_ticks([prop], ticks=ticks)
            except Exception as e:  # noqa: BLE001
                results[prop] = {"group": group, "available": False, "error": f"{type(e).__name__}: {e}"}
                continue
            if prop not in df.columns:
                results[prop] = {"group": group, "available": False, "returned_columns": sorted(df.columns)}
                continue
            col = df[prop]
            results[prop] = {"group": group, "available": bool(col.notna().any()), "rows": len(df), **_describe_column(col)}
    return results


def tick_rate(parser: DemoParser) -> float | None:
    """用 game_time 与 tick 的关系估算 tick rate。"""
    try:
        df = parser.parse_ticks(["game_time"], ticks=list(range(1000, 200000, 5000)))
    except Exception:  # noqa: BLE001
        return None
    df = df.dropna(subset=["game_time"]).drop_duplicates("tick").sort_values("tick")
    if len(df) < 2:
        return None
    dt = df["game_time"].iloc[-1] - df["game_time"].iloc[0]
    return round(float((df["tick"].iloc[-1] - df["tick"].iloc[0]) / dt), 3) if dt else None


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demo", type=Path, nargs="?", default=None)
    ap.add_argument("--demo-member", default=None, help="压缩包内有多个 demo 时指定其一")
    ap.add_argument("--out", type=Path, default=Path("data/output/explore"))
    ap.add_argument("--tick-step", type=int, default=32, help="tick 字段探测的采样步长")
    args = ap.parse_args(argv)

    src = args.demo or find_default_demo(Path("data/demos"))
    demo = resolve_demo(src, member=args.demo_member)
    print(f"demo: {demo}  ({demo.stat().st_size / 1e6:.1f} MB)")
    parser = DemoParser(str(demo))

    header = parser.parse_header()
    print("\n== 文件头")
    for k, v in header.items():
        print(f"  {k}: {v}")

    rate = tick_rate(parser)
    print(f"\n== 估算 tick rate: {rate}")

    print("\n== 事件")
    events = explore_events(parser)
    for name, info in events.items():
        if "error" in info:
            print(f"  {name}: 解析失败 {info['error']}")
        else:
            tag = " [未列出，demoparser2 合成]" if info["synthesized"] else ""
            print(f"  {name} ({info['count']}){tag}: {', '.join(info['columns'])}")

    print("\n== 事件附加玩家字段 (player=/other=)")
    extras = explore_event_extras(parser)
    for name, info in extras.items():
        print(f"  {name}: {info.get('columns') or info.get('error')}")

    ticks = sample_ticks(parser, args.tick_step)
    print(f"\n== tick 字段探测（{len(ticks)} 个采样 tick，步长 {args.tick_step}）")
    props = probe_tick_props(parser, ticks)
    for prop, info in props.items():
        mark = "OK " if info["available"] else "-- "
        detail = ""
        if info["available"]:
            detail = f"{info['dtype']} non_null={info['non_null_ratio']} samples={str(info['samples'])[:100]}"
        elif "error" in info:
            detail = info["error"][:100]
        elif "dtype" in info:
            detail = "列存在但全为空"
        else:
            detail = "未返回该列"
        print(f"  {mark}[{info['group']}] {prop}: {detail}")

    player_info = parser.parse_player_info()
    print(f"\n== 玩家信息 ({len(player_info)})\n{player_info.to_string()}")

    grenades = parser.parse_grenades()
    print(f"\n== parse_grenades: {len(grenades)} 行, 列 {list(grenades.columns)}")
    if len(grenades):
        print(f"  grenade_type: {grenades['grenade_type'].value_counts().to_dict()}")

    updated = sorted(parser.list_updated_fields())
    print(f"\n== list_updated_fields: {len(updated)} 个原始字段（完整列表见 JSON）")

    result = {
        "demo": str(demo),
        "header": header,
        "tick_rate_estimate": rate,
        "events": events,
        "event_extras": extras,
        "tick_props": props,
        "player_info": _jsonable(player_info.to_dict(orient="records")),
        "grenades": {"rows": len(grenades), "columns": {c: _describe_column(grenades[c]) for c in grenades.columns}},
        "updated_fields": updated,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    out_file = args.out / f"{demo.stem}.json"
    out_file.write_text(json.dumps(_jsonable(result), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完整结果已写入 {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
