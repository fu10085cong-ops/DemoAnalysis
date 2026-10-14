"""构造合成的 MatchData，供单元测试使用（不需要真实 demo）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from cs2coach.parsing.match import MatchData, build_match, horizontal_speed
from cs2coach.parsing.models import MatchMeta, Round, Team

TICK_RATE = 64

A, B, C, D = 76561198000000001, 76561198000000002, 76561198000000003, 76561198000000004
NAMES = {A: "alice", B: "bob", C: "carol", D: "dave"}
# A、B 是 T；C、D 是 CT
TEAMS = {A: Team.T, B: Team.T, C: Team.CT, D: Team.CT}

_DEFAULTS = {
    "X": 0.0, "Y": 0.0, "Z": 0.0, "pitch": 0.0, "yaw": 0.0,
    "health": 100, "armor_value": 100, "is_alive": True,
    "is_airborne": False, "is_walking": False, "duck_amount": 0.0, "is_scoped": False,
    "active_weapon_name": "AK-47", "flash_duration": 0.0, "last_place_name": "Mid",
    "spotted_by_mask": 0, "controlling_bot": False,
}


def make_round(number: int = 1, start: int = 1, freeze_end: int | None = 10, end: int = 100,
               last: int = 120, winner: Team = Team.T, reason: int = 9) -> Round:
    return Round(number=number, start_tick=start, freeze_end_tick=freeze_end, end_tick=end,
                 last_tick=last, winner=winner, reason=reason)


def make_ticks(first: int = 1, last: int = 120, overrides: dict | None = None) -> pd.DataFrame:
    """所有玩家在 [first, last] 每个 tick 一行，取默认值；overrides[(steamid, tick)] = {列: 值} 覆盖单个格子。"""
    rows = []
    for sid in NAMES:
        for t in range(first, last + 1):
            row = {"tick": t, "steamid": sid, **_DEFAULTS, "team_num": int(TEAMS[sid])}
            row.update((overrides or {}).get((sid, t), {}))
            rows.append(row)
    df = pd.DataFrame(rows).sort_values(["steamid", "tick"]).reset_index(drop=True)
    df["steamid"] = df["steamid"].astype("int64")
    df["X"] = df["X"].astype("float32")
    df["speed_xy"] = horizontal_speed(df, TICK_RATE)
    return df


def make_match(ticks: pd.DataFrame | None = None, rounds: list[Round] | None = None,
               min_coverage: float = 0.95) -> MatchData:
    ticks = make_ticks() if ticks is None else ticks
    rounds = [make_round()] if rounds is None else rounds
    meta = MatchMeta(map_name="de_test", server_name="test", patch_version="0",
                     tick_rate=TICK_RATE, last_tick=int(ticks["tick"].max()))
    return build_match(Path("test.dem"), meta, dict(NAMES), rounds, ticks, min_coverage)


def sid(v: int | None) -> object:
    """模拟事件中的 steamid：字符串，缺失时为 NaN（陷阱 5）。"""
    return str(v) if v is not None else np.nan


class AlwaysVisible:
    """假的视线检测：没有任何遮挡（测试用，不需要地图数据）。"""

    def is_visible(self, a, b) -> bool:
        return True


class WallChecker:
    """假的视线检测：x = wall_x 处有一堵墙，覆盖 y < wall_y_max 的部分。"""

    def __init__(self, wall_x: float = 500.0, wall_y_max: float = -100.0):
        self.wall_x, self.wall_y_max = wall_x, wall_y_max

    def is_visible(self, a, b) -> bool:
        (ax, ay, _), (bx, by, _) = a, b
        if (ax - self.wall_x) * (bx - self.wall_x) > 0 or ax == bx:
            return True  # 两点在墙的同一侧
        y = ay + (by - ay) * (self.wall_x - ax) / (bx - ax)
        return y >= self.wall_y_max
