"""R03 第 6 版：这一回合的威胁热力图——只用 CT 能知道的信息（雷达、听得到的声音与枪声、接火点）。"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from cs2coach.research import stance
from cs2coach.research.threat import MatchArchive, Threat, active_smokes, contact_threats, danger_at, known_threats, window_danger
from tests.helpers.synthetic import AlwaysVisible, WallChecker
from tests.test_research_r01 import CFG as _CFG

CFG = __import__("copy").deepcopy(_CFG)  # 固定参数，调配置不影响这些用例
CFG["research"]["threat"].update(full_weight_distance=1500, zero_weight_distance=3500, hidden_weight=0.0,
                                 push_min_delta=0.5, retreat_max_delta=-0.5, memory_seconds=3.0, eye_height=64)

CT1, T1, T2, T3 = 11, 21, 22, 23   # spotted 位序按 steamid 升序：CT1=0, T1=1, T2=2, T3=3


def _archive(tmp_path, sounds=(), shots=(), smokes=(), t3_dies_at=None, name="a"):
    rows = []
    for tick in range(0, 1001, 8):
        rows.append(dict(tick=tick, steamid=CT1, team_num=3, X=0.0, Y=0.0, Z=0.0, is_alive=True, spotted_by_mask=0))
        # T1 被 CT1 的雷达发现（第 0 位）；T2 一直没被发现；T3 被发现但之后阵亡
        rows.append(dict(tick=tick, steamid=T1, team_num=2, X=1000.0, Y=0.0, Z=0.0, is_alive=True, spotted_by_mask=1))
        rows.append(dict(tick=tick, steamid=T2, team_num=2, X=500.0, Y=500.0, Z=0.0, is_alive=True, spotted_by_mask=0))
        rows.append(dict(tick=tick, steamid=T3, team_num=2, X=-800.0, Y=0.0, Z=0.0,
                         is_alive=t3_dies_at is None or tick < t3_dies_at, spotted_by_mask=1))
    folder = tmp_path / name
    folder.mkdir()
    (folder / "meta.json").write_text(json.dumps({"tick_rate": 64}), encoding="utf-8")
    pd.DataFrame(rows).to_parquet(folder / "ticks.parquet")
    pd.DataFrame(list(sounds), columns=["tick", "steamid", "x", "y", "z", "radius", "step", "duration"]).to_parquet(folder / "sounds.parquet")
    pd.DataFrame(list(shots), columns=["tick", "steamid", "weapon", "is_gun"]).to_parquet(folder / "shots.parquet")
    pd.DataFrame(list(smokes), columns=["tick", "end_tick", "type", "x", "y", "z"]).to_parquet(folder / "grenades.parquet")
    return MatchArchive(folder)


def test_radar_spotted_enemies_are_threats_but_unseen_ones_are_not(tmp_path):
    threats = known_threats(_archive(tmp_path), 400, 600, CFG)
    assert {(t.enemy, t.source) for t in threats} == {(T1, "radar"), (T3, "radar")}
    assert max(t.tick for t in threats) <= 600


def test_sounds_count_only_within_audible_radius_and_dead_enemies_drop(tmp_path):
    sounds = [(500, T2, 500.0, 500.0, 0.0, 1100.0, True, 0.5),     # 离 CT1 约 707 < 1100：听得到
              (500, T2, 500.0, 500.0, 0.0, 300.0, True, 0.5)]      # 同一处、半径 300：听不到（不影响上一条）
    threats = known_threats(_archive(tmp_path, sounds=sounds, t3_dies_at=550), 400, 600, CFG)
    assert {(t.enemy, t.source) for t in threats} == {(T1, "radar"), (T2, "sound")}  # T3 之后阵亡，不算
    far = known_threats(_archive(tmp_path, sounds=[(500, T2, 500.0, 500.0, 0.0, 300.0, True, 0.5)], name="b"), 400, 600, CFG)
    assert T2 not in {t.enemy for t in far}


def test_gunshots_heard_within_gunshot_radius(tmp_path):
    threats = known_threats(_archive(tmp_path, shots=[(520, T2, "ak47", True), (530, T2, "knife", False)]), 400, 600, CFG)
    assert (T2, "gunshot") in {(t.enemy, t.source) for t in threats}


def test_danger_counts_visible_threats_and_respects_walls_smokes_distance():
    threats = [Threat(T1, 1000.0, 0.0, 0.0, 1, "radar"), Threat(T2, 0.0, -1000.0, 0.0, 1, "radar"),
               Threat(T3, 5000.0, 0.0, 0.0, 1, "radar")]                      # T3 超过 3500：不算
    assert danger_at((0.0, 0.0, 0.0), threats, AlwaysVisible(), np.zeros((0, 3)), CFG) == pytest.approx(2.0)
    wall = WallChecker(wall_x=500.0, wall_y_max=100.0)                        # x=500 处有墙：挡住 T1
    assert danger_at((0.0, 0.0, 0.0), threats, wall, np.zeros((0, 3)), CFG) == pytest.approx(1.0)
    smoke = np.array([[500.0, 0.0, 64.0]])                                     # 烟挡住 T1
    assert danger_at((0.0, 0.0, 0.0), threats, AlwaysVisible(), smoke, CFG) == pytest.approx(1.0)
    mid = [Threat(T1, 2500.0, 0.0, 0.0, 1, "radar")]                           # 1500–3500 线性递减
    assert danger_at((0.0, 0.0, 0.0), mid, AlwaysVisible(), np.zeros((0, 3)), CFG) == pytest.approx(0.5)


def test_active_smokes_window(tmp_path):
    arch = _archive(tmp_path, smokes=[(100, 2000, "smokegrenade", 1.0, 2.0, 3.0), (100, 2000, "flashbang", 0, 0, 0)])
    assert len(active_smokes(arch, 100 + 64 * 2, CFG)) == 1 and len(active_smokes(arch, 100, CFG)) == 0  # 起爆 1 秒后才算生效


def test_contact_threats_and_window_danger(tmp_path):
    events = pd.DataFrame([dict(tick=450, kind="kill", enemy_x=300.0, enemy_y=0.0, enemy_z=0.0),
                           dict(tick=455, kind="hurt", enemy_x=0.0, enemy_y=0.0, enemy_z=0.0)])
    assert [t.source for t in contact_threats(events, 400, 600)] == ["contact"]
    arch = _archive(tmp_path)
    d0, d1, count, threats = window_danger(arch, events, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 600, 800,
                                           WallChecker(wall_x=-100.0, wall_y_max=1e9), CFG)
    assert count == 3 and d0 == d1                                             # T1、T3 雷达 + 接火点


@pytest.mark.parametrize("d0, d1, expected", [(1.0, 1.6, ("aggressive_push", None)), (1.0, 1.2, ("conservative", "sideways")),
                                              (2.0, 1.0, ("conservative", "retreat")), (None, 1.0, ("unknown", "no_danger"))])
def test_classify_threat_method_uses_difference(d0, d1, expected):
    assert stance.classify(400, 0, d0, d1, 3.0, CFG, "threat") == expected


def test_label_excludes_rounds_planted_before_first_kill():
    from tests.test_research_stance import FlatMaps, _events, _facts, _track

    result = stance.label(_facts(bomb_planted=True), _track([(0, 0, 0, True), (3, 0, 0, True)]), _events(), FlatMaps(),
                          CFG, "de_mirage")
    assert (result["label"], result["detail"]) == ("unknown", "after_plant")


def test_hidden_threats_count_partly_and_closer_is_more_dangerous():
    cfg = __import__("copy").deepcopy(CFG)
    cfg["research"]["threat"].update(full_weight_distance=0, zero_weight_distance=2000, hidden_weight=0.3)
    threats = [Threat(T1, 1000.0, 0.0, 0.0, 1, "radar")]
    wall = WallChecker(wall_x=500.0, wall_y_max=100.0)
    open_ = danger_at((0.0, 0.0, 0.0), threats, AlwaysVisible(), np.zeros((0, 3)), cfg)
    assert open_ == pytest.approx(0.5) and danger_at((0.0, 0.0, 0.0), threats, wall, np.zeros((0, 3)), cfg) == pytest.approx(0.15)
    assert danger_at((400.0, 0.0, 0.0), threats, AlwaysVisible(), np.zeros((0, 3)), cfg) > open_   # 走近威胁更危险


def test_window_smokes_include_smokes_popping_during_observation(tmp_path):
    from cs2coach.research.threat import window_smokes

    arch = _archive(tmp_path, smokes=[(100, 5000, "smokegrenade", 1.0, 2.0, 3.0)])
    assert len(active_smokes(arch, 120, CFG)) == 0 and len(window_smokes(arch, 120, 120 + 64 * 3, CFG)) == 1


def test_smoke_share_weights_threats_behind_smoke():
    from cs2coach.research.threat import smoke_share

    threats = [Threat(T1, 1000.0, 0.0, 0.0, 1, "radar"), Threat(T2, 0.0, 1000.0, 0.0, 1, "radar")]
    smoke = np.array([[500.0, 0.0, 64.0]])                                     # 只挡住东边那名
    assert smoke_share((0.0, 0.0, 0.0), threats, smoke, CFG) == pytest.approx(0.5)
    assert smoke_share((0.0, 0.0, 0.0), threats, np.zeros((0, 3)), CFG) == 0.0


def test_pre_danger_uses_only_information_before_engagement_end(tmp_path):
    from cs2coach.research.threat import pre_danger

    events = pd.DataFrame([dict(tick=590, kind="kill", enemy_x=300.0, enemy_y=0.0, enemy_z=0.0)])
    late = [(700, T2, 500.0, 500.0, 0.0, 3000.0, True, 0.5)]   # T2 的脚步在交战结束之后才听到：不算
    arch = _archive(tmp_path, sounds=late)
    danger, alive = pre_danger(arch, events, (0.0, 0.0, 0.0), 600, AlwaysVisible(), CFG)
    assert alive == 2                                    # 雷达上的 T1、T3；接火点是死人，不算人数
    full = danger_at((0.0, 0.0, 0.0), known_threats(arch, 408, 600, CFG) + contact_threats(events, 408, 600),
                     AlwaysVisible(), np.zeros((0, 3)), CFG)
    assert danger == full > 0
