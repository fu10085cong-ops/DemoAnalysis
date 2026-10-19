"""R03 交战后换位：交火段、规则基线，以及选手视角时间线不泄露上帝视角信息。"""

from __future__ import annotations

import pandas as pd
import pytest

from cs2coach.features.visibility import Sight
from cs2coach.research.fight import find_fight, fight_events, reposition
from cs2coach.research.timeline import build_timeline
from tests.helpers.synthetic import WallChecker
from tests.test_research_r01 import CFG, CT, T, synthetic_match

RATE = 64


def fight_match(moves=None, extra_kills=(), shots=(90, 95, 100)):
    match = synthetic_match([(100, CT[0], T[0]), *extra_kills], moves)
    match.kills["weapon"] = "ak47"
    match.shots = pd.DataFrame([dict(tick=t, steamid=CT[0], weapon="ak47", is_gun=True) for t in shots],
                               columns=["tick", "steamid", "weapon", "is_gun"])
    match.damages = pd.DataFrame([dict(tick=100, round=1, attacker=CT[0], victim=T[0], weapon="ak47", dmg_effective=100)],
                                 columns=["tick", "round", "attacker", "victim", "weapon", "dmg_effective"])
    return match


def test_find_fight_prefers_episode_containing_trigger():
    events = pd.DataFrame({"tick": [10, 20, 90, 100, 300], "kind": ["shot", "shot", "shot", "kill", "shot"],
                           "enemy": pd.Series([None, None, None, 5, None], dtype="Int64")})
    fight = find_fight(events, trigger=100, gap_ticks=RATE)
    assert (fight.start_tick, fight.end_tick, fight.opponent) == (90, 100, 5)
    later = find_fight(events, trigger=200, gap_ticks=RATE)
    assert (later.start_tick, later.end_tick) == (300, 300)


def test_fight_events_only_count_enemies():
    match = fight_match()
    events = fight_events(match, CT[0], 1, 0, 400)
    assert set(events["kind"]) == {"shot", "hit", "kill"}
    assert set(events["enemy"].dropna().astype(int)) == {T[0]}


@pytest.mark.parametrize("moves, expected", [
    ({}, "stay"),
    ({CT[0]: {200: {"X": -300.0}}}, "leave"),       # 离交火对手（X=1000）远了 300
    ({CT[0]: {200: {"X": 200.0}}}, "push"),         # 近了 200
    ({CT[0]: {200: {"Y": 150.0}}}, "move"),         # 横向挪了 150，距离几乎不变
])
def test_reposition_rule_baseline(moves, expected):
    result = reposition(fight_match(moves), CT[0], 1, 100, CFG)
    assert result.label == expected
    assert result.location_source == "opponent"
    assert result.observe_end == 100 + 3 * RATE


def test_reposition_unknown_when_killed_right_after_fight():
    match = fight_match(extra_kills=[(130, T[1], CT[0])])
    assert reposition(match, CT[0], 1, 100, CFG).label == "unknown"


def test_timeline_hides_enemies_not_seen_heard_or_on_radar():
    moves = {T[1]: {1: {"Y": -1000.0, "last_place_name": "SecretSpot"}},
             T[2]: {1: {"Y": -1000.0, "X": 1200.0, "last_place_name": "RadarSpot"}}}
    match = fight_match(moves)
    match.ticks["last_place_name"] = match.ticks["last_place_name"].astype(object)
    sight = Sight(match, CFG, checker=WallChecker(wall_x=500, wall_y_max=-100))
    window = dict(player_id=str(CT[0]), round_id=1, start_tick=100, start_place="Mid", first_kill_by="self", round_time_s=1.4)
    result = reposition(match, CT[0], 1, 100, CFG, sight=sight)
    no_sound = pd.DataFrame(columns=["tick", "steamid", "x", "y", "z", "radius", "step"])
    no_ping = pd.DataFrame(columns=["tick", "steamid", "x", "y", "z"])
    text = build_timeline(match, window, result, sight, no_sound, no_ping, CFG)
    assert "SecretSpot" not in text and "RadarSpot" not in text  # 墙后、没声音、没上雷达
    assert "程序判断的交火结束" in text and "你自己" in text
    # 雷达：队友 CT[1] 发现了 T[2]
    bit = match.spotted_bit(CT[1])
    match.ticks.loc[match.ticks["steamid"] == T[2], "spotted_by_mask"] = 1 << bit
    text = build_timeline(match, window, result, sight, no_sound, no_ping, CFG)
    assert "雷达上有 T：RadarSpot" in text and "SecretSpot" not in text
    # 声音：T[1] 的脚步在可听范围内才写
    far = pd.DataFrame([dict(tick=150, steamid=T[1], x=1000.0, y=-1000.0, z=0.0, radius=1100.0, step=True)])
    assert "SecretSpot" not in build_timeline(match, window, result, sight, far, no_ping, CFG)
    near = far.assign(radius=2000.0)
    assert "脚步 SecretSpot方向" in build_timeline(match, window, result, sight, near, no_ping, CFG)
