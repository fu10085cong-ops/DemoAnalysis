"""整场的枪法统计，名称和口径沿用 Leetify（docs/metrics_research.md）。

与 Leetify 的差别（同时写在 docs/features.md）：
- "看见敌人"用地图几何视野（features/visibility.py），Leetify 用自己的位置重放（方法未公开）
- Time to Damage、Crosshair Placement 按"遭遇"统计：同一回合对同一敌人的伤害，间隔超过 encounter_gap_seconds
  才算新的一次遭遇，每次遭遇取第一次伤害
"""

from __future__ import annotations

import statistics
from typing import Any

import numpy as np
import pandas as pd

from cs2coach.features.models import AimStats, Moment
from cs2coach.features.visibility import Sight, angle_between
from cs2coach.parsing.match import MatchData
from cs2coach.parsing.weapons import RIFLES, is_gun


def aim_stats(match: MatchData, sid: int, cfg: dict[str, Any], shots: pd.DataFrame, sight: Sight | None,
              moments: list[Moment]) -> AimStats | None:
    if sight is None or not sight.available:
        return None
    ac, cs = cfg["aim_stats"], cfg["counter_strafe"]
    rate = match.meta.tick_rate
    min_n = int(ac["min_samples"])

    ttd, cp = _encounters(match, sid, sight, ac, rate)

    mine = shots[(shots["steamid"] == sid).fillna(False) & shots["is_gun"] & ~shots["controlling_bot"]]
    mine = mine.sort_values("tick")
    hit_ticks = _hit_ticks(match, sid)
    states = match.player_ticks(sid).set_index("tick")
    mine = mine[mine["tick"].isin(states.index)]
    # 取前一 tick：被这一枪打死的人在开枪那一 tick 已经是死亡状态
    seen_flags = sight.enemies_seen(sid, [int(t) - 1 for t in mine["tick"]])
    rows = []
    for r, seen in zip(mine.itertuples(), seen_flags):
        t = int(r.tick)
        st = states.loc[t]
        speed = _float(st["speed_xy"])
        scoped = bool(st["is_scoped"])
        mx = (cs["max_speed_scoped"].get(r.weapon) if scoped else None) or cs["max_speed"].get(r.weapon)
        rows.append({"tick": t, "weapon": r.weapon, "seen": seen, "hit": t in hit_ticks,
                     "crouched": (_float(st["duck_amount"]) or 0.0) >= cs["crouch_duck_amount"],
                     "stopped": (speed / mx < cs["accurate_speed_ratio"]) if speed is not None and mx else None})
    sh = pd.DataFrame(rows, columns=["tick", "weapon", "seen", "hit", "crouched", "stopped"])

    seen = sh[sh["seen"]]
    rifle_cs = seen[seen["weapon"].isin(RIFLES) & ~seen["crouched"] & seen["stopped"].notna()]
    spray = _spray_shots(sh, rate, ac)
    spray = spray[spray["seen"] & spray["weapon"].isin(RIFLES)]

    d = match.damages
    gun_hits = d[(d["attacker"] == sid).fillna(False) & (d["attacker_team_num"] != d["victim_team_num"])
                 & d["weapon"].map(is_gun) & (d["weapon"] != "awp")]
    return AimStats(
        time_to_damage_ms=round(statistics.median(ttd) * 1000) if len(ttd) >= min_n else None,
        time_to_damage_samples=len(ttd),
        crosshair_placement_deg=round(statistics.median(cp), 2) if len(cp) >= min_n else None,
        crosshair_placement_samples=len(cp),
        counter_strafing_pct=_pct(rifle_cs["stopped"].astype(bool).sum(), len(rifle_cs), min_n),
        counter_strafing_shots=len(rifle_cs),
        spray_accuracy_pct=_pct(spray["hit"].sum(), len(spray), min_n),
        spray_shots=len(spray),
        spotted_accuracy_pct=_pct(seen["hit"].sum(), len(seen), min_n),
        spotted_shots=len(seen),
        headshot_accuracy_pct=_pct((gun_hits["hitgroup"] == "head").sum(), len(gun_hits), min_n),
        hits=len(gun_hits),
    )


def _encounters(match: MatchData, sid: int, sight: Sight, ac: dict, rate: int) -> tuple[list[float], list[float]]:
    """每次遭遇的 Time to Damage（秒）和 Crosshair Placement（度）。≥ time_to_damage_max_seconds 的排除。"""
    d = match.damages
    mine = d[(d["attacker"] == sid).fillna(False) & d["victim"].notna() & (d["attacker_team_num"] != d["victim_team_num"])
             & d["weapon"].map(is_gun)].sort_values("tick")
    gap = round(ac["encounter_gap_seconds"] * rate)
    max_ttd = ac["time_to_damage_max_seconds"]
    ttd, cp = [], []
    last: dict[tuple[int, int], int] = {}
    for r in mine.itertuples():
        key = (int(r.round), int(r.victim))
        t = int(r.tick)
        prev = last.get(key)
        last[key] = t
        if prev is not None and t - prev <= gap:
            continue  # 同一次遭遇里的后续伤害
        rnd = match.round(int(r.round))
        floor = rnd.freeze_end_tick if rnd.freeze_end_tick is not None else rnd.start_tick
        fs = sight.first_seen(sid, int(r.victim), t, floor)
        if fs.tick is None or fs.smoke:
            continue
        x = (t - fs.tick) / rate
        if x >= max_ttd:
            continue  # Leetify 口径：1 秒以上视为有意等待（如架点），不计入
        ttd.append(x)
        angle = angle_between(sight.view_vector(sid, fs.tick), sight.view_vector(sid, t))
        if angle is not None:
            cp.append(angle)
    return ttd, cp


def _hit_ticks(match: MatchData, sid: int) -> set[int]:
    d = match.damages
    hits = d[(d["attacker"] == sid).fillna(False) & (d["attacker_team_num"] != d["victim_team_num"]) & d["weapon"].map(is_gun)]
    return set(hits["tick"].astype(int))


def _spray_shots(sh: pd.DataFrame, rate: int, ac: dict) -> pd.DataFrame:
    """属于扫射的射击：同一把武器、相邻两枪间隔不超过 spray_max_gap_seconds、连续 spray_min_shots 发以上。"""
    if sh.empty:
        return sh
    gap = ac["spray_max_gap_seconds"] * rate
    t = sh["tick"].to_numpy()
    w = sh["weapon"].to_numpy()
    new_run = np.ones(len(sh), dtype=bool)
    new_run[1:] = (np.diff(t) > gap) | (w[1:] != w[:-1])
    run_id = np.cumsum(new_run)
    sizes = pd.Series(run_id).map(pd.Series(run_id).value_counts()).to_numpy()
    return sh[sizes >= ac["spray_min_shots"]]


def _pct(num: float, den: int, min_n: int) -> float | None:
    return round(100.0 * float(num) / den, 1) if den >= min_n else None


def _float(v: object) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f
