"""R03 第 5 版：首杀方向、危险度地图（留一场）、连续击杀、观察期截断与激进 / 保守判定。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cs2coach.research import stance
from cs2coach.research.danger import build_danger, direction_of, first_kills, samples
from tests.test_research_fight import fight_match
from tests.test_research_r01 import CFG, CT, T

RATE = 64


def test_merge_end_merges_within_gap():
    assert stance.merge_end([100, 150, 300], 100, RATE) == 150
    assert stance.merge_end([100], 100, RATE) == 100
    assert stance.merge_end([100, 164, 228, 400], 100, RATE) == 228


@pytest.mark.parametrize("place, x, y, expected", [
    ("BombsiteA", 0, 0, "A"),
    ("Apartments", 0, 0, "B"),
    ("SideAlley", 0, 0, "Mid"),
    ("TSpawn", 0, 0, None),
    ("Catwalk", -900, 100, "B"),       # 第二个拐角以北
    ("Catwalk", -900, -300, "Mid"),
    ("CTSpawn", -1100, -700, "Mid"),   # 通往 Jungle 的通道顶端角落
    ("CTSpawn", -1300, -1000, "A"),    # 通往 Jungle 的通道
    ("CTSpawn", -1250, -1500, "A"),    # 出生点东侧小区域
    ("CTSpawn", -1700, -1000, None),   # 通往 Shop 的通道
    ("CTSpawn", -1300, -2400, "A"),    # 通往 A 包点的南段
    (None, 0, 0, None),
])
def test_direction_of_uses_place_groups_and_coordinate_splits(place, x, y, expected):
    assert direction_of(place, x, y, CFG, "de_mirage") == expected


@pytest.mark.parametrize("move, height, d0, d1, observed, expected", [
    (150, 10, None, None, 3.0, ("aggressive_hold", None)),    # 原位先于危险度判断
    (150, 70, 1.0, 1.0, 3.0, ("conservative", "sideways")),  # 跳上 / 跳下一层不算原位
    (400, 0, 1.0, 1.3, 3.0, ("aggressive_push", None)),
    (400, 0, 1.0, 1.1, 3.0, ("conservative", "sideways")),   # 同等危险的横移算保守
    (400, 0, 1.0, 0.5, 3.0, ("conservative", "retreat")),
    (400, 0, None, 0.5, 3.0, ("unknown", "no_danger")),      # 首杀方向不归类：移动了就判不出
    (150, 0, 1.0, 1.0, 0.8, ("unknown", "short")),
    (None, None, None, None, None, ("unknown", "missing")),
])
def test_classify_order(move, height, d0, d1, observed, expected):
    assert stance.classify(move, height, d0, d1, observed, CFG) == expected


class FlatMaps:
    """假的危险度：x 越大越危险（每 100 单位 +0.1 倍）。"""

    def ratio(self, direction, x, y, exclude=None):
        return None if direction is None else 1.0 + x / 1000


def _facts(**changes):
    facts = dict(match_id="m", tick_rate=RATE, first_kill_tick=1000, round_end_tick=5000,
                 fk_victim_place="BombsiteA", fk_victim_x=500.0, fk_victim_y=0.0)
    facts.update(changes)
    return facts


def _events(*rows):
    """(秒, kind, enemy_x)：相对首杀；首杀本身总在第 0 秒。"""
    base = [(0.0, "kill", 500.0), *rows]
    return pd.DataFrame([dict(tick=1000 + round(s * RATE), kind=k, enemy="9", weapon="ak47", gun=True, damage=None,
                              enemy_x=x, enemy_y=0.0, enemy_z=0.0) for s, k, x in base])


def _track(points, yaw=0.0):
    """(秒, x, y, alive)：t 相对首杀。"""
    return pd.DataFrame([dict(t=t, X=x, Y=y, Z=0.0, yaw=yaw, alive=alive) for t, x, y, alive in points])


MAP_CFG = __import__("copy").deepcopy(CFG)
MAP_CFG["research"]["stance"]["danger_method"] = "map"   # 这些用例检验第 5 版的统计地图规则，不随配置切换
MAP_CFG["research"]["stance"]["scheme"] = "position"


def _label(events, track, **facts):
    return stance.label(_facts(**facts), track, events, FlatMaps(), MAP_CFG, "de_mirage")


def test_label_push_hold_and_view_patch():
    push = _label(_events(), _track([(0, 0, 0, True), (1.5, 200, 0, True), (3.0, 400, 0, True)]))
    assert (push["label"], push["stance"], push["move"], push["direction"]) == ("aggressive_push", "激进", 400.0, "A")
    assert push["view_toward_kill"] is True and push["stop_reason"] == "observe" and push["engagement_end_s"] == 0
    hold = _label(_events(), _track([(0, 0, 0, True), (3.0, 100, 0, True)], yaw=180.0))
    assert hold["label"] == "aggressive_hold" and hold["view_toward_kill"] is False   # 退半步背对：仍算原位，只记录视线


def test_engagement_merges_continuous_fight_and_observes_after_it():
    # 首杀后 0.5 秒被打、1.2 秒又杀一人（都在 1 秒间隔内）：交战到 1.2 秒结束，从那时再看 3 秒
    events = _events((0.5, "hurt", 600.0), (1.2, "kill", 800.0))
    track = _track([(0, 0, 0, True), (1.25, 300, 0, True), (4.25, 300, 0, True)])
    result = _label(events, track)
    assert result["engagement_end_s"] == pytest.approx(1.203, abs=0.01) and result["engagement_kills"] == 2
    assert result["label"] == "aggressive_hold" and result["move"] == 0.0           # 起点是交战结束时的位置
    shots_only = _events((0.5, "shot", None))
    assert _label(shots_only, track)["engagement_end_s"] == 0                     # 只开枪不算交火


def test_label_truncated_by_next_fight_and_death():
    track = _track([(0, 0, 0, True), (1.0, -300, 0, True), (2.0, -600, 0, True), (3.0, -900, 0, True)])
    later = _label(_events((2.0, "hurt", 0.0)), track)                           # 间隔 > 1 秒：又一次交战
    assert later["stop_reason"] == "new_fight" and later["refight"] and later["move"] == 600.0
    assert later["label"] == "conservative"                                       # 用新交战开始那一刻的位置判
    died = _label(_events((2.5, "death", 0.0)), _track([(0, 0, 0, True), (1.0, 50, 0, True), (2.5, 50, 0, False)]))
    assert died["stop_reason"] == "death" and died["label"] == "aggressive_hold" and died["observed_s"] == 2.5
    in_fight = _label(_events((0.6, "death", 0.0)), track)
    assert (in_fight["label"], in_fight["detail"]) == ("unknown", "died_in_fight")


def test_label_without_start_position_or_track_is_unknown():
    assert _label(_events(), _track([(0.5, 0, 0, True)]))["detail"] == "missing"
    long_fight = _events(*[(0.9 * i, "hit", 600.0) for i in range(1, 14)])          # 交战 11.7 秒 + 观察 3 秒 > 12 秒
    assert _label(long_fight, _track([(0, 0, 0, True)]))["detail"] == "track_too_short"


def _danger_data():
    """两场比赛、首杀都在 A 包点；m1 的 CT 都死在 x=0 附近，m2 的 CT 都在 x=900 附近停留且没死。"""
    pos, kills = [], []
    for match in ("m1", "m2"):
        for rnd in range(1, 5):
            kills.append(dict(demo_id=f"{match}.dem", match_id=match, round=rnd, t=10.0, tick=640,
                              victim_team_num=2, victim_X=0.0, victim_Y=0.0, victim_last_place_name="BombsiteA"))
            for i in range(80):  # 首杀后 20 秒，每 0.25 秒
                pos.append(dict(demo_id=f"{match}.dem", match_id=match, round=rnd, t=10.0 + i * 0.25, team_num=3,
                                X=0.0 if match == "m1" else 900.0, Y=0.0))
            if match == "m1":
                kills.append(dict(demo_id=f"{match}.dem", match_id=match, round=rnd, t=15.0, tick=960,
                                  victim_team_num=3, victim_X=0.0, victim_Y=0.0, victim_last_place_name="BombsiteA"))
    return pd.DataFrame(pos), pd.DataFrame(kills)


def _cfg(shrink):
    import copy

    cfg = copy.deepcopy(CFG)
    cfg["research"]["danger"]["shrink_seconds"] = shrink
    return cfg


def test_danger_ratio_and_leave_one_match_out():
    pos, kills = _danger_data()
    maps = build_danger(pos, kills, _cfg(0.1), "de_mirage")
    assert maps.ratio("A", 0.0, 0.0) > 1.5 > 0.5 > maps.ratio("A", 900.0, 0.0)
    # 留掉 m1：剩下的 m2 没有阵亡，没有基准，判不出危险度
    assert maps.ratio("A", 0.0, 0.0, exclude="m1") is None
    assert maps.ratio("B", 0.0, 0.0) is None and maps.ratio(None, 0.0, 0.0) is None
    assert maps.ratio("A", 1e6, 0.0) is None                       # 超出地图


def test_danger_shrinks_unvisited_cells_to_group_average():
    pos, kills = _danger_data()
    maps = build_danger(pos, kills, CFG, "de_mirage")
    assert maps.ratio("A", 450.0, 200.0) == pytest.approx(1.0, abs=0.01)    # 没人去过的格子 = 本组平均
    assert np.isnan(maps.ratio_grid("A")[maps._flat([450.0], [200.0])[0] // maps.shape[1], maps._flat([450.0], [200.0])[0] % maps.shape[1]])  # 只在可走区域显示


def test_first_kills_direction_by_victim_position():
    _, kills = _danger_data()
    first = first_kills(kills, CFG, "de_mirage")
    assert len(first) == 8 and set(first["direction"]) == {"A"} and (first["victim_team_num"] == 2).all()


def test_facts_for_events_track_and_engagement():
    moves = {CT[0]: {1: {"last_place_name": "BombsiteA"}, 200: {"X": 300.0}}}
    match = fight_match(moves, extra_kills=[(140, CT[0], T[1])])               # 第二个人头在 0.6 秒后
    match.kills["weapon"] = "ak47"
    match.damages = pd.concat([match.damages, pd.DataFrame([dict(tick=300, round=1, attacker=T[2], victim=CT[0],
                                                                 weapon="ak47", dmg_effective=20)])], ignore_index=True)
    from cs2coach.research.triggers import find_situations

    situation = find_situations(match)[0][0]
    facts, track, events = stance.facts_for(match, situation, CFG)
    assert facts["first_kill_tick"] == 100 and facts["player_id"] == str(CT[0]) and facts["fk_victim_id"] == str(T[0])
    assert set(events["kind"]) == {"shot", "hit", "kill", "hurt"} and (events["tick"] >= 100).all()
    assert events.loc[events["kind"] == "hurt", "enemy"].tolist() == [str(T[2])]
    assert track["t"].min() < 0 and track["t"].diff().dropna().eq(0.125).all()
    result = stance.label(facts, track, events, FlatMaps(), CFG, "de_mirage")
    assert result["engagement_kills"] == 2 and result["engagement_end_s"] == pytest.approx(40 / RATE)
    assert result["stop_reason"] == "new_fight" and result["observed_s"] == pytest.approx(160 / RATE)


def test_samples_every_quarter_second_from_freeze_end():
    match = fight_match()
    pos, kills = samples(match, 0.25)
    assert set(pos["tick"] % 16) == {0} and pos["t"].min() >= 0
    assert len(kills) == 1 and kills["t"].iloc[0] == pytest.approx((100 - 10) / RATE)


def test_label_all_writes_windows_and_distribution_without_outcomes(tmp_path, monkeypatch):
    from scripts import research_stance as rs

    src = tmp_path / "s"
    for sub in ("tracks", "events", "raw/positions", "raw/kills"):
        (src / sub).mkdir(parents=True)
    pos, kills = _danger_data()
    pos.to_parquet(src / "raw/positions/a.parquet")
    kills.to_parquet(src / "raw/kills/a.parquet")
    pd.DataFrame([dict(_facts(match_id="m1"), demo_id="m1.dem", round_id=1, player_id="7", weapon="awp")]
                 ).to_csv(src / "facts.csv", index=False)
    _events().assign(demo_id="m1.dem", round_id=1, player_id="7").to_parquet(src / "events/a.parquet")
    track = _track([(0, 0, 0, True), (3.0, 100, 0, True)]).assign(demo_id="m1.dem", round_id=1, player_id="7",
                                                                  place="BombsiteA")
    track.to_parquet(src / "tracks/a.parquet")
    monkeypatch.setattr(rs, "load_config", lambda: MAP_CFG)
    out = rs.label_all("s", "l", root=tmp_path)
    windows = pd.read_csv(out / "windows.csv")
    assert windows["behaviour"].tolist() == ["激进·原位架枪"] and windows["sniper"].tolist() == [True]
    assert windows["role"].tolist() == ["A 主防"]
    text = (out / "summary.md").read_text(encoding="utf-8")
    assert "不含任何结果" in text and "traded" not in windows.columns


def test_spotted_count_ignores_ticks_without_team():
    import numpy as np

    match = fight_match({CT[0]: {1: {"last_place_name": "BombsiteA"}}, T[0]: {1: {"last_place_name": "BombsiteA"}}})
    bit = match.spotted_bit(CT[0])
    cfg = _cfg(CFG["research"]["danger"]["shrink_seconds"])
    cfg["research"]["stance"]["mainline_lookback_seconds"] = 40 / RATE    # 窗口从 tick 60 开始
    rows = (match.ticks["steamid"] == T[1]) & match.ticks["tick"].between(55, 70)
    match.ticks.loc[rows, "team_num"] = np.nan                       # 窗口开头阵营缺失（教练位等）：不能报错
    match.ticks.loc[match.ticks["steamid"] == T[0], "spotted_by_mask"] = 1 << bit
    from cs2coach.research.triggers import find_situations

    facts, _, _ = stance.facts_for(match, find_situations(match)[0][0], cfg)
    assert facts["spotted_t_in_direction"] == 1


def _stance_windows(n=40):
    labels = ["aggressive_hold", "aggressive_push", "conservative", "unknown"]
    return pd.DataFrame([dict(demo_id=f"m{i}.dem", match_id=f"m{i}", round_id=i, player_id=str(100 + i), player_name="p",
                              first_kill_tick=1000 + i, start_place="BombsiteA", round_time_s=20.0,
                              label=labels[i % 4], behaviour="x", detail="died_in_fight" if i == 3 else None,
                              engagement_end_s=0.5, move=1.0, height=0.0, danger_start=1.0, danger_end=1.0,
                              observed_s=3.0, jump_command="demo_gototick 1") for i in range(n)])


def test_review_pack_is_blind_split_and_skips_reviewed_rounds(tmp_path):
    import tomllib

    from scripts import research_stance as rs

    out = tmp_path / "data" / "research" / "stance" / "l"
    out.mkdir(parents=True)
    _stance_windows().to_csv(out / "windows.csv", index=False)
    old = tmp_path / "data" / "research" / "review" / "r03-1"
    old.mkdir(parents=True)
    (old / "review.toml").write_text('[[items]]\ndemo_id = "m0.dem"\nround_id = 0\n', encoding="utf-8")
    pack = rs.review("l", "g", 1, "x", root=tmp_path)
    items = tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))["items"]
    key = pd.read_csv(pack / "answer_key.csv")
    assert all("label" not in i and "behaviour" not in i for i in items)                 # 盲审
    assert {i["round_id"] for i in items}.isdisjoint({0, 3})                               # 复核过的、交战中阵亡的不抽
    assert set(key["split"]) == {"dev", "holdout"} and abs((key["split"] == "dev").sum() - (key["split"] == "holdout").sum()) <= 1
    assert items[0]["archive"].startswith("stance/x/archive/") and items[0]["focus"] == [0.5, 3.5]


def test_replay_reads_archive_without_parsing(tmp_path):
    import json

    from scripts import research_replay as rp

    folder = tmp_path / "a"
    folder.mkdir()
    (folder / "meta.json").write_text(json.dumps({"tick_rate": 64, "map_name": "de_mirage"}), encoding="utf-8")
    pd.DataFrame([dict(steamid="7", name="p")]).to_parquet(folder / "players.parquet")
    pd.DataFrame([dict(tick=t, steamid=7, X=0.0, Y=0.0, yaw=0.0, is_alive=True, team_num=3) for t in range(800, 1400, 8)]
                 ).to_parquet(folder / "ticks.parquet")
    pd.DataFrame([dict(tick=1000, attacker=7, victim=8)]).to_parquet(folder / "kills.parquet")
    archived = rp._Archived(folder)
    out = rp.extract(archived, [dict(id="g1", start_tick=1000, player_id="7", replay_until_s=3.0)], CFG)
    frames = out["g1"]["frames"]
    assert frames and frames[0][0] < 0 and out["g1"]["kills"] == [[0.0, "7", "8"]] and out["g1"]["shots"] == []


def test_score_dev_split_only(tmp_path):
    from scripts import research_review as rr
    from scripts import research_stance as rs

    pack = tmp_path / "data" / "research" / "review" / "g"
    pack.mkdir(parents=True)
    items = [dict(id=f"g{i}", demo_id=f"m{i}.dem", round_id=i, player_id=str(i), result=r, comment="")
             for i, r in enumerate(["保守", "激进·前压", "保守", "看不出"])]
    rr._write_review(pack, {"source": "x"}, items)
    pd.DataFrame([dict(id=f"g{i}", split=s) for i, s in enumerate(["dev", "dev", "holdout", "dev"])]).to_csv(pack / "answer_key.csv", index=False)
    out = tmp_path / "data" / "research" / "stance" / "l"
    out.mkdir(parents=True)
    pd.DataFrame([dict(demo_id=f"m{i}.dem", round_id=i, player_id=str(i), behaviour=b)
                  for i, b in enumerate(["保守", "激进·原位架枪", "激进·前压", "看不出"])]).to_csv(out / "windows.csv", index=False)
    text = rs.score("g", "l", root=tmp_path)
    assert "（dev，3 条）" in text and "细类：一致 2/3" in text and "激进 / 保守：一致 3/3" in text
    assert "g2" not in text                                                       # 留出集不参与


def test_ratio_grid_matches_point_queries():
    pos, kills = _danger_data()
    maps = build_danger(pos, kills, _cfg(0.1), "de_mirage")
    grid = maps.ratio_grid("A", min_exposure=0.0)
    flat = int(maps._flat([0.0], [0.0])[0])
    r, c = divmod(flat, maps.shape[1])
    assert grid[r, c] == pytest.approx(maps.ratio("A", 0.0, 0.0))
    assert np.isnan(maps.ratio_grid("A", min_exposure=1e9)).all() and maps.ratio_grid("B") is None


def test_png_encoder_and_criteria_follow_config():
    import struct
    import zlib

    from scripts import research_replay as rp
    from scripts import research_stance as rs

    rgba = np.zeros((2, 3, 4), dtype="uint8")
    rgba[0, 0] = [255, 0, 0, 255]
    png = rp._png(rgba)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (3, 2)
    raw = zlib.decompress(png[png.index(b"IDAT") + 4:-16])
    assert raw[0] == 0 and raw[1:5] == bytes([255, 0, 0, 255]) and len(raw) == 2 * (1 + 3 * 4)
    text = " ".join(rs.criteria(MAP_CFG))
    assert f"不到 {CFG['research']['stance']['stay_max_move']:g} 单位" in text and "去掉了本场比赛" in text
    threat_cfg = __import__("copy").deepcopy(MAP_CFG)
    threat_cfg["research"]["stance"]["danger_method"] = "threat"
    text = " ".join(rs.criteria(threat_cfg))
    assert "队友听到也算" in text and "去掉了本场比赛" not in text


def test_compare_pack_is_blind_and_skips_gold_rounds(tmp_path):
    import tomllib

    from scripts import research_stance as rs

    base = _stance_windows(8).assign(behaviour="保守", detail=None)
    other = base.copy()
    other.loc[[1, 2, 5], "behaviour"] = "激进·前压"
    for name, frame in (("a", base), ("b", other)):
        out = tmp_path / "data" / "research" / "stance" / name
        out.mkdir(parents=True)
        frame.to_csv(out / "windows.csv", index=False)
    gold = tmp_path / "data" / "research" / "review" / "gold-1"
    gold.mkdir(parents=True)
    (gold / "review.toml").write_text('[[items]]\ndemo_id = "m5.dem"\nround_id = 5\n', encoding="utf-8")
    pack = rs.compare_pack("a", "b", "c", "x", root=tmp_path)
    items = tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))["items"]
    assert sorted(i["round_id"] for i in items) == [1, 2] and all("behaviour" not in i for i in items)
    assert set(pd.read_csv(pack / "answer_key.csv").columns) == {"id", "a", "b"}



SEEK_CFG = __import__("copy").deepcopy(CFG)
SEEK_CFG["research"]["stance"].update(scheme="seek", danger_method="threat", stay_max_move=200, stay_max_height=60,
                                      min_observe_seconds=1.0, passive_min_danger=0.05, facing_max_angle=45, facing_min_share=0.5,
                                      fought_to_round_end_active=True)
SEEK_CFG["research"]["threat"].update(push_min_delta=0.1, retreat_max_delta=-0.5)


@pytest.mark.parametrize("move, height, d0, d1, facing, expected", [
    (400, 0, 0.2, 0.5, 0.0, ("aggressive_active", None)),        # 移动且危险度明显上升：主动寻求对枪
    (50, 0, 0.3, 0.3, 0.8, ("aggressive_passive", None)),        # 原地、暴露、朝着威胁：被动
    (400, 0, 0.3, 0.3, 0.8, ("aggressive_passive", None)),       # 横移到另一个也架得到的位置：被动（产品负责人定）
    (50, 0, 0.7, 0.28, 0.0, ("conservative", "not_facing")),     # 原地但不看威胁方向（g35 这类）：保守
    (50, 0, 0.3, 0.0, 0.8, ("conservative", "hidden")),          # 缩到看不到威胁的地方：保守
    (400, 0, 1.0, 0.2, 0.0, ("conservative", "retreat")),
    (400, 0, None, 0.2, 0.0, ("unknown", "no_danger")),
])
def test_classify_seek(move, height, d0, d1, facing, expected):
    assert stance.classify_seek(move, height, d0, d1, 3.0, facing, SEEK_CFG) == expected


def test_facing_share_counts_time_looking_at_threat_sources():
    track = pd.DataFrame([dict(t=t, X=0.0, Y=0.0, Z=0.0, yaw=yaw, alive=True)
                          for t, yaw in ((0.0, 0.0), (1.0, 10.0), (2.0, 180.0), (3.0, 90.0))])
    assert stance.facing_share(track, 0.0, 3.0, [(1000.0, 0.0, 1.0)], SEEK_CFG) == 0.5
    assert stance.facing_share(track, 0.0, 3.0, [], SEEK_CFG) == 0.0


def test_seek_label_uses_sources_and_round_end_fight():
    def threat(p0, p1, start, stop):
        return 0.3, 0.3, 1, [(1000.0, 0.0, 1.0)]

    track = pd.DataFrame([dict(t=t / 4, X=0.0, Y=0.0, Z=0.0, yaw=0.0, alive=True) for t in range(13)])
    result = stance.label(_facts(), track, _events(), None, SEEK_CFG, "de_mirage", threat=threat)
    assert result["label"] == "aggressive_passive" and result["facing_share"] == 1.0 and result["stance"] == "激进"
    fight = _events(*[(0.5 * i, "hit", 600.0) for i in range(1, 6)])         # 一直打到回合结束
    ended = stance.label(_facts(round_end_tick=1000 + round(2.5 * RATE)), track, fight, None, SEEK_CFG, "de_mirage", threat=threat)
    assert (ended["label"], ended["detail"]) == ("aggressive_active", "fought_to_round_end")


def test_v6_patches_hide_and_fought_to_round_end():
    cfg = __import__("copy").deepcopy(CFG)
    cfg["research"]["stance"].update(scheme="position", danger_method="threat", stay_max_move=200, stay_max_height=60,
                                     min_observe_seconds=1.0, patch_hide_when_danger_drops=True, hide_max_delta=-0.5,
                                     patch_fought_to_round_end=True)
    cfg["research"]["threat"].update(push_min_delta=0.1, retreat_max_delta=-0.5)
    assert stance.classify(135, 0, 1.75, 0.87, 3.0, cfg, "threat") == ("conservative", "hide")
    assert stance.classify(135, 0, 0.25, 0.28, 3.0, cfg, "threat") == ("aggressive_hold", None)
    off = __import__("copy").deepcopy(cfg)
    off["research"]["stance"]["patch_hide_when_danger_drops"] = False
    assert stance.classify(135, 0, 1.75, 0.87, 3.0, off, "threat") == ("aggressive_hold", None)
    track = pd.DataFrame([dict(t=t / 4, X=0.0, Y=0.0, Z=0.0, yaw=0.0, alive=True) for t in range(13)])
    fight = _events(*[(0.5 * i, "hit", 600.0) for i in range(1, 6)])
    ended = stance.label(_facts(round_end_tick=1000 + round(2.5 * RATE)), track, fight, None, cfg, "de_mirage")
    assert (ended["label"], ended["detail"]) == ("aggressive_push", "fought_to_round_end")


def test_smoked_hold_patch():
    cfg = __import__("copy").deepcopy(CFG)
    cfg["research"]["stance"].update(stay_max_move=200, stay_max_height=60, min_observe_seconds=1.0, patch_smoked_hold=True,
                                     smoked_min_share=0.5, patch_hide_when_danger_drops=False)
    assert stance.classify(50, 0, 0.3, 0.3, 3.0, cfg, "threat", 0.8) == ("conservative", "smoked")
    assert stance.classify(50, 0, 0.3, 0.3, 3.0, cfg, "threat", 0.2) == ("aggressive_hold", None)
    assert stance.classify(400, 0, 0.3, 0.6, 3.0, cfg, "threat", 0.9)[0] == "aggressive_push"   # 只管原地的


def _v6b_cfg():
    cfg = __import__("copy").deepcopy(CFG)
    cfg["research"]["stance"].update(scheme="position", danger_method="threat", stay_max_move=200, stay_max_height=60,
                                     min_observe_seconds=1.0, patch_hide_when_danger_drops=False, patch_smoked_hold=False,
                                     patch_own_smoke=True, own_smoke_radius=250, own_smoke_min_share=0.4,
                                     patch_sideways_toward_t=True, sideways_min_progress=50)
    cfg["research"]["threat"].update(push_min_delta=0.1, retreat_max_delta=-0.5)
    return cfg


def test_own_smoke_patch_before_hold_and_push():
    cfg = _v6b_cfg()
    assert stance.classify(130, 0, 0.3, 0.3, 3.0, cfg, "threat", None, 0.5) == ("conservative", "own_smoke")   # g35：原地
    assert stance.classify(400, 0, 0.3, 0.6, 3.0, cfg, "threat", None, 0.4) == ("conservative", "own_smoke")   # 移动也算
    assert stance.classify(130, 0, 0.3, 0.3, 3.0, cfg, "threat", None, 0.3) == ("aggressive_hold", None)
    assert stance.classify(130, 0, 0.3, 0.3, 0.5, cfg, "threat", None, 1.0) == ("unknown", "short")           # 观察太短仍判不出
    cfg["research"]["stance"]["patch_own_smoke"] = False
    assert stance.classify(130, 0, 0.3, 0.3, 3.0, cfg, "threat", None, 1.0) == ("aggressive_hold", None)


def test_sideways_toward_t_patch():
    cfg = _v6b_cfg()
    assert stance.classify(290, -2, 0.11, 0.16, 3.0, cfg, "threat", None, 0.0, 311) == ("aggressive_push", "toward_t")  # g53
    assert stance.classify(290, -2, 0.11, 0.16, 3.0, cfg, "threat", None, 0.0, 49) == ("conservative", "sideways")
    assert stance.classify(290, -2, 0.11, 0.16, 3.0, cfg, "threat", None, 0.0, None) == ("conservative", "sideways")
    assert stance.classify(500, 0, 0.8, 0.2, 3.0, cfg, "threat", None, 0.0, 300) == ("conservative", "retreat")  # 后撤不受影响
    cfg["research"]["stance"]["patch_sideways_toward_t"] = False
    assert stance.classify(290, -2, 0.11, 0.16, 3.0, cfg, "threat", None, 0.0, 311) == ("conservative", "sideways")


def test_own_smoke_share_counts_alive_samples_near_smoke():
    track = pd.DataFrame([dict(t=t / 4, X=0.0, Y=0.0, Z=0.0, alive=t < 12) for t in range(14)])
    near = np.array([[100.0, 100.0, 50.0]])
    far = np.array([[300.0, 0.0, 50.0]])
    smokes_at = lambda t: near if t >= 2.0 else far  # noqa: E731
    # 观察期 0.5–3.0 秒：活着的样本 t = 0.5…2.75（10 个），t ≥ 2.0 的 4 个身边 250 以内有烟
    assert stance.own_smoke_share(track, 0.5, 3.0, smokes_at, 250) == 0.4
    assert stance.own_smoke_share(track, 0.5, 3.0, lambda t: np.zeros((0, 3)), 250) == 0.0
    assert stance.own_smoke_share(track, 5.0, 6.0, smokes_at, 250) is None


def test_label_passes_progress_and_own_smoke_to_classify():
    cfg = _v6b_cfg()
    track = _track([(0, 0, 0, True), (1.5, 150, 150, True), (3.0, 290, 0, True)])
    threat = lambda p0, p1, start, stop: (0.11, 0.16, 1)  # noqa: E731
    toward = stance.label(_facts(), track, _events(), FlatMaps(), cfg, "de_mirage", threat=threat,
                          progress=lambda p0, p1: 311.0, smokes_at=lambda t: np.zeros((0, 3)))
    assert (toward["label"], toward["detail"], toward["t_progress"], toward["own_smoke_share"]) == \
        ("aggressive_push", "toward_t", 311.0, 0.0)
    smoked = stance.label(_facts(), track, _events(), FlatMaps(), cfg, "de_mirage", threat=threat,
                          smokes_at=lambda t: np.array([[0.0, 0.0, 0.0]]) if t < 1.6 else np.zeros((0, 3)))
    assert smoked["t_progress"] is None and smoked["own_smoke_share"] >= 0.4
    assert (smoked["label"], smoked["detail"]) == ("conservative", "own_smoke")
