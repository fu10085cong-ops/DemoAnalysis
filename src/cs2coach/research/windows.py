"""逐 tick 行为窗口与可回放证据。"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from cs2coach.parsing.match import MatchData
from cs2coach.research.labels import label_window
from cs2coach.research.models import BehaviourWindow, Situation
from cs2coach.research.triggers import alive_at, round_roster


def _position(state) -> tuple[float, float, float] | None:
    if state is None or any(pd.isna(state[key]) for key in ("X", "Y", "Z")):
        return None
    return tuple(float(state[key]) for key in ("X", "Y", "Z"))


def _distance(a, b) -> float | None:
    p, q = _position(a), _position(b)
    return math.dist(p, q) if p is not None and q is not None else None


_CENTROIDS: dict[int, tuple] = {}


def _snap(nav, point, max_distance: float):
    """点不在任何导航区域内时，退回到 max_distance 以内最近的区域中心；没有则为 None。"""
    if nav.find_area(point) is not None:
        return point
    areas = getattr(nav, "areas", None)
    if areas is None or max_distance <= 0:
        return None
    key = id(nav)
    if key not in _CENTROIDS:
        frame = areas.to_pandas() if hasattr(areas, "to_pandas") else pd.DataFrame(areas)
        _CENTROIDS[key] = (nav, frame[["centroid_x", "centroid_y", "centroid_z"]].to_numpy(dtype=float))
    centroids = _CENTROIDS[key][1]
    if not len(centroids):
        return None
    distances = np.linalg.norm(centroids - np.asarray(point, dtype=float), axis=1)
    best = int(distances.argmin())
    return tuple(float(v) for v in centroids[best]) if distances[best] <= max_distance else None


def _nav_distance(nav, point, goal, max_snap: float = 0) -> float | None:
    if nav is None or point is None or goal is None:
        return None
    try:
        snapped, goal_point = _snap(nav, point, max_snap), _snap(nav, goal, max_snap)
        if snapped is None or goal_point is None:
            return None
        offset = math.dist(point, snapped)  # 退回到最近区域时，把这段直线距离也算进路径
        point, goal = snapped, goal_point
        path = nav.find_path(point, goal)
        if not path:
            return None
        centers = [tuple(nav.area(int(area))["centroid"]) for area in path]
        route = [point, *centers, goal]
        return round(offset + sum(math.dist(a, b) for a, b in zip(route, route[1:])), 1)
    except (KeyError, ValueError, TypeError, RuntimeError):
        return None


def _nearest(match: MatchData, sid: int, ct: set[int], tick: int) -> float | None:
    me = match.state_at(sid, tick)
    values = []
    for other in ct - {sid}:
        state = match.state_at(other, tick)
        if state is not None and bool(state["is_alive"]):
            value = _distance(me, state)
            if value is not None:
                values.append(value)
    return round(min(values), 1) if values else None


def _seen(sight, viewer, target, tick: int) -> bool:
    if viewer is None or target is None or not sight.sees(viewer, target):
        return False
    if pd.notna(viewer.get("flash_duration")) and float(viewer["flash_duration"]) > sight.min_blind:
        return False
    return not sight.smoke_between(viewer, target, tick)


def _events(df: pd.DataFrame, round_id: int, start: int, end: int) -> pd.DataFrame:
    if df.empty:
        return df
    return df[(df["round"] == round_id) & (df["tick"] >= start) & (df["tick"] <= end)]


def behaviour_windows(match: MatchData, situation: Situation, sight, nav, cfg: dict) -> list[BehaviourWindow]:
    """触发时存活的每名 CT 一行，阵亡时缩短观察区间。"""
    rnd = match.round(situation.round_id)
    if rnd is None or rnd.freeze_end_tick is None:
        return []
    rate = match.meta.tick_rate
    end_limit = min(rnd.end_tick, situation.tick + round(cfg["research"]["window_seconds"] * rate))
    roster = round_roster(match, situation.round_id)
    alive = alive_at(match, situation.round_id, situation.tick)
    ct = {sid for sid, team in roster.items() if team == 3}
    enemies = {sid for sid, team in roster.items() if team == 2}
    t_positions = [_position(match.state_at(sid, rnd.freeze_end_tick)) for sid in enemies]
    goal = tuple(float(np.mean([point[i] for point in t_positions])) for i in range(3)) \
        if len(t_positions) == 5 and all(point is not None for point in t_positions) else None
    if nav is None or goal is None or _snap(nav, goal, cfg["research"]["nav_snap_max_distance"]) is None:
        goal = None
    rows = []
    for sid in sorted(sid for sid in ct if sid in alive):
        deaths = _events(match.kills, situation.round_id, situation.tick + 1, end_limit)
        deaths = deaths[deaths["victim"] == sid] if not deaths.empty else deaths
        death_tick = int(deaths["tick"].iloc[0]) if not deaths.empty else None
        end = min(end_limit, death_tick) if death_tick is not None else end_limit
        frame = match.player_ticks(sid, situation.tick, end)
        states = {int(row["tick"]): row for _, row in frame.iterrows()}
        start_state, end_state = states.get(situation.tick), states.get(end)
        start_position, end_position = _position(start_state), _position(end_state)
        displacement = _distance(start_state, end_state)
        snap = cfg["research"]["nav_snap_max_distance"]
        nav_start = _nav_distance(nav, start_position, goal, snap)
        nav_end = _nav_distance(nav, end_position, goal, snap)
        delta = round(nav_end - nav_start, 1) if nav_start is not None and nav_end is not None else None
        yaw = pd.to_numeric(frame.get("yaw", pd.Series(dtype=float)), errors="coerce").dropna().to_numpy()
        yaw_change = round(float(np.abs((np.diff(yaw) + 180) % 360 - 180).sum()), 1) if len(yaw) else None

        shots = _events(match.shots, situation.round_id, situation.tick, end)
        shots = shots[(shots["steamid"] == sid) & shots["is_gun"] & ~shots["controlling_bot"]] \
            if not shots.empty else shots
        throws = _events(match.shots, situation.round_id, situation.tick, end)
        throws = throws[(throws["steamid"] == sid) & throws["is_grenade"] & ~throws["controlling_bot"]] \
            if not throws.empty else throws
        utility = list(dict.fromkeys(str(x) for x in throws["weapon"])) if not throws.empty else []
        damages = _events(match.damages, situation.round_id, situation.tick, end)
        dealt = damages[damages["attacker"] == sid] if not damages.empty else damages
        taken = damages[damages["victim"] == sid] if not damages.empty else damages
        kills = _events(match.kills, situation.round_id, situation.tick, end)
        killed = bool(not kills.empty and ((kills["attacker"] == sid) & kills["is_enemy_kill"]).any())
        visible_trigger = visible_window = radar_count = None
        if sight is not None and getattr(sight, "available", True):
            visible_trigger_set: set[int] = set()
            visible_window_set: set[int] = set()
            enemy_frames = {enemy: {int(row["tick"]): row for _, row in
                                     match.player_ticks(enemy, situation.tick, end).iterrows()} for enemy in enemies}
            for enemy, timeline in enemy_frames.items():
                target = timeline.get(situation.tick)
                if _seen(sight, start_state, target, situation.tick):
                    visible_trigger_set.add(enemy)
                if any(_seen(sight, viewer, timeline.get(tick), tick)
                       for tick, viewer in states.items() if tick >= situation.tick):
                    visible_window_set.add(enemy)
            visible_trigger, visible_window = len(visible_trigger_set), len(visible_window_set)
        spotted: set[int] = set()
        for enemy in enemies:
            frame_enemy = match.player_ticks(enemy, situation.tick, end)
            if frame_enemy.empty or "spotted_by_mask" not in frame_enemy:
                continue
            mask = 0
            for teammate in ct:
                bit = match.spotted_bit(teammate)
                if bit is not None:
                    mask |= 1 << bit
            if mask and ((frame_enemy["spotted_by_mask"].fillna(0).astype("int64") & mask) != 0).any():
                spotted.add(enemy)
        radar_count = len(spotted)
        remaining = alive_at(match, situation.round_id, end)
        start_place = str(start_state["last_place_name"]) if start_state is not None \
            and pd.notna(start_state["last_place_name"]) else None
        end_place = str(end_state["last_place_name"]) if end_state is not None \
            and pd.notna(end_state["last_place_name"]) else None
        regions = cfg["research"]["regions"].get(situation.map, {})
        window = BehaviourWindow(**situation.model_dump(), player_id=str(sid), player_name=match.name_of(sid),
                                 tick_rate=rate,
                                 start_tick=situation.tick, end_tick=end,
                                 start_position=start_position, end_position=end_position,
                                 displacement=round(displacement, 1) if displacement is not None else None,
                                 start_place=start_place, end_place=end_place,
                                 start_region=regions.get(start_place) or None,
                                 end_region=regions.get(end_place) or None,
                                 nav_start_distance=nav_start, nav_end_distance=nav_end, nav_delta=delta,
                                 yaw_change=yaw_change, utility_types=utility, shots=len(shots),
                                 damage_dealt=int(dealt["dmg_effective"].sum()) if not dealt.empty else 0,
                                 damage_taken=int(taken["dmg_effective"].sum()) if not taken.empty else 0,
                                 killed_enemy=killed, died=death_tick is not None, death_tick=death_tick,
                                 teammate_distance_start=_nearest(match, sid, ct, situation.tick),
                                 teammate_distance_end=_nearest(match, sid, ct, end),
                                 visible_enemies_trigger=visible_trigger, visible_enemies_window=visible_window,
                                 radar_enemies_window=radar_count,
                                 advantage_held=sum(t == 3 for t in remaining.values()) >
                                 sum(t == 2 for t in remaining.values()), ct_won=int(rnd.winner) == 3,
                                 jump_command=f"demo_gototick {max(0, situation.tick - 2 * rate)}")
        window.main_label, window.aux_labels, window.label_confidence = label_window(window, cfg)
        rows.append(window)
    return rows
