import numpy as np
import pandas as pd
import pytest

from cs2coach.config import load_config
from cs2coach.features.aim import aim_vector, shot_targets
from cs2coach.parsing.match import build_shots
from tests.helpers.synthetic import A, C, D, make_match, make_ticks, sid

CFG = load_config()


def test_aim_vector_convention():
    # Source：yaw 0 朝 +X，yaw 90 朝 +Y，pitch 为正是向下看
    assert aim_vector(np.array([0.0]), np.array([0.0]))[0] == pytest.approx([1, 0, 0])
    assert aim_vector(np.array([0.0]), np.array([90.0]))[0] == pytest.approx([0, 1, 0], abs=1e-9)
    assert aim_vector(np.array([90.0]), np.array([0.0]))[0] == pytest.approx([0, 0, -1], abs=1e-9)


def match_with_shots(rows, overrides):
    m = make_match(make_ticks(overrides=overrides))
    wf = pd.DataFrame([{"tick": t, "user_steamid": sid(s), "weapon": "weapon_ak47"} for t, s, _ in rows])
    fb = pd.DataFrame([{"tick": t, "user_steamid": sid(s), "angles_x": 0.0, "angles_y": yaw, "inaccuracy": 0.0,
                        "spread": 0.0, "recoil_index": 0.0} for t, s, yaw in rows])
    m.shots = build_shots(wf, fb, m)
    return m


def test_shot_targets_picks_closest_enemy_by_angle():
    o = {}
    for t in range(1, 121):
        o[(C, t)] = {"X": 1000.0}           # A 正前方
        o[(D, t)] = {"X": 1000.0, "Y": 300.0}  # 偏右约 16.7°
    o.update({(C, t): {"X": 1000.0, "is_alive": False} for t in range(60, 121)})  # C 在 60 被打死
    rows = [(40, A, 0.0), (41, A, 16.0), (42, A, 120.0), (60, A, 0.0)]
    m = match_with_shots(rows, o)
    t = shot_targets(m, CFG).set_index("tick")
    assert t.loc[40, "target"] == C and t.loc[40, "target_angle"] < 3
    assert t.loc[41, "target"] == D
    assert pd.isna(t.loc[42, "target"])  # 偏离所有敌人超过 max_target_angle
    assert t.loc[60, "target"] == C  # 击杀那一枪：用前一 tick 的状态，C 仍然存活


def test_shot_targets_ignore_teammates():
    o = {(C, t): {"X": -1000.0} for t in range(1, 121)}  # 敌人在背后
    rows = [(40, A, 0.0)]  # 正前方只有队友 B（原点附近，不算）
    t = shot_targets(match_with_shots(rows, o), CFG)
    assert pd.isna(t["target"].iloc[0])


@pytest.mark.parametrize("rows,expected", [
    ({100: True}, True),
    ({99: False, 100: True}, False),
    ({99: None, 100: True}, True),
    ({98: True}, False),
])
def test_scoped_before_shot_falls_back_when_previous_tick_is_missing(rows, expected):
    from cs2coach.features.aim import scoped_before_shot

    states = pd.DataFrame({"is_scoped": rows})
    assert scoped_before_shot(states, 100) is expected
