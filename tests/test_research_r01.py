"""R01 的合成比赛验收用例。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from cs2coach.config import load_config
from cs2coach.features.visibility import Sight
from cs2coach.parsing.match import build_match
from cs2coach.parsing.models import MatchMeta, Team
from cs2coach.research.labels import label_window
from cs2coach.research.models import BehaviourWindow
from cs2coach.research.triggers import ct_first_man_advantage, find_situations
from cs2coach.research.windows import behaviour_windows
from tests.helpers.synthetic import WallChecker, make_round, make_ticks

CFG = load_config()
T = list(range(76561190000000010, 76561190000000015))
CT = list(range(76561190000000020, 76561190000000025))


def synthetic_match(kill_rows: list[tuple[int, int, int]], moves: dict | None = None, winner=Team.CT):
    """用 synthetic.make_ticks 的逐 tick 字段构成完整 5v5。"""
    template = make_ticks(first=1, last=500)
    base = template[template["steamid"] == template["steamid"].iloc[0]].copy()
    frames = []
    for sid in T + CT:
        frame = base.copy()
        frame["steamid"] = sid
        frame["team_num"] = 2 if sid in T else 3
        frame["X"] = 1000.0 if sid in T else 0.0
        for tick, change in (moves or {}).get(sid, {}).items():
            frame.loc[frame["tick"] >= tick, list(change)] = list(change.values())
        for death_tick, _, victim in kill_rows:
            if victim == sid:
                frame.loc[frame["tick"] >= death_tick, "is_alive"] = False
        frames.append(frame)
    ticks = pd.concat(frames).sort_values(["steamid", "tick"]).reset_index(drop=True)
    rnd = make_round(freeze_end=10, end=450, last=500, winner=winner)
    meta = MatchMeta(map_name="de_mirage", server_name="test", patch_version="0", tick_rate=64, last_tick=500)
    match = build_match(Path("example.dem"), meta, {sid: f"player{sid}" for sid in T + CT}, [rnd], ticks, 0.95)
    rows = []
    for tick, attacker, victim in kill_rows:
        rows.append(dict(tick=tick, round=1, attacker=attacker, victim=victim,
                         is_enemy_kill=attacker in CT and victim in T or attacker in T and victim in CT,
                         in_freeze_time=tick < 10, after_round_end=False))
    match.kills = pd.DataFrame(rows, columns=["tick", "round", "attacker", "victim", "is_enemy_kill",
                                               "in_freeze_time", "after_round_end"])
    return match


class LineNav:
    def find_area(self, point):
        return None if point[0] < -900 else int(point[0] // 100)

    def find_path(self, start, end, *, weight="distance"):
        a, b = self.find_area(start), self.find_area(end)
        return list(range(a, b + 1)) if a <= b else list(range(a, b - 1, -1))

    def area(self, number):
        return {"centroid": (number * 100.0 + 50, 0.0, 0.0)}


@pytest.mark.parametrize("kills, expected, reason", [
    ([(100, CT[0], T[0])], 1, None),
    ([(100, T[0], CT[0])], 0, "first_enemy_kill_ct"),
    ([(90, T[0], CT[0]), (100, CT[1], T[0])], 0, "first_enemy_kill_ct"),
    ([(5, CT[0], T[0]), (100, CT[1], T[1])], 0, "not_5v5_before_first_enemy_kill"),
    ([(50, CT[0], CT[1]), (100, CT[2], T[0])], 0, "not_5v5_before_first_enemy_kill"),
])
def test_trigger(kills, expected, reason):
    match = synthetic_match(kills)
    found, skipped = find_situations(match)
    assert len(ct_first_man_advantage(match, CFG)) == expected
    assert len(found) == expected
    if reason:
        assert skipped[reason] == 1


def test_disconnect_before_first_kill_does_not_trigger():
    match = synthetic_match([(100, CT[0], T[0])], {CT[1]: {50: {"team_num": 0, "is_alive": False}}})
    found, skipped = find_situations(match)
    assert found == []
    assert skipped["not_5v5_before_first_enemy_kill"] == 1


def test_windows_death_navigation_and_outcomes():
    match = synthetic_match([(100, CT[0], T[0]), (200, T[1], CT[1])],
                            {CT[0]: {150: {"X": 400.0}}, CT[1]: {170: {"X": -1000.0}}})
    situation = ct_first_man_advantage(match, CFG)[0]
    sight = Sight(match, CFG, checker=WallChecker(wall_x=500, wall_y_max=-100))
    rows = behaviour_windows(match, situation, sight, LineNav(), CFG)
    assert len(rows) == 5
    by_id = {row.player_id: row for row in rows}
    assert by_id[str(CT[0])].nav_delta < 0
    assert by_id[str(CT[0])].main_label == "advance"
    assert by_id[str(CT[1])].end_tick == 200
    assert by_id[str(CT[1])].death_tick == 200
    assert by_id[str(CT[1])].nav_delta is None
    assert by_id[str(CT[1])].main_label == "unknown"
    assert by_id[str(CT[1])].advantage_held is False
    assert by_id[str(CT[0])].advantage_held is False
    assert all(row.ct_won is True for row in rows)
    held = synthetic_match([(100, CT[0], T[0])])
    held_rows = behaviour_windows(held, ct_first_man_advantage(held, CFG)[0],
                                  Sight(held, CFG, checker=WallChecker()), LineNav(), CFG)
    assert all(row.advantage_held is True for row in held_rows)


def test_enemy_information_requires_sight_or_radar():
    match = synthetic_match([(100, CT[0], T[0])], {T[1]: {10: {"X": 200.0}}})
    situation = ct_first_man_advantage(match, CFG)[0]
    sight = Sight(match, CFG, checker=WallChecker(wall_x=100, wall_y_max=100))
    rows = behaviour_windows(match, situation, sight, LineNav(), CFG)
    own = next(row for row in rows if row.player_id == str(CT[0]))
    assert own.visible_enemies_trigger == 0
    assert own.visible_enemies_window == 0
    assert own.radar_enemies_window == 0


def sample_window(**changes):
    values = dict(demo_id="a.dem", match_id="a", round_id=1, tick=100, map="de_mirage",
                  alive_ct=5, alive_t=4, player_id="1", player_name="test", tick_rate=64, start_tick=100,
                  end_tick=420, jump_command="demo_gototick 0", displacement=0.0, nav_delta=0.0)
    values.update(changes)
    return BehaviourWindow(**values)


@pytest.mark.parametrize("changes, expected", [
    ({}, "hold"),
    ({"displacement": 400, "nav_delta": -400}, "advance"),
    ({"displacement": 400, "nav_delta": 400}, "retreat"),
    ({"displacement": 400, "nav_delta": 10}, "lateral"),
    ({"nav_delta": None}, "unknown"),
    ({"died": True, "end_tick": 130}, "unknown"),
])
def test_labels(changes, expected):
    main, _, confidence = label_window(sample_window(**changes), CFG)
    assert main == expected
    assert (confidence == 0) == (expected == "unknown")


def test_auxiliary_labels_combine():
    row = sample_window(utility_types=["flashbang"], teammate_distance_start=1000,
                        teammate_distance_end=500, shots=2, damage_taken=10)
    assert label_window(row, CFG)[1] == ["utility", "regroup", "engaged"]


class SnapNav(LineNav):
    """-900 以西不在任何区域内；区域中心表供"退回最近区域"使用。"""
    areas = pd.DataFrame({"centroid_x": [-850.0, 50.0], "centroid_y": [0.0, 0.0], "centroid_z": [0.0, 0.0]})

    def find_area(self, point):
        return None if point[0] < -900 else int((point[0] + 1000) // 100)

    def find_path(self, start, end, *, weight="distance"):
        return [self.find_area(start), self.find_area(end)]

    def area(self, number):
        return {"centroid": (number * 100.0 - 950, 0.0, 0.0)}


def test_nav_distance_snaps_to_nearest_area_within_limit():
    # R01 审查：站在箱子上、高度偏差时玩家不在任何导航区域内，退回最近区域中心并计入这段直线距离
    from cs2coach.research.windows import _nav_distance
    nav = SnapNav()
    goal = (50.0, 0.0, 0.0)
    on_mesh = _nav_distance(nav, (-850.0, 0.0, 0.0), goal, 128)
    snapped = _nav_distance(nav, (-950.0, 0.0, 0.0), goal, 128)  # 离最近区域中心 100
    assert snapped is not None and snapped == pytest.approx(on_mesh + 100.0)
    assert _nav_distance(nav, (-1100.0, 0.0, 0.0), goal, 128) is None  # 离最近区域中心 250，超过上限
    assert _nav_distance(nav, (-950.0, 0.0, 0.0), goal, 0) is None  # 关闭退回
    assert _nav_distance(LineNav(), (-950.0, 0.0, 0.0), goal, 128) is None  # 没有区域表的导航对象照旧
