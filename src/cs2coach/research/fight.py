"""R03 交战后换位：交火段、交火位置与规则基线（docs/tasks/research/R03-ai-post-fight-reposition.md）。"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from cs2coach.parsing.match import MatchData

# 第 4 版（2026-10-20 产品负责人复核 fk-review-1 后）：去掉"撤离"——职业选手很少头也不回地离开交火区，
# 往后挪的都算换位；前后方向以 T 出生点为参照（首杀选手的对手已经死了，威胁来自 T 方其余的人）
LABELS = {"push": "继续压", "move": "换位", "stay": "原位再架", "unknown": "看不出"}


@dataclass
class Fight:
    start_tick: int
    end_tick: int
    opponent: int | None  # 交火段最后一个与他有伤害或击杀关系的敌人
    kinds: list[str]


@dataclass
class Reposition:
    label: str  # LABELS 的键
    fight: Fight | None
    observe_end: int | None = None
    location: tuple[float, float, float] | None = None  # 交火位置 F
    location_source: str | None = None  # opponent / visible / aim
    move: float | None = None  # 水平挪动
    height: float | None = None  # 高度变化
    closer: float | None = None  # 沿可走路线离 T 出生点近了多少（正数 = 更靠前）；没有导航网格时为空


def fight_events(match: MatchData, sid: int, round_id: int, start: int, end: int) -> pd.DataFrame:
    """他在 [start, end] 内的开枪、造成伤害、受到伤害、击杀事件：tick、kind、enemy（无对象时为空）。"""
    rows = []
    shots = match.shots
    if not shots.empty:
        own = shots[(shots["steamid"] == sid) & shots["is_gun"] & (shots["tick"] >= start) & (shots["tick"] <= end)]
        rows += [(int(t), "shot", None) for t in own["tick"]]
    team = _team(match, sid, start)
    for frame, kind, me, other in ((match.damages, "hit", "attacker", "victim"),
                                   (match.damages, "hurt", "victim", "attacker"),
                                   (match.kills, "kill", "attacker", "victim")):
        if frame.empty:
            continue
        part = frame[(frame["round"] == round_id) & (frame[me] == sid) & (frame["tick"] >= start)
                     & (frame["tick"] <= end) & frame[other].notna()]
        for tick, enemy in zip(part["tick"], part[other]):
            if _team(match, int(enemy), int(tick)) not in (None, team):
                rows.append((int(tick), kind, int(enemy)))
    frame = pd.DataFrame({"tick": pd.Series([r[0] for r in rows], dtype="int64"),
                          "kind": pd.Series([r[1] for r in rows], dtype=object),
                          "enemy": pd.Series([r[2] for r in rows], dtype="Int64")})  # 混入 None 时 float 会丢 steamid 精度
    return frame.sort_values("tick", kind="stable").reset_index(drop=True)


def find_fight(events: pd.DataFrame, trigger: int, gap_ticks: int) -> Fight | None:
    """包含触发时刻的交火段；没有就取触发后的第一段，再没有就取触发前最后一段。"""
    if events.empty:
        return None
    episodes, current = [], [events.iloc[0]]
    for _, row in events.iloc[1:].iterrows():
        if row["tick"] - current[-1]["tick"] <= gap_ticks:
            current.append(row)
        else:
            episodes.append(current)
            current = [row]
    episodes.append(current)
    spans = [(int(ep[0]["tick"]), int(ep[-1]["tick"]), ep) for ep in episodes]
    chosen = next((s for s in spans if s[0] <= trigger <= s[1] + gap_ticks), None) \
        or next((s for s in spans if s[0] > trigger), None) or spans[-1]
    start, end, rows = chosen
    enemies = [int(r["enemy"]) for r in rows if pd.notna(r["enemy"])]
    return Fight(start_tick=start, end_tick=end, opponent=enemies[-1] if enemies else None,
                 kinds=sorted({str(r["kind"]) for r in rows}))


def reposition(match: MatchData, sid: int, round_id: int, trigger: int, cfg: dict,
               sight=None, lead_ticks: int = 0, window_end: int | None = None, nav=None) -> Reposition:
    """规则基线（R03 第 4 版）：比较交火结束时与观察期结束时的位置，按顺序判断。

    原位再架：水平挪动 < stay_max_move 且高度变化 < stay_max_height；继续压：区域层级前进一层，
    或沿可走路线离 T 出生点近了 push_min_closer；其余为换位（含往后挪）。
    """
    limits = cfg["research"]["fight"]
    rate = match.meta.tick_rate
    rnd = match.round(round_id)
    end = window_end if window_end is not None else trigger + round(cfg["research"]["window_seconds"] * rate)
    events = fight_events(match, sid, round_id, trigger - lead_ticks, end)
    fight = find_fight(events, trigger, round(limits["gap_seconds"] * rate))
    if fight is None:
        return Reposition(label="unknown", fight=None)
    stop = fight.end_tick + round(limits["observe_seconds"] * rate)
    if rnd is not None:
        stop = min(stop, rnd.end_tick)
    death = _death_tick(match, sid, round_id, fight.end_tick)
    if death is not None:
        stop = min(stop, death)
    result = Reposition(label="unknown", fight=fight, observe_end=stop)
    location, source = _location(match, sid, fight, sight)
    result.location, result.location_source = location, source
    s0, s3 = match.state_at(sid, fight.end_tick), match.state_at(sid, stop)
    p0, p3 = _xyz(s0), _xyz(s3)
    if (stop - fight.end_tick) < limits["min_observe_seconds"] * rate or p0 is None or p3 is None or location is None:
        return result
    result.move = round(math.dist(p0[:2], p3[:2]), 1)
    result.height = round(p3[2] - p0[2], 1)
    goal = limits["t_spawn"].get(match.meta.map_name)
    if nav is not None and goal is not None:
        from cs2coach.research.windows import _nav_distance

        snap = cfg["research"]["nav_snap_max_distance"]
        d0, d3 = _nav_distance(nav, p0, tuple(goal), snap), _nav_distance(nav, p3, tuple(goal), snap)
        if d0 is not None and d3 is not None:
            result.closer = round(d0 - d3, 1)
    layer0 = area_of(s0, cfg, match.meta.map_name)["layer"]
    layer3 = area_of(s3, cfg, match.meta.map_name)["layer"]
    forward = layer0 is not None and layer3 is not None and layer3 > layer0
    ahead = result.closer is not None and result.closer >= limits["push_min_closer"]
    if result.move < limits["stay_max_move"] and abs(result.height) < limits["stay_max_height"]:
        result.label = "stay"
    elif forward or ahead:
        result.label = "push"
    else:
        result.label = "move"
    return result


LAYERS = {"deep": 0, "hold": 1, "contact": 2}


def area_of(state, cfg: dict, map_name: str) -> dict:
    """报点（Palace 等按高度拆分）、区域层级（后方 0 / 防守区 1 / 接触区 2）、所属防守区组。"""
    from cs2coach.research.labels import _place

    research = cfg["research"]
    if state is None or pd.isna(state["last_place_name"]):
        return {"place": None, "layer": None, "zones": set()}
    name = str(state["last_place_name"])
    point = (float(state["X"]), float(state["Y"]), float(state["Z"])) if pd.notna(state["Z"]) else None
    place = _place(name, point, research.get("floors", {}).get(map_name, {}))
    zones = {zone for zone, places in research.get("zones", {}).get(map_name, {}).items() if place in places}
    return {"place": place, "layer": LAYERS.get(research["regions"].get(map_name, {}).get(name, "")), "zones": zones}


def _location(match: MatchData, sid: int, fight: Fight, sight) -> tuple[tuple[float, float, float] | None, str | None]:
    if fight.opponent is not None:
        point = _xyz(match.state_at(fight.opponent, fight.end_tick))
        if point is not None:
            return point, "opponent"
    me = match.state_at(sid, fight.end_tick)
    if me is None or _xyz(me) is None:
        return None, None
    if sight is not None and getattr(sight, "available", True):
        team = int(me["team_num"])
        seen = []
        for other in match.players:
            st = match.state_at(other, fight.end_tick)
            if st is not None and pd.notna(st["team_num"]) and int(st["team_num"]) not in (team, 0, 1) \
                    and bool(st["is_alive"]) and sight.sees(me, st):
                seen.append((math.dist(_xyz(me), _xyz(st)), _xyz(st)))
        if seen:
            return min(seen)[1], "visible"
    x, y, z = _xyz(me)
    yaw = math.radians(float(me["yaw"])) if pd.notna(me["yaw"]) else 0.0
    return (x + 1000 * math.cos(yaw), y + 1000 * math.sin(yaw), z), "aim"


def _death_tick(match: MatchData, sid: int, round_id: int, after: int) -> int | None:
    k = match.kills
    if k.empty:
        return None
    hit = k[(k["round"] == round_id) & (k["victim"] == sid) & (k["tick"] >= after)]
    return int(hit["tick"].min()) if not hit.empty else None


def _team(match: MatchData, sid: int, tick: int) -> int | None:
    st = match.state_at(sid, tick)
    return int(st["team_num"]) if st is not None and pd.notna(st["team_num"]) else None


def _xyz(state) -> tuple[float, float, float] | None:
    if state is None or pd.isna(state["X"]) or pd.isna(state["Y"]):
        return None
    return float(state["X"]), float(state["Y"]), float(state["Z"]) if pd.notna(state["Z"]) else 0.0
