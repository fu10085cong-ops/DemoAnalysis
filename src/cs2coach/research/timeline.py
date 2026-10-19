"""R03 交给 AI 的选手视角时间线：只写这名选手当时能看到、听到、从雷达和 HUD 知道的信息。

看到 = 几何视野（扣除烟雾和被闪）；雷达 = 本队任一人发现（spotted_by_mask）；听到 = player_sound 事件在其
radius 内（脚步等，游戏自己的可听范围），枪声按配置的距离近似。没看到、没听到的敌人位置和回合结果都不写。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from cs2coach.parsing.match import MatchData
from cs2coach.research.fight import LABELS, Reposition
from cs2coach.research.labels import _place
from cs2coach.research.windows import _seen

TEAM = {2: "T", 3: "CT"}
LAYER = {"deep": "后方", "hold": "防守区", "contact": "接触区"}


def landmarks(match: MatchData) -> dict[str, tuple[float, float]]:
    """每个报点的代表位置：本场所有存活玩家在该报点时坐标的中位数。"""
    t = match.ticks
    t = t[t["is_alive"].astype(bool) & t["last_place_name"].notna() & t["X"].notna()]
    grouped = t.groupby(t["last_place_name"].astype(str), observed=True)[["X", "Y"]].median()
    return {str(name): (float(row.X), float(row.Y)) for name, row in grouped.iterrows()}


def look_target(state, marks: dict[str, tuple[float, float]], cfg: dict) -> str | None:
    """视线水平方向上夹角最小的报点（夹角与距离都在配置范围内）。"""
    limits = cfg["research"]["timeline"]
    if state is None or pd.isna(state["yaw"]) or pd.isna(state["X"]):
        return None
    x, y, yaw = float(state["X"]), float(state["Y"]), float(state["yaw"])
    own = str(state["last_place_name"]) if pd.notna(state["last_place_name"]) else None
    best = None
    for name, (px, py) in marks.items():
        dist = math.hypot(px - x, py - y)
        if name == own or dist < 150 or dist > limits["look_max_distance"]:
            continue
        angle = abs((math.degrees(math.atan2(py - y, px - x)) - yaw + 180) % 360 - 180)
        if angle <= limits["look_max_angle"] and (best is None or angle < best[0]):
            best = (angle, name)
    return best[1] if best else None


def place_text(state, cfg: dict, map_name: str) -> str:
    if state is None or pd.isna(state["last_place_name"]):
        return "未知位置"
    name = str(state["last_place_name"])
    floors = cfg["research"].get("floors", {}).get(map_name, {})
    point = (float(state["X"]), float(state["Y"]), float(state["Z"])) if pd.notna(state["Z"]) else None
    split = _place(name, point, floors)
    if split != name:
        name = f"{name}{'楼下' if split.endswith('/lower') else '楼上'}"
    return name


def role_text(place: str | None, position, cfg: dict, map_name: str) -> str:
    research = cfg["research"]
    layer = LAYER.get(research["regions"].get(map_name, {}).get(place or "", ""), "未分层")
    split = _place(place, position, research.get("floors", {}).get(map_name, {}))
    zones = [name for name, places in research.get("zones", {}).get(map_name, {}).items() if split in places]
    return f"{layer}" + (f"，防守区组 {'/'.join(zones)}" if zones else "")


def build_timeline(match: MatchData, window: dict, result: Reposition, sight, sounds: pd.DataFrame,
                   pings: pd.DataFrame, cfg: dict) -> str:
    """window：研究表的一行（player_id、round_id、start_tick、start_place、first_kill_by 等）。"""
    limits = cfg["research"]["timeline"]
    rate = match.meta.tick_rate
    sid, trigger, round_id = int(window["player_id"]), int(window["start_tick"]), int(window["round_id"])
    map_name = match.meta.map_name
    me0 = match.state_at(sid, trigger)
    team = int(me0["team_num"])
    rnd = match.round(round_id)
    start = max(rnd.freeze_end_tick or rnd.start_tick, trigger - round(limits["lead_seconds"] * rate))
    end = result.observe_end if result.observe_end is not None else trigger + round(cfg["research"]["window_seconds"] * rate)
    end = min(max(end, trigger + rate), rnd.end_tick)
    step = max(1, round(limits["step_seconds"] * rate))
    marks = landmarks(match)
    roster = {s: int(st["team_num"]) for s in match.players
              if (st := match.state_at(s, trigger)) is not None and pd.notna(st["team_num"])}
    mates = [s for s, t in roster.items() if t == team and s != sid]
    enemies = [s for s, t in roster.items() if t not in (team, 0, 1)]
    team_mask = 0
    for s in [sid, *mates]:
        bit = match.spotted_bit(s)
        if bit is not None:
            team_mask |= 1 << bit
    rel = lambda tick: f"{(tick - trigger) / rate:+.1f}s"  # noqa: E731
    position = (float(me0["X"]), float(me0["Y"]), float(me0["Z"]))
    fight = result.fight
    lines = [
        f"地图 {map_name}。你是 CT 方的 {match.name_of(sid)}。时间以 T 方第一个人阵亡为 0 秒。",
        f"T 阵亡时你在 {place_text(me0, cfg, map_name)}（{role_text(window.get('start_place'), position, cfg, map_name)}）。"
        f"第一个人头是{'你自己' if window.get('first_kill_by') == 'self' else '队友'}拿的。局势变为 CT 5 对 T 4，"
        f"冻结期结束后第 {float(window.get('round_time_s') or 0):.0f} 秒。",
        "信息范围：只列你当时能看到（视线内、不隔烟、没被闪白）、雷达上能看到、听到（脚步等按游戏的可听范围；"
        "枪声按距离近似）的信息，以及击杀信息、队友位置、队友标点。语音沟通未知；墙体隔音未计。",
    ]
    if fight is not None:
        lines.append(f"程序判断的交火段：{rel(fight.start_tick)} 到 {rel(fight.end_tick)}（以你的开枪、命中、受伤、击杀计，"
                     f"间隔不超过 {cfg['research']['fight']['gap_seconds']} 秒的连成一段）。你可以不同意。")
    lines.append("")
    p0 = match.state_at(sid, fight.end_tick if fight else trigger)
    anchor = (float(p0["X"]), float(p0["Y"])) if p0 is not None and pd.notna(p0["X"]) else position[:2]
    anchor_name = "交火结束时位置" if fight else "0 秒时位置"
    prev = start - 1
    ticks = list(range(start, end + 1, step))
    if ticks[-1] != end:
        ticks.append(end)
    for tick in ticks:
        me = match.state_at(sid, tick)
        if me is None or not bool(me["is_alive"]):
            lines.append(f"{rel(tick)} 你已阵亡。")
            break
        parts = [_self_text(me, tick, anchor, anchor_name, marks, match, sid, cfg, map_name)]
        seen = {}
        for enemy in enemies:
            st = match.state_at(enemy, tick)
            if sight is not None and st is not None and bool(st["is_alive"]) and _seen(sight, me, st, tick):
                seen[enemy] = f"1 名 T 在 {place_text(st, cfg, map_name)} 约 {_dist(me, st):.0f}"
        if seen:
            parts.append("看到：" + "；".join(seen.values()))
        radar = []
        for enemy in enemies:
            st = match.state_at(enemy, tick)
            if st is None or not bool(st["is_alive"]) or enemy in seen:
                continue
            if pd.notna(st.get("spotted_by_mask")) and int(st["spotted_by_mask"]) & team_mask:
                radar.append(f"{place_text(st, cfg, map_name)}")
        if radar:
            parts.append("雷达上有 T：" + "、".join(radar))
        heard = _heard(match, me, sid, team, prev, tick, sounds, enemies, cfg, map_name)
        if heard:
            parts.append("听到：" + "；".join(heard))
        events = _events(match, sid, prev, tick, pings, mates, marks, cfg, map_name)
        if events:
            parts.append("事件：" + "；".join(events))
        if (tick - start) % round(limits["teammate_every_seconds"] * rate) < step:
            alive = []
            for mate in mates:
                st = match.state_at(mate, tick)
                if st is not None and bool(st["is_alive"]):
                    alive.append(f"{match.name_of(mate)} {place_text(st, cfg, map_name)} {int(st['health'])}血")
            parts.append("队友：" + ("、".join(alive) if alive else "无存活"))
        marker = ""
        if fight is not None and prev < fight.end_tick <= tick:
            marker = " 【程序判断的交火结束】"
        lines.append(f"{rel(tick)}{marker} " + " | ".join(parts))
        prev = tick
    return "\n".join(lines)


def _self_text(me, tick, anchor, anchor_name, marks, match, sid, cfg, map_name) -> str:
    limits = cfg["research"]["timeline"]
    speed = float(me["speed_xy"]) if pd.notna(me.get("speed_xy")) else 0.0
    if speed < limits["moving_speed"]:
        move = "静止"
    elif bool(me.get("is_walking")):
        move = "静步"
    elif pd.notna(me.get("duck_amount")) and float(me["duck_amount"]) > 0.5:
        move = "蹲走"
    else:
        move = "跑" if speed >= limits["running_speed"] else "走"
    if pd.notna(me.get("duck_amount")) and float(me["duck_amount"]) > 0.5 and move == "静止":
        move = "蹲着"
    look = look_target(me, marks, cfg)
    weapon = me.get("active_weapon_name")
    ammo = me.get("active_weapon_ammo")
    gun = f"{weapon}" + (f"（{int(ammo)} 发）" if pd.notna(ammo) else "") if isinstance(weapon, str) else "武器未知"
    flags = []
    if bool(me.get("is_scoped")):
        flags.append("开镜")
    if _blinded(match, sid, tick):
        flags.append("被闪白")
    away = math.hypot(float(me["X"]) - anchor[0], float(me["Y"]) - anchor[1])
    return (f"你 在 {place_text(me, cfg, map_name)}，离{anchor_name} {away:.0f}，{move}，"
            f"看向 {look or '（无对应报点）'}，{int(me['health'])}血/{int(me['armor_value'])}甲，{gun}"
            + (f"，{'、'.join(flags)}" if flags else ""))


def _heard(match, me, sid, team, prev, tick, sounds, enemies, cfg, map_name) -> list[str]:
    limits = cfg["research"]["timeline"]
    out = []
    here = (float(me["X"]), float(me["Y"]), float(me["Z"]))
    if not sounds.empty:
        part = sounds[(sounds["tick"] > prev) & (sounds["tick"] <= tick) & sounds["steamid"].isin(enemies)]
        for (enemy, step), rows in part.groupby(["steamid", "step"]):
            row = rows.iloc[-1]
            dist = math.dist(here, (row["x"], row["y"], row["z"]))
            if dist <= float(row["radius"]):
                st = match.state_at(int(enemy), int(row["tick"]))
                out.append(f"{'脚步' if step else '声音'} {place_text(st, cfg, map_name)}方向 约 {dist:.0f}")
    shots = match.shots
    if not shots.empty:
        part = shots[(shots["tick"] > prev) & (shots["tick"] <= tick) & shots["steamid"].isin(enemies) & shots["is_gun"]]
        for enemy, rows in part.groupby("steamid"):
            st = match.state_at(int(enemy), int(rows["tick"].iloc[-1]))
            if st is None or pd.isna(st["X"]):
                continue
            silenced = rows["weapon"].iloc[-1] in limits["silenced_weapons"]
            radius = limits["silenced_gunshot_radius"] if silenced else limits["gunshot_radius"]
            dist = math.dist(here, (float(st["X"]), float(st["Y"]), float(st["Z"])))
            if dist <= radius:
                out.append(f"枪声 {place_text(st, cfg, map_name)}方向 约 {dist:.0f}（{len(rows)} 发）")
    return out


def _events(match, sid, prev, tick, pings, mates, marks, cfg, map_name) -> list[str]:
    out = []
    k = match.kills
    if not k.empty:
        for row in k[(k["tick"] > prev) & (k["tick"] <= tick)].itertuples():
            killer = match.name_of(int(row.attacker)) if pd.notna(row.attacker) else "（环境）"
            victim = int(row.victim) if pd.notna(row.victim) else None
            text = f"击杀信息：{killer} 用 {row.weapon} 击杀 {match.name_of(victim) if victim else '?'}"
            if victim in mates:
                st = match.state_at(victim, int(row.tick) - 1)
                text += f"（队友，阵亡在 {place_text(st, cfg, map_name)}）"
            out.append(text)
    shots = match.shots
    if not shots.empty:
        own = shots[(shots["tick"] > prev) & (shots["tick"] <= tick) & (shots["steamid"] == sid) & shots["is_gun"]]
        if len(own):
            out.append(f"你开了 {len(own)} 枪")
    d = match.damages
    if not d.empty:
        part = d[(d["tick"] > prev) & (d["tick"] <= tick)]
        for row in part[part["attacker"] == sid].itertuples():
            if pd.notna(row.victim) and int(row.victim) not in mates:
                out.append(f"你命中 {match.name_of(int(row.victim))} {int(row.dmg_effective)} 伤害")
        for row in part[part["victim"] == sid].itertuples():
            src = match.state_at(int(row.attacker), int(row.tick)) if pd.notna(row.attacker) else None
            out.append(f"你受到 {int(row.dmg_effective)} 伤害，来自 {place_text(src, cfg, map_name)}方向")
    if not pings.empty:
        part = pings[(pings["tick"] > prev) & (pings["tick"] <= tick) & pings["steamid"].isin(mates)]
        for row in part.itertuples():
            out.append(f"队友 {match.name_of(int(row.steamid))} 在地图上标点（{_nearest_place(marks, row.x, row.y)}）")
    b = match.bomb
    if not b.empty:
        planted = b[(b["tick"] > prev) & (b["tick"] <= tick) & (b["event"] == "planted")]
        if len(planted):
            out.append("炸弹已安放")
    return out


def _nearest_place(marks: dict[str, tuple[float, float]], x: float, y: float) -> str:
    return min(marks, key=lambda n: math.hypot(marks[n][0] - x, marks[n][1] - y)) if marks else "未知"


def _blinded(match: MatchData, sid: int, tick: int) -> bool:
    b = match.blinds
    if b.empty:
        return False
    rate = match.meta.tick_rate
    hit = b[(b["victim"] == sid) & (b["tick"] <= tick)]
    return bool(((tick - hit["tick"]) / rate < hit["duration"]).any())


def _dist(a, b) -> float:
    return float(np.hypot(float(a["X"]) - float(b["X"]), float(a["Y"]) - float(b["Y"])))


def rule_text(result: Reposition) -> str:
    return LABELS[result.label]
