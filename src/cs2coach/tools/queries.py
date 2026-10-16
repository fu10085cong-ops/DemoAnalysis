"""AI 可调用的查询工具。只有两个、参数简单，便宜的模型也不容易用错（CLAUDE.md 架构原则 4）。

工具定义与模型无关（JSON Schema）；返回值是 JSON 字符串。
"""

from __future__ import annotations

import json
from typing import Any

from cs2coach.features.labels import ROUND_END_REASONS
from cs2coach.features.models import MatchMoments
from cs2coach.parsing.match import MatchData
from cs2coach.parsing.models import Team
from cs2coach.tools.digest import player_side

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_moment",
        "description": (
            "查看一个决定性瞬间的完整数据（JSON）。摘要里的数据不够判断时再用，参数是摘要中的编号，例如 R5-death。"
            "tick 每秒 64 个。主要字段：context（瞬间前一刻的局势：存活人数、装备、手持武器、位置、距离 distance 单位为米；"
            "round_time_s 是冻结期结束后已经过去的秒数，不是剩余时间）；"
            "engagement.shots（这次交火中瞄准这名对手的每一枪：speed 开枪时水平速度 u/s、max_speed 武器最高速度、stopped 是否停稳、"
            "hit 是否命中、aim_error_deg 子弹方向偏离对手身体中心的角度）；"
            "engagement.player_first_seen_tick / opponent_first_seen_tick（自己 / 对手第一次看见对方的 tick；"
            "visibility_note 为 smoke 或 blind 时，这两个 tick 只作核对，不能据此判断谁先看见）；"
            "engagement.time_to_damage_s（自己从看见对手到第一次打中他）；"
            "engagement.opponent_time_to_damage_s（对手从看见自己到第一次打中自己，不是开枪到击杀的时间）；"
            "engagement.player_first_shot_tick / opponent_first_shot_tick（双方第一枪的 tick）；"
            "engagement.player_speed_at_contact / opponent_speed_at_contact / contact_type（接触时双方速度及判断：peek 主动拉出、held 架点、unclear 无法判断）；"
            "engagement.player_blind / opponent_blind（双方交火中的致盲：blinded_at_event 事件时是否仍被闪，"
            "remaining_at_event_s 事件时剩余秒数，max_duration_s 相关致盲的最长时长，thrower 投掷者，"
            "thrower_ambiguous 投掷者是否不确定，thrower_role 相对目标玩家为 self 本人、teammate 队友、opponent 本次对手、enemy 对手的其他队友；未知时为空）；"
            "trade（是否被补回、最近队友距离，单位为游戏单位）；death（死时身上的道具、是否被闪，"
            "thrown_grenades 是本回合阵亡前已投道具，按时间顺序且保留重复，不含接管 bot 时的投掷）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {"moment_id": {"type": "string", "description": "瞬间编号，如 R5-death、R12-kill2"}},
            "required": ["moment_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_round",
        "description": (
            "查看一个回合的完整经过：双方每一次击杀的时间、击杀者、受害者、武器，炸弹事件，"
            "双方的装备价值，以及目标玩家在这一回合的瞬间编号。用于判断一个瞬间前后的局势。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {"round": {"type": "integer", "description": "回合号，从 1 开始"}},
            "required": ["round"],
            "additionalProperties": False,
        },
    },
]


class ToolError(Exception):
    pass


def run_tool(name: str, args: dict[str, Any], match: MatchData, mm: MatchMoments) -> str:
    if name == "get_moment":
        return get_moment(str(args.get("moment_id", "")), mm)
    if name == "get_round":
        try:
            number = int(args.get("round"))
        except (TypeError, ValueError):
            raise ToolError("round 应为整数") from None
        return get_round(number, match, mm)
    raise ToolError(f"没有名为 {name} 的工具")


def get_moment(moment_id: str, mm: MatchMoments) -> str:
    m = next((x for x in mm.moments if x.id == moment_id), None)
    if m is None:
        ids = ", ".join(x.id for x in mm.moments)
        raise ToolError(f"没有编号为 {moment_id!r} 的瞬间。可用编号：{ids}")
    return m.model_dump_json(exclude={"player", "jump_command"})


def get_round(number: int, match: MatchData, mm: MatchMoments) -> str:
    r = match.round(number)
    if r is None:
        raise ToolError(f"没有第 {number} 回合（共 {len(match.rounds)} 回合）")
    side = player_side(match, mm.player, number)
    start = r.freeze_end_tick if r.freeze_end_tick is not None else r.start_tick
    rate = match.meta.tick_rate

    def name(sid: object) -> str | None:
        try:
            return match.name_of(int(sid))
        except (TypeError, ValueError):
            return None

    def team(v: object) -> str | None:
        try:
            return Team(int(v)).name
        except (TypeError, ValueError):
            return None

    k = match.kills
    k = k[(k["round"] == number) & ~k["in_freeze_time"]]
    kills = [{
        "tick": int(x.tick), "time_s": round((int(x.tick) - start) / rate, 2),
        "killer": name(x.attacker), "killer_side": team(x.attacker_team_num),
        "victim": name(x.victim), "victim_side": team(x.victim_team_num),
        "weapon": x.weapon, "headshot": bool(x.headshot),
        "after_round_end": bool(x.after_round_end),
    } for x in k.itertuples()]
    b = match.bomb
    bomb = [] if b.empty else [{"tick": int(x.tick), "time_s": round((int(x.tick) - start) / rate, 2),
                                "event": x.event, "player": name(x.steamid)}
                               for x in b[b["round"] == number].itertuples()]
    equip: dict[str, int] = {}
    e = match.economy
    if not e.empty:
        er = e[e["round"] == number]
        for t, g in er.groupby("team_num"):
            if team(t):
                equip[team(t)] = int(g["current_equip_value"].fillna(0).sum())
    out = {
        "round": number,
        "player_side": side,
        "winner": r.winner.name,
        "player_won": side == r.winner.name if side else None,
        "end_reason": ROUND_END_REASONS.get(r.reason, str(r.reason)),
        "round_length_s": round((r.end_tick - start) / rate, 1),
        "team_equip_value_at_buy_end": equip,
        "kills": kills,
        "bomb": bomb,
        "player_moments": [x.id for x in mm.moments if x.round == number],
    }
    return json.dumps(out, ensure_ascii=False)
