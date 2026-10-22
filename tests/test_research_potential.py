"""可能威胁补丁：历史 T 分布（留一场）、本队视野内看过的格子清零、按位置不明的 T 人数缩放。"""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from cs2coach.research.potential import (TPrior, build_prior, cleared, floor_grid, potential, potential_danger,
                                         visibility_matrix)
from tests.helpers.synthetic import AlwaysVisible, WallChecker
from tests.test_research_r01 import CFG as _CFG

CFG = copy.deepcopy(_CFG)
CFG["research"]["threat"].update(full_weight_distance=0, zero_weight_distance=2000, potential_weight=0.5,
                                 potential_bucket_seconds=5, potential_time_sigma=0.0, eye_height=64)
SQUARE = [[(0.0, 0.0, 0.0), (400.0, 0.0, 0.0), (400.0, 400.0, 0.0), (0.0, 400.0, 0.0)]]


def test_floor_grid_covers_polygon_cells():
    grid = floor_grid(SQUARE, 100.0)
    assert len(grid.centers) == 16 and set(grid.centers[:, 2]) == {0.0}
    assert grid.locate([50.0], [50.0])[0] >= 0 and grid.locate([-500.0], [50.0])[0] == -1
    assert grid.nearest(-30.0, 50.0) == grid.locate([50.0], [50.0])[0]          # 不在格子上取最近的


def test_visibility_matrix_respects_walls_and_symmetry():
    grid = floor_grid(SQUARE, 100.0)
    vis = visibility_matrix(grid, WallChecker(wall_x=200.0, wall_y_max=1e9), 64, 2000)
    left, right = grid.locate([50.0], [50.0])[0], grid.locate([350.0], [50.0])[0]
    assert not vis[left, right] and (vis == vis.T).all() and vis[left, left]


def test_cleared_only_inside_field_of_view():
    grid = floor_grid(SQUARE, 100.0)
    vis = np.ones((16, 16), dtype=bool)
    facing_east = cleared(grid, vis, [(50.0, 50.0, 0.0)], fov=90)
    east, north = grid.locate([350.0], [50.0])[0], grid.locate([50.0], [350.0])[0]
    assert facing_east[east] and not facing_east[north]
    assert cleared(grid, vis, [(50.0, 50.0)], fov=90).all()                    # 没有朝向按 360 度


def test_potential_scales_to_unknown_enemies_and_skips_cleared():
    prior = np.array([1.0, 1.0, 2.0, 0.0])
    pot = potential(prior, np.array([False, True, False, False]), unknown_t=2)
    assert pot.tolist() == [0.5, 0.0, 1.0, 0.0]
    assert potential(prior, np.zeros(4, dtype=bool), 0).sum() == 0 and potential(None, np.zeros(4, dtype=bool), 2) is None


def test_potential_danger_counts_visible_cells_with_distance_weight():
    grid = floor_grid(SQUARE, 100.0)
    pot = np.zeros(16)
    far = grid.locate([350.0], [350.0])[0]
    pot[far] = 1.0
    d = potential_danger((50.0, 50.0, 0.0), pot, grid, np.ones((16, 16), dtype=bool), CFG)
    assert d == pytest.approx(0.5 * (1 - np.hypot(300, 300) / 2000))
    assert potential_danger((50.0, 50.0, 0.0), pot, grid, np.eye(16, dtype=bool), CFG) == 0.0


def test_prior_is_per_direction_phase_and_leaves_own_match_out():
    grid = floor_grid(SQUARE, 100.0)
    pos, kills = [], []
    for match, x in (("m1", 50.0), ("m2", 350.0)):
        kills.append(dict(demo_id=f"{match}.dem", match_id=match, round=1, t=3.0, tick=192, victim_team_num=2,
                          victim_X=0.0, victim_Y=0.0, victim_last_place_name="BombsiteA"))
        for i in range(8):  # 0–2 秒，每 0.25 秒一个时刻：一名 T、一名 CT
            pos.append(dict(demo_id=f"{match}.dem", match_id=match, round=1, t=i * 0.25, team_num=2, X=x, Y=50.0))
            pos.append(dict(demo_id=f"{match}.dem", match_id=match, round=1, t=i * 0.25, team_num=3, X=200.0, Y=200.0))
    prior = build_prior(pd.DataFrame(pos), pd.DataFrame(kills), grid, CFG, "de_mirage")
    assert isinstance(prior, TPrior)
    both = prior.expected("A", 1.0, None)
    assert both.sum() == pytest.approx(1.0)                                      # 平均每个时刻 1 名 T
    only_m2 = prior.expected("A", 1.0, "m1")
    assert only_m2[grid.locate([350.0], [50.0])[0]] == pytest.approx(1.0) and only_m2[grid.locate([50.0], [50.0])[0]] == 0
    assert prior.expected("B", 1.0, None) is None and prior.expected("A", 7.0, None) is None


def test_prior_time_smoothing_blends_neighbouring_seconds():
    prior = TPrior(bucket=1.0, n_cells=2, sigma=1.0)
    prior.counts = {("A", 10, "m"): np.array([4.0, 0.0]), ("A", 12, "m"): np.array([0.0, 4.0])}
    prior.moments = {("A", 10, "m"): 4, ("A", 12, "m"): 4}
    at_10, at_11, at_12 = (prior.expected("A", t, None) for t in (10.5, 11.5, 12.5))
    assert at_10[0] > at_10[1] and at_12[1] > at_12[0] and at_11[0] == pytest.approx(at_11[1])
    prior.sigma = 0.0
    assert prior.expected("A", 11.5, None) is None                               # 不平滑时第 11 秒那一档没有数据


def test_smoke_blocks_potential_threats():
    grid = floor_grid(SQUARE, 100.0)
    pot = np.zeros(16)
    pot[grid.locate([350.0], [50.0])[0]] = 1.0
    open_ = potential_danger((50.0, 50.0, 0.0), pot, grid, np.ones((16, 16), dtype=bool), CFG)
    smoke = np.array([[200.0, 50.0, 64.0]])
    assert open_ > 0 and potential_danger((50.0, 50.0, 0.0), pot, grid, np.ones((16, 16), dtype=bool), CFG, smoke) == 0.0


def test_emergence_is_first_visible_cell_on_the_path_and_facing_uses_it():
    """L 形通道：他在左下角，威胁在右上角拐过去的地方。看不到威胁时，出现点是拐角处第一个看得到的格子。"""
    from cs2coach.research.potential import adjacency, corner_facing, emergence

    corridor = [[(0.0, 0.0, 0.0), (500.0, 0.0, 0.0), (500.0, 100.0, 0.0), (0.0, 100.0, 0.0)],
                [(400.0, 100.0, 0.0), (500.0, 100.0, 0.0), (500.0, 600.0, 0.0), (400.0, 600.0, 0.0)]]
    grid = floor_grid(corridor, 100.0)
    me, corner, enemy = (grid.locate([50.0], [50.0])[0], grid.locate([450.0], [50.0])[0], grid.locate([450.0], [550.0])[0])
    vis = np.zeros((len(grid.centers),) * 2, dtype=bool)
    bottom = [grid.locate([x], [50.0])[0] for x in (50.0, 150.0, 250.0, 350.0, 450.0)]
    for a in bottom:          # 只有下面这一条走廊互相看得到
        for b in bottom:
            vis[a, b] = True
    adj = adjacency(grid, 80)
    em = emergence(me, vis, adj, {enemy: 1.0}, 3000)
    near_corner = {corner, grid.locate([350.0], [50.0])[0]}                       # 斜着抄近路时拐角前一格也算
    assert set(em) <= near_corner and sum(em.values()) == pytest.approx(1 - 900 / 3000, abs=0.1)
    track = pd.DataFrame([dict(t=t, X=50.0, Y=50.0, Z=0.0, yaw=yaw, alive=True) for t, yaw in ((0, 0.0), (1, 0.0), (2, 90.0))])
    share = corner_facing(track, 0, 2, {enemy: 1.0}, grid, vis, adj, 20, 3000)
    assert share == pytest.approx(2 / 3, abs=0.001)                              # 3 个时刻里 2 个准星对着拐角


def test_visible_threat_is_its_own_emergence_even_from_a_ledge():
    """他站在高处（窗口），威胁在下面看得到的地方：不需要走得过去，出现点就是威胁本身。"""
    from cs2coach.research.potential import adjacency, emergence

    ledge = [[(0.0, 0.0, 100.0), (100.0, 0.0, 100.0), (100.0, 100.0, 100.0), (0.0, 100.0, 100.0)],
             [(100.0, 0.0, 0.0), (500.0, 0.0, 0.0), (500.0, 100.0, 0.0), (100.0, 100.0, 0.0)]]
    grid = floor_grid(ledge, 100.0)
    me, enemy = grid.locate([50.0], [50.0])[0], grid.locate([450.0], [50.0])[0]
    vis = np.ones((len(grid.centers),) * 2, dtype=bool)
    adj = adjacency(grid, 80)
    floor = grid.locate([150.0], [50.0])[0]
    assert any(j == me for j, _ in adj[floor])                                  # 敌人能从高处跳到下面
    assert not any(j == floor for j, _ in adj[me])                              # 但爬不上 100 的高处
    em = emergence(me, vis, adj, {enemy: 1.0}, 3000, grid.centers)
    assert list(em) == [enemy] and em[enemy] == pytest.approx(1 - 400 / 3000)
