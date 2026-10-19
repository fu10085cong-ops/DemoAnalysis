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


A_SITE = {1: {"last_place_name": "BombsiteA"}}


@pytest.mark.parametrize("moves, expected", [
    ({CT[0]: A_SITE}, "stay"),
    ({CT[0]: {**A_SITE, 200: {"Y": 140.0}}}, "stay"),                      # 挪动 < 150
    ({CT[0]: {**A_SITE, 200: {"Y": 90.0, "Z": 45.0}}}, "move"),             # 爬上高处：高度变化 ≥ 40
    ({CT[0]: {**A_SITE, 200: {"X": -300.0}}}, "move"),                      # 往后挪也算换位（第 4 版没有撤离）
    ({CT[0]: {**A_SITE, 200: {"X": -300.0, "last_place_name": "CTSpawn"}}}, "move"),
    ({CT[0]: {**A_SITE, 200: {"Y": 200.0, "last_place_name": "Middle"}}}, "push"),  # 往前一层
    ({CT[0]: {**A_SITE, 200: {"X": 200.0}}}, "move"),                       # 没有导航网格时只按层级判断压
])
def test_reposition_rule_baseline(moves, expected):
    result = reposition(fight_match(moves), CT[0], 1, 100, CFG)
    assert result.label == expected
    assert result.location_source == "opponent"
    assert result.observe_end == 100 + 3 * RATE


def test_reposition_push_by_path_toward_t_spawn():
    from tests.test_research_r01 import LineNav

    near = reposition(fight_match({CT[0]: {**A_SITE, 200: {"X": 400.0}}}), CT[0], 1, 100, CFG, nav=LineNav())
    assert near.closer == pytest.approx(400, abs=1) and near.label == "push"  # 沿路线离 T 出生点近了 400
    short = reposition(fight_match({CT[0]: {**A_SITE, 200: {"X": 200.0}}}), CT[0], 1, 100, CFG, nav=LineNav())
    assert short.label == "move"  # 近了 200 < 300


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


def _window():
    return dict(player_id=str(CT[0]), round_id=1, start_tick=100, start_place="BombsiteA", first_kill_by="self",
                round_time_s=1.4)


def _silent():
    return (pd.DataFrame(columns=["tick", "steamid", "x", "y", "z", "radius", "step"]),
            pd.DataFrame(columns=["tick", "steamid", "x", "y", "z"]))


def test_timeline_marks_fight_end_and_observation_end_rows():
    match = fight_match({CT[0]: {**A_SITE, 200: {"Y": 150.0, "last_place_name": "Stairs"}}})
    result = reposition(match, CT[0], 1, 100, CFG)
    text = build_timeline(match, _window(), result, None, *_silent(), CFG)
    fight_rows = [line for line in text.splitlines() if "【程序判断的交火结束】" in line]
    close_rows = [line for line in text.splitlines() if "【观察期结束】" in line]
    assert len(fight_rows) == 1 and fight_rows[0].startswith("+0.0s")  # 交火结束在 tick 100，正好一行
    assert len(close_rows) == 1 and close_rows[0].startswith("+3.0s") and text.splitlines()[-1] == close_rows[0]


def test_timeline_keeps_death_row_close_to_previous_row():
    # 交火后 2.1 秒阵亡（离上一行 +2.0s 只有 0.1 秒）：阵亡行必须保留，观察期结束标在最后一个存活的时刻
    match = fight_match(extra_kills=[(100 + round(2.1 * RATE), T[1], CT[0])])
    result = reposition(match, CT[0], 1, 100, CFG)
    text = build_timeline(match, _window(), result, None, *_silent(), CFG)
    lines = text.splitlines()
    assert lines[-1].endswith("你已阵亡。")
    assert "【观察期结束】" in lines[-2]
    # 1.5 秒内阵亡：没有观察期结束行
    early = fight_match(extra_kills=[(130, T[1], CT[0])])
    text = build_timeline(early, _window(), reposition(early, CT[0], 1, 100, CFG), None, *_silent(), CFG)
    assert "【观察期结束】" not in text and text.splitlines()[-1].endswith("你已阵亡。")
