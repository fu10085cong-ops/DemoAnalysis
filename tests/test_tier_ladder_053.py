"""053：代表人物人均基准与多场阶梯。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pandas as pd

from cs2coach.config import load_config
from cs2coach.features.ladder import LadderPoint, ladder_points, ladder_position
from cs2coach.features.panel import PanelCounts
from cs2coach.features.problems import ProblemReport
from cs2coach.report.page_check import check_page
from cs2coach.report.page_data import build_match_page, build_summary_page
from cs2coach.summary.aggregate import aggregate_matches
from cs2coach.summary.discover import DemoRef, find_player_demos
from scripts.build_tier_baseline import build_tier_baseline, parse_one_tier_demo, representative_entries
from tests.helpers.moments import make_mm
from tests.helpers.synthetic import A, B
from tests.test_radar import line


def _aim(ttd, offset=1.0, stopped=70, spray=40):
    return dict(time_to_damage_ms=ttd, time_to_damage_samples=10,
        crosshair_placement_deg=5, crosshair_placement_samples=10,
        crosshair_offset_m=offset, crosshair_offset_samples=10,
        counter_strafing_pct=stopped, counter_strafing_shots=10,
        spray_accuracy_pct=spray, spray_shots=10,
        spotted_accuracy_pct=40, spotted_shots=10,
        headshot_accuracy_pct=40, hits=10)


def _game(sid, ttd, kills):
    return make_mm(player=sid, player_name="虚构玩家", rounds_played=20,
                   lobby=[line(steamid=sid, name="虚构玩家", aim=_aim(ttd))],
                   panel=PanelCounts(rounds=20, kills=kills))


def _tier(ident, level, ttd, offset=1.0, strafe=70, spray=40):
    metrics = {key: {"standard": value, "q25": value - (10 if key == "time_to_damage_ms" else .1),
                     "q75": value + (10 if key == "time_to_damage_ms" else .1), "values": [value]}
               for key, value in zip(("time_to_damage_ms", "crosshair_offset_m",
                                      "counter_strafing_pct", "spray_accuracy_pct"),
                                     (ttd, offset, strafe, spray))}
    return {"id": ident, "name": {1: "入门标准（5E B 段）", 2: "5E A 段", 3: "5E S 段"}[level],
            "level": level, "source": "合成样本", "per_player": {"n_players": 8,
                "matches": [10] * 8, "metrics": metrics}, "metrics": {}, "problem_rates": {}}


def test_ladder_interpolation_extrapolation_and_reach():
    points = [LadderPoint(level=i, tier_id=str(i), name=str(i), standard=v, q25=v - 5, q75=v + 5)
              for i, v in enumerate((10, 20, 30), 1)]
    assert ladder_position(20, points, True).position == 2
    assert ladder_position(15, points, True).position == 1.5
    assert ladder_position(-100, points, True).position == .5
    assert ladder_position(100, points, True).position == 3.5
    assert ladder_position(17, points, True).reached.level == 2
    assert ladder_position(14, points, True).reached.level == 1
    lower = [p.model_copy(update={"standard": 40 - p.standard, "q25": 35 - p.standard,
                                   "q75": 45 - p.standard}) for p in points]
    assert ladder_position(25, lower, False).position == 1.5
    assert ladder_position(25, lower, False).reached.level == 2
    assert ladder_position(5, lower, False).position == 3.5
    assert ladder_position(15, points[:1], True).position is None
    wrong = [points[0], points[1], points[2].model_copy(update={"standard": 15})]
    assert ladder_position(20, wrong, True).ordered is False
    assert ladder_position(20, wrong, True).position is None


def test_per_player_median_and_merged_panel(monkeypatch):
    cfg = load_config()
    cfg["panel"]["min_samples"] = {key: 1 for key in cfg["panel"]["min_samples"]}
    monkeypatch.setattr("scripts.build_tier_baseline.problem_rates", lambda *args: {})
    games = [_game(A, ttd, kills) for ttd, kills in ((100, 2), (300, 4), (900, 6))]
    games += [_game(B, ttd, kills) for ttd, kills in ((200, 10), (400, 10))]
    for mm in games:
        mm.panel.time_to_damage_ms = [100]
    result = build_tier_baseline({"id": "test", "name": "测试", "level": 1}, games, 5, {}, cfg)
    assert result["metrics"]["time_to_damage_ms"]["n"] == 5
    assert result["per_player"]["n_players"] == 1
    assert result["per_player"]["matches"] == [3]
    assert result["per_player"]["metrics"]["time_to_damage_ms"]["values"] == [300]
    assert result["per_player"]["panel_metrics"]["time_to_damage_ms"]["values"] == [100]
    assert result["per_player"]["metrics"]["kpr"]["values"] == [.2]
    assert str(A) not in json.dumps(result) and str(B) not in json.dumps(result)
    result = build_tier_baseline({"id": "test", "name": "测试"}, games + [_game(B, 600, 10)], 6, {}, cfg)
    assert result["per_player"]["matches"] == [3, 3]
    assert result["per_player"]["metrics"]["time_to_damage_ms"]["values"] == [300, 400]
    assert result["per_player"]["metrics"]["time_to_damage_ms"]["standard"] == 350
    assert result["per_player"]["metrics"]["time_to_damage_ms"]["q25"] == 325
    assert result["per_player"]["metrics"]["time_to_damage_ms"]["q75"] == 375


def test_summary_ladder_and_old_baseline_fallback():
    cfg = load_config()
    games = []
    for index, ttd in enumerate((200, 250, 300)):
        mm = _game(A, ttd, 10)
        ref = DemoRef(path=Path("synthetic.dem"), member=None, match_id=f"s{index}",
                      map_name="de_test", player_name="虚构玩家")
        games.append((ref, mm, ProblemReport(hits=[], disabled=[], skipped={}, baseline_source=None), (10, 10)))
    summary = aggregate_matches(A, games, None, cfg)
    tiers = {t["id"]: t for t in (_tier("5e_b", 1, 500, 1.4, 60, 30),
                                  _tier("5e_a", 2, 400, 1.2, 80, 40),
                                  _tier("5e_s", 3, 300, 1.0, 90, 50))}
    page = build_summary_page(summary, cfg, tiers)
    view = next(v for v in page.tiers if v.id == "5e_a")
    assert view.you[0] == 3.5
    assert view.ladder["ttd"].reached.level == 3
    assert view.strengths and view.strengths[0].id == "time_to_damage_ms"
    assert "最弱的是" in view.verdict
    assert page.ladder_levels and "8 名玩家" in page.ladder_note
    # 多场页改画横向阶梯条：每个维度带你的数值、各档标准（展示单位）和方向
    ttd = next(axis for axis in page.ladder_axes if axis["id"] == "ttd")
    assert [axis["id"] for axis in page.ladder_axes] == [axis["id"] for axis in cfg["radar"]["axes"]]
    assert ttd["you"] == 250 and ttd["lower_is_better"] is True and ttd["unit"] == "毫秒"
    assert [(point["level"], point["standard"]) for point in ttd["points"]] == [(1, 500), (2, 400), (3, 300)]
    assert check_page(page, cfg) == []
    single = build_match_page(games[0][1], games[0][2], [], cfg, tiers)
    assert single.ladder_levels == [] and single.tiers[0].comparisons
    old = {"5e_a": {k: v for k, v in tiers["5e_a"].items() if k not in ("per_player", "level")}}
    fallback = build_summary_page(summary, cfg, old)
    assert fallback.ladder_levels == [] and fallback.tiers[0].comparisons[0].id == "tier_median"
    assert fallback.ladder_axes == []


def test_summary_excludes_tier_demos_by_default(tmp_path, monkeypatch):
    from cs2coach.summary import discover
    root = tmp_path / "data/demos"
    public = root / "ordinary.dem"
    sample = root / "tiers/sample.dem"
    public.parent.mkdir(parents=True)
    sample.parent.mkdir(parents=True)
    public.touch()
    sample.touch()
    seen = []
    monkeypatch.setattr(discover, "inspect_player_demos", lambda sid, entries: seen.extend(entries) or [])
    discover.find_player_demos(A, root, [root / "tiers"])
    assert seen == [(public, None)]


def test_representative_scan_and_absent_target(tmp_path, monkeypatch):
    import scripts.build_tier_baseline as builder
    roster = tmp_path / "players.toml"
    roster.write_text('[[player]]\nname="虚构甲"\nsteamid="76561198000000001"\ndir="a"\n'
                      '[[player]]\nname="虚构乙"\nsteamid="76561198000000002"\ndir="b"\n', encoding="utf-8")
    for folder in ("a", "b"):
        root = tmp_path / folder
        root.mkdir()
        for index in range(3):
            (root / f"{index}.dem").touch()
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    entries = representative_entries({"players_file": "players.toml"})
    assert len(entries) == 6 and {sid for _, _, sid in entries} == {A, B}
    monkeypatch.setattr(builder, "resolve_demo", lambda path, member=None: path)
    monkeypatch.setattr(builder, "parse_match", lambda *_: SimpleNamespace(
        players={A: SimpleNamespace(coverage=1.0)}, shots=pd.DataFrame()))
    monkeypatch.setattr(builder, "Sight", lambda *_: SimpleNamespace(available=True))
    monkeypatch.setattr(builder, "shot_targets", lambda *_: pd.DataFrame(columns=["target", "target_angle"]))
    monkeypatch.setattr(builder, "_lobby_inputs", lambda *_: {})
    monkeypatch.setattr(builder, "find_moments", lambda *args, **kwargs: _game(A, 300, 4))
    monkeypatch.setattr(builder, "lobby_line", lambda *args, **kwargs: line(steamid=A))
    assert len(parse_one_tier_demo(tmp_path / "a/0.dem", None, load_config(), A)[0]) == 1
    with pytest.raises(ValueError, match="目标 SteamID 不在 demo 中"):
        parse_one_tier_demo(tmp_path / "b/0.dem", None, load_config(), B)
