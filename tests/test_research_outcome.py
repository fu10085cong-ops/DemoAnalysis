"""R03 第 2 步后果：阵亡时间（回合结束截断）、之后的击杀、Kaplan–Meier、按比赛重抽。"""

import numpy as np
import pandas as pd

from cs2coach.research.outcome import cluster_bootstrap, km_death_by, outcome_row

RATE = 64


def _window(stop=1000, end=2000):
    return {"tick_rate": RATE, "observe_end_tick": stop, "player_id": "7", "round_id": 3, "round_end_tick": end}


def _kills(*rows):
    """(tick, attacker, victim, victim_team_num)"""
    return pd.DataFrame([dict(round=3, tick=t, attacker=a, victim=v, victim_team_num=team) for t, a, v, team in rows],
                        columns=["round", "tick", "attacker", "victim", "victim_team_num"])


def test_died_within_horizon_and_no_kill_after():
    o = outcome_row(_window(), _kills((1000 + 3 * RATE, 99, 7, 3)), 10)
    assert o == {"time_s": 3.0, "event": True, "died": True, "kill_after": False}


def test_kill_after_then_died_later():
    o = outcome_row(_window(end=5000), _kills((1000 + RATE, 7, 50, 2), (1000 + 12 * RATE, 99, 7, 3)), 10)
    assert o == {"time_s": 12.0, "event": True, "died": False, "kill_after": True}


def test_round_end_censors():
    short = outcome_row(_window(end=1000 + 4 * RATE), _kills(), 10)
    assert short == {"time_s": 4.0, "event": False, "died": None, "kill_after": None}   # 回合 4 秒后结束：不知道
    long = outcome_row(_window(end=1000 + 20 * RATE), _kills(), 10)
    assert long == {"time_s": 20.0, "event": False, "died": False, "kill_after": False}


def test_other_rounds_and_teammate_kills_ignored():
    k = _kills((1000 + RATE, 7, 51, 3))                       # 打死的是 CT（误伤）不算
    k = pd.concat([k, pd.DataFrame([dict(round=4, tick=1100, attacker=99, victim=7, victim_team_num=3)])])
    o = outcome_row(_window(end=1000 + 20 * RATE), k, 10)
    assert (o["event"], o["kill_after"]) == (False, False)


def test_death_at_stop_counts():
    o = outcome_row(_window(), _kills((1000, 99, 7, 3)), 10)  # 观察因阵亡截止：截止那一刻就是阵亡
    assert (o["time_s"], o["died"]) == (0.0, True)


def test_km_treats_censored_as_at_risk_only():
    # 4 人：1 秒阵亡、2 秒截断、3 秒阵亡、20 秒截断 → S = (1 − 1/4)(1 − 1/2) = 0.375
    assert km_death_by(np.array([1, 2, 3, 20]), np.array([1, 0, 1, 0]), 10) == 0.625
    assert km_death_by(np.array([]), np.array([]), 10) is None


def test_cluster_bootstrap_resamples_whole_matches():
    frame = pd.DataFrame({"match_id": ["a"] * 3 + ["b"] * 3, "y": [1, 1, 1, 0, 0, 0]})
    low, high = cluster_bootstrap(frame, lambda f: f["y"].mean(), reps=200)
    assert low == 0.0 and high == 1.0   # 只有两场：整场重抽只会得到 0、0.5、1


def test_mh_risk_difference_removes_confounding_by_stratum():
    from cs2coach.research.outcome import mh_risk_difference

    # 两个格子里激进与保守的阵亡率完全相同（差 0），但激进多集中在高危格子 → 粗差 > 0、调整后 = 0
    rows = ([dict(s="高危", g="激进", y=1)] * 8 + [dict(s="高危", g="激进", y=0)] * 2 + [dict(s="高危", g="保守", y=1)] * 4
            + [dict(s="高危", g="保守", y=0)] * 1 + [dict(s="低危", g="激进", y=0)] * 4 + [dict(s="低危", g="激进", y=1)] * 1
            + [dict(s="低危", g="保守", y=0)] * 8 + [dict(s="低危", g="保守", y=1)] * 2 + [dict(s="只有一组", g="激进", y=1)] * 5)
    f = pd.DataFrame(rows)
    crude = f[f.g == "激进"].y.mean() - f[f.g == "保守"].y.mean()
    assert crude > 0.2
    assert mh_risk_difference(f, "g", "y", ["s"], "激进", "保守") == 0.0


def test_econ_type_and_matchup():
    from cs2coach.research.outcome import econ_matchup, econ_type

    assert [econ_type(v, 3900, 1500) for v in (5000, 3900, 3899, 1500, 1499, float("nan"))] == \
        ["全起", "全起", "半起", "半起", "经济局", "未知"]
    assert econ_matchup("全起", "全起") == "双方全起" and econ_matchup("全起", "经济局") == "只有 CT 全起"
    assert econ_matchup("半起", "全起") == "只有 T 全起" and econ_matchup("经济局", "半起") == "都没全起"
    assert econ_matchup("未知", "全起") == "未知"
