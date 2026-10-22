"""R03 第 5 版：首杀选手拿完人头后是激进还是保守（docs/tasks/research/R03-ai-post-fight-reposition.md "第 5 版定义"）。

分两步：
1. facts_for(match, situation, cfg)：从解析好的比赛取出判定需要的事实——首杀时的局面、他从首杀起的全部交火事件、
   轨迹——不做判定。存下来以后改阈值、改"一次交战"的合并方式都不用重新解析 demo。
2. label(facts, track, events, maps, cfg)：把首杀开始、相邻间隔不超过 fight_gap_seconds 的枪械交火并作一次交战，
   交战结束后观察 observe_seconds，按顺序判定——看不出 → 激进·原位架枪 → 激进·前压 → 保守。
阈值全部在 config/research.toml 的 [research.stance]。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from cs2coach.parsing.match import MatchData
from cs2coach.parsing.weapons import is_gun
from cs2coach.research.danger import T_TEAM, DangerMaps, direction_of

LABELS = {"aggressive_hold": "激进·原位架枪", "aggressive_push": "激进·前压",
          "aggressive_active": "激进·主动寻求对枪", "aggressive_passive": "激进·被动寻求对枪",
          "conservative": "保守", "unknown": "看不出"}
STANCE = {"aggressive_hold": "激进", "aggressive_push": "激进", "aggressive_active": "激进", "aggressive_passive": "激进",
          "conservative": "保守", "unknown": "看不出"}
FIGHT_KINDS = ("hit", "hurt", "kill", "death")  # 他打中 T / 被 T 打中 / 击杀 T / 被 T 击杀
EVENT_COLUMNS = ["demo_id", "round_id", "player_id", "tick", "kind", "enemy", "weapon", "gun", "damage",
                 "enemy_x", "enemy_y", "enemy_z"]


def merge_end(ticks: list[int], first: int, gap_ticks: int) -> int:
    """从 first 开始，相邻间隔不超过 gap_ticks 的并作一次；返回最后一个的 tick。"""
    end = first
    for tick in sorted(t for t in ticks if t > first):
        if tick - end > gap_ticks:
            break
        end = tick
    return end


def _xyz(state) -> tuple[float, float, float] | None:
    if state is None or pd.isna(state["X"]) or pd.isna(state["Y"]):
        return None
    return float(state["X"]), float(state["Y"]), float(state["Z"]) if pd.notna(state["Z"]) else 0.0


def _team(match: MatchData, sid: int, tick: int) -> int | None:
    st = match.state_at(sid, tick)
    return int(st["team_num"]) if st is not None and pd.notna(st["team_num"]) else None


def fight_events(match: MatchData, sid: int, round_id: int, start: int, end: int, demo_id: str) -> pd.DataFrame:
    """[start, end] 内他与 T 之间的伤害、击杀，以及他自己的开枪（kind = shot，无对象）。
    enemy_x/y/z 为对方在该 tick 前一刻的位置（死亡那一 tick 的状态可能已清空）。"""
    rows = []
    for frame, kind, me, other, amount in ((match.damages, "hit", "attacker", "victim", "dmg_effective"),
                                           (match.damages, "hurt", "victim", "attacker", "dmg_effective"),
                                           (match.kills, "kill", "attacker", "victim", None),
                                           (match.kills, "death", "victim", "attacker", None)):
        if frame.empty:
            continue
        part = frame[(frame["round"] == round_id) & (frame[me] == sid) & (frame["tick"] >= start)
                     & (frame["tick"] <= end) & frame[other].notna()]
        for r in part.itertuples(index=False):
            tick, enemy = int(r.tick), int(getattr(r, other))
            if _team(match, enemy, tick - 1) != T_TEAM:
                continue
            weapon = str(getattr(r, "weapon", ""))
            pos = _xyz(match.state_at(enemy, tick - 1))
            rows.append({"tick": tick, "kind": kind, "enemy": str(enemy), "weapon": weapon, "gun": is_gun(weapon),
                         "damage": int(getattr(r, amount)) if amount and pd.notna(getattr(r, amount, None)) else None,
                         "enemy_x": pos[0] if pos else None, "enemy_y": pos[1] if pos else None,
                         "enemy_z": pos[2] if pos else None})
    shots = match.shots
    if not shots.empty:
        own = shots[(shots["steamid"] == sid) & (shots["tick"] >= start) & (shots["tick"] <= end)]
        rows += [{"tick": int(t), "kind": "shot", "enemy": None, "weapon": str(w), "gun": bool(g)}
                 for t, w, g in zip(own["tick"], own["weapon"], own["is_gun"])]
    frame = pd.DataFrame(rows, columns=EVENT_COLUMNS[3:])
    frame.insert(0, "player_id", str(sid))
    frame.insert(0, "round_id", round_id)
    frame.insert(0, "demo_id", demo_id)
    return frame.sort_values("tick", kind="stable").reset_index(drop=True)


def facts_for(match: MatchData, situation, cfg: dict) -> tuple[dict, pd.DataFrame, pd.DataFrame] | None:
    """CT 首杀回合里拿首杀那名选手的判定事实、轨迹（t = 距首杀的秒数）与交火事件；击杀者缺失时为 None。"""
    if situation.first_killer_id is None:
        return None
    p = cfg["research"]["stance"]
    rate = match.meta.tick_rate
    sid, round_id, first = int(situation.first_killer_id), situation.round_id, situation.tick
    rnd = match.round(round_id)
    kills = match.kills
    first_kill = kills[(kills["round"] == round_id) & (kills["attacker"] == sid) & (kills["tick"] == first)]
    if first_kill.empty:
        return None
    first_row = first_kill.iloc[0]
    fk_state = match.state_at(int(first_row["victim"]), first - 1)
    victim_xyz = _xyz(fk_state)
    me = match.state_at(sid, first)
    track_end = first + round(p["track_seconds"] * rate)
    row = {
        "demo_id": situation.demo_id, "match_id": situation.match_id, "round_id": round_id,
        "player_id": str(sid), "player_name": match.name_of(sid), "tick_rate": rate,
        "first_kill_tick": first, "round_time_s": situation.round_time_s, "bomb_planted": situation.bomb_planted,
        "weapon": str(first_row["weapon"]) if "weapon" in first_kill else "",
        "round_end_tick": rnd.end_tick if rnd is not None else None,
        "fk_victim_id": str(int(first_row["victim"])),
        "fk_victim_place": str(fk_state["last_place_name"]) if fk_state is not None and pd.notna(fk_state["last_place_name"]) else None,
        "fk_victim_x": victim_xyz[0] if victim_xyz else None, "fk_victim_y": victim_xyz[1] if victim_xyz else None,
        "fk_victim_z": victim_xyz[2] if victim_xyz else None,
        "start_place": str(me["last_place_name"]) if me is not None and pd.notna(me["last_place_name"]) else None,
        "hp_at_first_kill": int(me["health"]) if me is not None and pd.notna(me["health"]) else None,
        "spotted_t_in_direction": _spotted_in_direction(match, round_id, first - round(p["mainline_lookback_seconds"] * rate),
                                                        first, fk_state, cfg),
        "jump_command": f"demo_gototick {max(0, first - 2 * rate)}",
    }
    events = fight_events(match, sid, round_id, first, track_end, situation.demo_id)
    step = max(1, round(p["track_step_seconds"] * rate))
    lead = round(p["track_lead_seconds"] * rate)
    ticks = match.player_ticks(sid, first - lead, track_end)
    ticks = ticks[((ticks["tick"] - first) % step == 0)]
    track = pd.DataFrame({"demo_id": situation.demo_id, "round_id": round_id, "player_id": str(sid),
                          "t": (ticks["tick"] - first) / rate, "X": ticks["X"], "Y": ticks["Y"], "Z": ticks["Z"],
                          "yaw": ticks["yaw"], "pitch": ticks["pitch"], "health": ticks["health"],
                          "alive": ticks["is_alive"].astype(bool), "place": ticks["last_place_name"],
                          "weapon": ticks["active_weapon_name"], "scoped": ticks["is_scoped"],
                          "duck": ticks["duck_amount"], "walking": ticks["is_walking"], "speed": ticks["speed_xy"]})
    return row, track.reset_index(drop=True), events


def _spotted_in_direction(match: MatchData, round_id: int, start: int, end: int, fk_state, cfg: dict) -> int | None:
    """[start, end] 内本队雷达上发现过、且当时位于首杀方向的不同 T 人数（主攻线索；首杀方向不归类时为空）。"""
    if fk_state is None:
        return None
    map_name = match.meta.map_name
    target = direction_of(str(fk_state["last_place_name"]) if pd.notna(fk_state["last_place_name"]) else None,
                          float(fk_state["X"]), float(fk_state["Y"]), cfg, map_name)
    if target is None:
        return None
    team = {s for s in match.players if (st := match.state_at(s, end)) is not None and pd.notna(st["team_num"])
            and int(st["team_num"]) == 3}
    mask = 0
    for s in team:
        bit = match.spotted_bit(s)
        if bit is not None:
            mask |= 1 << bit
    count = 0
    for enemy in match.players:
        frame = match.player_ticks(enemy, start, end)
        if frame.empty or "spotted_by_mask" not in frame:
            continue
        frame = frame[frame["team_num"] == T_TEAM]  # 阵营缺失的 tick（教练、观战位等）不算
        seen = frame[(frame["spotted_by_mask"].fillna(0).astype("int64") & mask) != 0]
        if any(direction_of(p if isinstance(p, str) else None, x, y, cfg, map_name) == target
               for p, x, y in zip(seen["last_place_name"], seen["X"], seen["Y"])):
            count += 1
    return count


def engagement(events: pd.DataFrame, first: int, gap_ticks: int) -> dict:
    """从首杀开始，枪械交火（伤害、击杀，不论谁打谁）间隔不超过 gap_ticks 的并作一次交战。"""
    fight = events[events["kind"].isin(FIGHT_KINDS) & events["gun"].astype(bool)] if not events.empty else events
    end = merge_end([int(t) for t in fight["tick"]], first, gap_ticks) if not fight.empty else first
    inside = fight[fight["tick"] <= end] if not fight.empty else fight
    after = fight[fight["tick"] > end] if not fight.empty else fight
    died = events[events["kind"] == "death"] if not events.empty else events
    return {"end_tick": end, "kills": int((inside["kind"] == "kill").sum()) if not inside.empty else 0,
            "hurt": int((inside["kind"] == "hurt").sum()) if not inside.empty else 0,
            "next_fight_tick": int(after["tick"].min()) if not after.empty else None,
            "death_tick": int(died["tick"].min()) if not died.empty else None,
            "last_kill": inside[inside["kind"] == "kill"].iloc[-1] if not inside.empty and (inside["kind"] == "kill").any() else None}


def classify(move: float | None, height: float | None, danger0: float | None, danger1: float | None,
             observed_s: float | None, cfg: dict, method: str = "map", smoke: float | None = None,
             own_smoke: float | None = None, t_progress: float | None = None) -> tuple[str, str | None]:
    """按顺序判定，返回 (标签, 小类或看不出的原因)。
    method="map"：danger 是统计危险度地图的倍数，按比例比较；"threat"：danger 是能看到他的威胁加权个数，按差值比较。"""
    p = cfg["research"]["stance"]
    if observed_s is None or move is None or height is None:
        return "unknown", "missing"
    if observed_s < p["min_observe_seconds"]:
        return "unknown", "short"
    # 身边有烟补丁（gold-1 g35、g46）：观察期内大部分时间他身边就有生效中的烟——躲在烟里 / 烟后，不算寻求对枪
    if p.get("patch_own_smoke") and own_smoke is not None and own_smoke >= p["own_smoke_min_share"]:
        return "conservative", "own_smoke"
    if move < p["stay_max_move"] and abs(height) < p["stay_max_height"]:
        # 烟雾补丁（gold-1 g35、g46）：原地不动，但和威胁之间大部分被烟隔开——打不到人也不会被打，不算架枪
        if p.get("patch_smoked_hold") and smoke is not None and smoke >= p["smoked_min_share"]:
            return "conservative", "smoked"
        # 补丁（gold-1 g20）：原地挪了一点、但威胁危险度明显下降——是躲起来，不是架枪
        if (method == "threat" and p.get("patch_hide_when_danger_drops") and danger0 is not None and danger1 is not None
                and danger1 - danger0 <= p["hide_max_delta"]):
            return "conservative", "hide"
        return "aggressive_hold", None
    if danger0 is None or danger1 is None:
        return "unknown", "no_danger"
    if method == "threat":
        q = cfg["research"]["threat"]
        if danger1 - danger0 >= q["push_min_delta"]:
            return "aggressive_push", None
        if danger1 - danger0 <= q["retreat_max_delta"]:
            return "conservative", "retreat"
        # 横移补丁（gold-1 g52、g53、g59）：威胁危险度没怎么变，但沿导航路线往 T 方向走了——去找下一个对枪位
        if p.get("patch_sideways_toward_t") and t_progress is not None and t_progress >= p["sideways_min_progress"]:
            return "aggressive_push", "toward_t"
        return "conservative", "sideways"
    if danger1 >= danger0 * p["push_danger_ratio"]:
        return "aggressive_push", None
    return "conservative", "retreat" if danger1 < danger0 * p["retreat_danger_ratio"] else "sideways"


def facing_share(track: pd.DataFrame, t0: float, t1: float, sources: list[tuple[float, float, float]], cfg: dict) -> float:
    """观察期内（t0 到 t1，相对首杀的秒数）他的视线朝着威胁的程度：每个时刻，威胁来源（按权重）有多少落在视线
    左右 facing_max_angle 度以内，再对各时刻取平均。0 = 背对所有威胁，1 = 威胁全在正前方。没有来源时为 0。"""
    if not sources:
        return 0.0
    part = track[(track["t"] >= t0 - 1e-9) & (track["t"] <= t1 + 1e-9) & track["alive"].astype(bool) & track["yaw"].notna()]
    if part.empty:
        return 0.0
    limit = float(cfg["research"]["stance"]["facing_max_angle"])
    src = np.array([(x, y) for x, y, _ in sources])
    weight = np.array([w for _, _, w in sources])
    if weight.sum() <= 0:
        return 0.0
    shares = []
    for x, y, yaw in zip(part["X"], part["Y"], part["yaw"]):
        ang = np.degrees(np.arctan2(src[:, 1] - y, src[:, 0] - x))
        front = np.abs((ang - float(yaw) + 180) % 360 - 180) <= limit
        shares.append(weight[front].sum() / weight.sum())
    return round(float(np.mean(shares)), 3)


def own_smoke_share(track: pd.DataFrame, t0: float, t1: float, smokes_at, radius: float) -> float | None:
    """观察期 [t0, t1]（距首杀的秒数）内他活着的轨迹样本里，身边 radius 以内（水平距离）有生效中的烟的比例。
    smokes_at(t) → 这一刻生效中的烟中心 (N, ≥2) 数组。"""
    part = track[(track["t"] >= t0 - 1e-9) & (track["t"] <= t1 + 1e-9) & track["alive"].astype(bool)]
    if part.empty:
        return None
    near = 0
    for r in part.itertuples():
        smokes = np.asarray(smokes_at(float(r.t)), dtype=float)
        if len(smokes) and float(np.min(np.hypot(smokes[:, 0] - r.X, smokes[:, 1] - r.Y))) <= radius:
            near += 1
    return round(near / len(part), 3)


def classify_seek(move: float | None, height: float | None, danger0: float | None, danger1: float | None,
                  observed_s: float | None, facing: float | None, cfg: dict) -> tuple[str, str | None]:
    """第 7 版（产品负责人 2026-10-21）：看不出 → 主动寻求对枪（移动了、且所到位置这一回合的威胁危险度明显上升）
    → 被动寻求对枪（观察结束时暴露在威胁下、且观察期内他周围的威胁平均至少一半在视线前方；挪没挪动都算）→ 保守。"""
    p = cfg["research"]["stance"]
    q = cfg["research"]["threat"]
    if observed_s is None or move is None or height is None:
        return "unknown", "missing"
    if observed_s < p["min_observe_seconds"]:
        return "unknown", "short"
    if danger0 is None or danger1 is None:
        return "unknown", "no_danger"
    moved = not (move < p["stay_max_move"] and abs(height) < p["stay_max_height"])
    if moved and danger1 - danger0 >= q["push_min_delta"]:
        return "aggressive_active", None
    if danger1 >= p["passive_min_danger"] and (facing or 0) >= p["facing_min_share"]:
        return "aggressive_passive", None
    if danger1 - danger0 <= q["retreat_max_delta"]:
        return "conservative", "retreat"
    return "conservative", "not_facing" if danger1 >= p["passive_min_danger"] else "hidden"


def _sample(track: pd.DataFrame, at: float, before: bool) -> pd.Series | None:
    """轨迹中 t ≥ at 的第一个（before=False）或 t ≤ at 的最后一个（before=True）存活样本。"""
    alive = track[track["alive"].astype(bool)]
    part = alive[alive["t"] <= at + 1e-9] if before else alive[alive["t"] >= at - 1e-9]
    if part.empty:
        return None
    return part.iloc[-1] if before else part.iloc[0]


def label(facts: dict, track: pd.DataFrame, events: pd.DataFrame, maps: DangerMaps | None, cfg: dict,
          map_name: str, threat=None, progress=None, smokes_at=None) -> dict:
    """threat：可选，threat(p0, p1, start_tick, stop_tick) → (危险度0, 危险度1, 威胁个数)，即这一回合的威胁热力图
    （research/threat.py）；配置 danger_method = "threat" 时用它判断前压 / 保守，否则只记录。
    progress：可选，progress(p0, p1) → 从 p0 到 p1 沿导航路线往 T 出生点缩短的距离（横移补丁）；
    smokes_at：可选，smokes_at(t) → 距首杀 t 秒时生效中的烟中心数组（身边有烟补丁）。"""
    p = cfg["research"]["stance"]
    rate = facts["tick_rate"]
    first = int(facts["first_kill_tick"])
    fight = engagement(events, first, round(p["fight_gap_seconds"] * rate))
    start = fight["end_tick"]
    out = {"engagement_end_s": round((start - first) / rate, 3), "engagement_kills": fight["kills"],
           "engagement_hurt": fight["hurt"], "observe_end_tick": None, "observed_s": None, "stop_reason": None,
           "refight": False, "move": None, "height": None, "danger_start": None, "danger_end": None,
           "view_toward_kill": None, "threat_start": None, "threat_end": None, "threat_count": None, "facing_share": None, "smoke_share": None,
           "t_progress": None, "own_smoke_share": None}
    direction = None
    if facts.get("fk_victim_x") is not None and not pd.isna(facts["fk_victim_x"]):
        direction = direction_of(facts.get("fk_victim_place"), facts["fk_victim_x"], facts["fk_victim_y"], cfg, map_name)
    out["direction"] = direction
    if p.get("exclude_after_plant") and facts.get("bomb_planted") in (True, "True", 1):
        out["label"], out["detail"] = "unknown", "after_plant"
        return _finish(out)
    if fight["death_tick"] is not None and fight["death_tick"] <= start:
        out["label"], out["detail"], out["stop_reason"] = "unknown", "died_in_fight", "death"
        return _finish(out)
    stops = {"observe": start + round(p["observe_seconds"] * rate)}
    for value, name in ((fight["death_tick"], "death"), (facts.get("round_end_tick"), "round_end"),
                        (fight["next_fight_tick"], "new_fight")):
        if value is not None and not pd.isna(value):
            stops[name] = int(value)
    reason = min(stops, key=lambda k: (stops[k], k != "observe"))
    stop = max(stops[reason], start)
    if (stop - first) / rate > p["track_seconds"] + 1e-9:  # 交战太长，轨迹不够：需要加大 track_seconds 重新提取
        out["label"], out["detail"] = "unknown", "track_too_short"
        return _finish(out)
    out.update(observe_end_tick=stop, observed_s=round((stop - start) / rate, 3), stop_reason=reason,
               refight=reason == "new_fight")
    seek = p.get("scheme", "position") == "seek"
    if (reason == "round_end" and out["observed_s"] < p["min_observe_seconds"] and start > first
            and (seek and p.get("fought_to_round_end_active", True) or not seek and p.get("patch_fought_to_round_end"))):
        # 从首杀起一直对枪打到回合结束（gold-1 g54），没有交战结束后的时段：第 7 版算主动寻求对枪，第 6 版补丁算前压
        out["label"], out["detail"] = ("aggressive_active" if seek else "aggressive_push"), "fought_to_round_end"
        return _finish(out)
    p0 = _sample(track, (start - first) / rate, before=False)
    p1 = _sample(track, (stop - first) / rate, before=True)
    if p0 is None or p1 is None or p1["t"] < p0["t"] or p0["t"] - (start - first) / rate > p["track_step_seconds"] + 1e-9:
        out["label"], out["detail"] = classify(None, None, None, None, None, cfg)
        return _finish(out)
    out["move"] = round(math.hypot(p1["X"] - p0["X"], p1["Y"] - p0["Y"]), 1)
    out["height"] = round(float(p1["Z"] - p0["Z"]), 1)
    if progress is not None:
        value = progress((p0["X"], p0["Y"], p0["Z"]), (p1["X"], p1["Y"], p1["Z"]))
        out["t_progress"] = round(value, 1) if value is not None else None
    if smokes_at is not None:
        out["own_smoke_share"] = own_smoke_share(track, (start - first) / rate, (stop - first) / rate, smokes_at,
                                                 float(p["own_smoke_radius"]))
    if maps is not None:
        d0 = maps.ratio(direction, p0["X"], p0["Y"], exclude=facts["match_id"])
        d1 = maps.ratio(direction, p1["X"], p1["Y"], exclude=facts["match_id"])
        out["danger_start"] = round(d0, 3) if d0 is not None else None
        out["danger_end"] = round(d1, 3) if d1 is not None else None
    sources = None
    if threat is not None:
        found = threat((p0["X"], p0["Y"], p0["Z"]), (p1["X"], p1["Y"], p1["Z"]), start, stop)
        t0, t1, count = found[:3]
        extra = found[3] if len(found) > 3 else None
        if isinstance(extra, dict):  # 第 7 版：优先用"准星对着敌人出现的拐角 / 门口"
            sources = extra.get("sources")
            out["smoke_share"] = extra.get("smoke_share")
            if extra.get("facing") is not None:
                out["facing_share"] = extra["facing"](track, (start - first) / rate, (stop - first) / rate)
        else:
            sources = extra
        out.update(threat_start=round(t0, 3), threat_end=round(t1, 3), threat_count=count)
    if out.get("facing_share") is None and sources is not None:
        out["facing_share"] = facing_share(track, (start - first) / rate, (stop - first) / rate, sources, cfg)
    target = fight["last_kill"]
    tx, ty = (target["enemy_x"], target["enemy_y"]) if target is not None else (facts.get("fk_victim_x"), facts.get("fk_victim_y"))
    if tx is not None and not pd.isna(tx) and pd.notna(p1["yaw"]):
        toward = math.degrees(math.atan2(ty - p1["Y"], tx - p1["X"]))
        out["view_toward_kill"] = abs((float(p1["yaw"]) - toward + 180) % 360 - 180) < p["view_toward_max_angle"]
    if seek:
        out["label"], out["detail"] = classify_seek(out["move"], out["height"], out["threat_start"], out["threat_end"],
                                                    out["observed_s"], out["facing_share"], cfg)
    elif p.get("danger_method", "map") == "threat":
        d0, d1 = (out["threat_start"], out["threat_end"]) if out["threat_count"] else (None, None)  # 没有已知威胁：判不出
        out["label"], out["detail"] = classify(out["move"], out["height"], d0, d1, out["observed_s"], cfg, "threat",
                                               out.get("smoke_share"), out["own_smoke_share"], out["t_progress"])
    else:
        out["label"], out["detail"] = classify(out["move"], out["height"], out["danger_start"], out["danger_end"],
                                               out["observed_s"], cfg)
    return _finish(out)


def _finish(out: dict) -> dict:
    out["stance"] = STANCE[out["label"]]
    out["behaviour"] = LABELS[out["label"]]
    return out
