"""职业基准 v2 的纯统计、读取兼容及清单生成流程，不读取真实 demo。"""

import copy
import json
from types import SimpleNamespace

import pytest

from cs2coach.baseline import (build_baseline, compare, engagement_weapon, is_pro_player,
                               pro_training_note, spray_segments, spray_stats, weapon_stats,
                               weapon_threshold)
from cs2coach.config import load_config
from tests.helpers.moments import make_mm, make_moment, make_shot
from tests.helpers.synthetic import A, B


def config(minimum=1):
    cfg = load_config()
    cfg["baseline"]["min_engagements"] = minimum
    cfg["baseline"]["min_metric_samples"] = 1
    return cfg


def test_pro_player_from_baseline_extra_list_and_legacy_baseline():
    cfg = config()
    cfg["training"]["extra_pro_steamids"] = [B]
    baseline = {"players": [{"steamid": A, "name": "alice"}]}
    assert is_pro_player(A, baseline, cfg)
    assert is_pro_player(B, baseline, cfg)
    assert not is_pro_player(123, baseline, cfg)
    assert not is_pro_player(A, {}, cfg)
    assert not is_pro_player(A, None, cfg)
    assert pro_training_note(A, baseline, cfg) == (
        "目标玩家是职业选手，本人就在职业样本中。训练建议面向职业水平："
        "聚焦细节、对局决策和针对性的情境练习，不推荐基础的瞄准、压枪练习图。"
        "与职业样本的比较，反映的是他和其他职业选手的差距。")
    assert "本人不在职业样本中" in pro_training_note(B, baseline, cfg)
    assert pro_training_note(123, baseline, cfg) is None


def test_engagement_weapon_uses_first_shot_in_time_not_kill_weapon():
    m = make_moment(weapon="awp", engagement={"shots": [make_shot(tick=95, weapon="glock"), make_shot(tick=90)]})
    assert engagement_weapon(m) == "ak47"


@pytest.mark.parametrize("display,expected", [("M4A4", "m4a1"), ("M4A1-S", "m4a1_silencer"),
                                               ("AWP", "awp"), ("unknown", None), (None, None)])
def test_engagement_weapon_falls_back_to_display_name(display, expected):
    assert engagement_weapon(make_moment(context={"own_weapon": display}, engagement={"shots": []})) == expected


def test_weapon_distributions_and_nullable_first_shot_denominators():
    ms = [make_moment(engagement={"time_to_damage_s": t, "crosshair_placement_deg": cp,
                                 "first_shot_stopped": stopped, "first_shot_hit": hit}, context={"distance": distance})
          for t, cp, stopped, hit, distance in [(0.2, 2, True, False, 10), (0.4, 4, False, True, 20),
                                               (0.6, 6, None, None, 30), (0.8, 8, True, True, 40)]]
    w = weapon_stats([make_mm(ms)], config(4))["ak47"]
    assert w["engagements"] == 4
    assert w["time_to_damage_ms"] == {"p50": 500.0, "p75": 650.0, "p90": 740.0, "n": 4}
    assert w["crosshair_placement_deg"] == {"p50": 5.0, "p75": 6.5, "p90": 7.4, "n": 4}
    assert w["distance_m"] == {"p50": 25.0, "p75": 32.5, "p90": 37.0, "n": 4}
    assert w["first_shot_stopped_pct"] == w["first_shot_hit_pct"] == 66.67
    assert w["first_shot_stopped_n"] == w["first_shot_hit_n"] == 3
    assert weapon_stats([make_mm(ms)], config(5)) == {}


def test_ttd_filter_and_blind_smoke_exclusions():
    ms = [make_moment(engagement={"time_to_damage_s": t, "visibility_note": note})
          for t, note in [(0.2, None), (1.0, None), (1.1, None), (0.3, "smoke"), (0.3, "blind"), (None, None)]]
    w = weapon_stats([make_mm(ms)], config())["ak47"]
    assert w["engagements"] == 6 and w["time_to_damage_ms"]["n"] == 1
    assert w["time_to_damage_ms"]["p75"] == 200
    assert w["crosshair_placement_deg"]["n"] == 4


@pytest.mark.parametrize("valid_n", [0, 1, 19, 20])
def test_weapon_metric_min_samples_checked_independently(valid_n):
    cfg = load_config()
    ms = [make_moment(engagement={
        "time_to_damage_s": 0.2 if i < valid_n else None,
        "seen_to_first_shot_s": 0.1 if i < valid_n else None,
        "crosshair_placement_deg": 2.0 if i < valid_n else None,
        "crosshair_initial_offset_m": 1.0 if i < valid_n else None,
    }, context={"distance": 10.0 if i < valid_n else None}) for i in range(30)]
    weapons = weapon_stats([make_mm(ms)], cfg)
    assert weapons["ak47"]["engagements"] == 30
    for metric, value in [("time_to_damage_ms", 200.0), ("seen_to_first_shot_ms", 100.0),
                          ("crosshair_placement_deg", 2.0), ("crosshair_initial_offset_m", 1.0),
                          ("distance_m", 10.0)]:
        expected = value if valid_n >= 20 else None
        assert weapons["ak47"][metric] == {
            "n": valid_n, "p50": expected, "p75": expected, "p90": expected}
        assert weapon_threshold({"weapons": weapons}, "ak47", metric, "p75") == expected


def test_weapon_metric_min_samples_is_configurable():
    cfg = config()
    cfg["baseline"]["min_metric_samples"] = 2
    ms = [make_moment(engagement={"time_to_damage_s": t}) for t in (0.2, None)]
    weapon = weapon_stats([make_mm(ms)], cfg)["ak47"]
    assert weapon["time_to_damage_ms"]["p75"] is None
    assert weapon["crosshair_placement_deg"]["p75"] is not None


def test_build_baseline_quartiles_stay_within_sample_range():
    metric = build_baseline({"time_to_damage_ms": [0, 100]}, "synthetic")["metrics"]["time_to_damage_ms"]
    assert metric["p25"] == 25
    assert metric["p75"] == 75


def test_crosshair_initial_baseline_excludes_turn_into_view():
    cfg = config()
    ms = [make_moment(engagement={"crosshair_initial_offset_m": offset,
                                  "opponent_in_sight_before_view_ticks": prior})
          for offset, prior in [(1.0, 0), (2.0, 7), (10.0, 8)]]
    dist = weapon_stats([make_mm(ms)], cfg)["ak47"]["crosshair_initial_offset_m"]
    assert dist == {"p50": 1.5, "p75": 1.75, "p90": 1.9, "n": 2}


@pytest.mark.parametrize("tag", ["after_round_end", "teamkill", "victim_is_bot", "world_damage"])
def test_baseline_excludes_flagged_moments(tag):
    mm = make_mm([make_moment(tags=[tag])])
    assert weapon_stats([mm], config()) == spray_stats([mm], config()) == {}


def test_baseline_excludes_assists_and_missing_engagements_and_weapons():
    mm = make_mm([make_moment(kind="assist"), make_moment(engagement=None),
                  make_moment(context={"own_weapon": "unknown"}, engagement={"shots": []})])
    assert weapon_stats([mm], config()) == spray_stats([mm], config()) == {}


def test_spray_segment_boundaries_and_missing_recoil():
    indices = [0, 1, 2, 3, 5, 6, 9, 10, 14, 15, 30, None]
    shots = [make_shot(tick=90 + i, recoil_index=x, hit=i % 2 == 0) for i, x in enumerate(indices)]
    shots += [make_shot(tick=110, weapon="glock", recoil_index=4)]
    s = spray_stats([make_mm([make_moment(engagement={"shots": shots})])], config())
    assert list(s) == ["ak47"]
    assert [(x["from"], x["to"], x["shots"], x["hit_pct"]) for x in s["ak47"]] == [
        (0, 0, 1, 100.0), (1, 2, 2, 50.0), (3, 5, 2, 50.0),
        (6, 9, 2, 50.0), (10, 14, 2, 50.0), (15, None, 2, 50.0)]


def test_spray_stats_excludes_unseen_and_unknown_visibility():
    shots = [make_shot(tick=90, recoil_index=3, hit=False, seen_opponent=True),
             make_shot(tick=91, recoil_index=3, hit=True, seen_opponent=False),
             make_shot(tick=92, recoil_index=3, hit=True, seen_opponent=None)]
    s = spray_stats([make_mm([make_moment(engagement={"shots": shots})])], config())
    segment = s["ak47"][2]
    assert segment["shots"] == 1 and segment["hit_pct"] == 0


def test_spray_uses_actual_shot_weapon_and_configured_segments():
    cfg = config()
    cfg["baseline"]["spray_segment_starts"] = [0, 4]
    shots = [make_shot(weapon="ak47", recoil_index=0), make_shot(weapon="m4a1", recoil_index=4, hit=True)]
    s = spray_stats([make_mm([make_moment(engagement={"shots": shots})])], cfg)
    assert s["ak47"][1]["shots"] == 0 and s["ak47"][1]["hit_pct"] is None
    assert s["m4a1"][1]["shots"] == 1 and s["m4a1"][1]["hit_pct"] == 100


def test_threshold_interfaces_and_compare_support_v2_and_missing_data():
    b = {"version": 2, **build_baseline({"time_to_damage_ms": [300, 400, 500]}, "synthetic"),
         "weapons": weapon_stats([make_mm([make_moment()])], config()),
         "spray": spray_stats([make_mm([make_moment()])], config())}
    assert compare("time_to_damage_ms", 400, b)["pro_median"] == 400
    assert weapon_threshold(b, "ak47", "time_to_damage_ms", "p75") == 300
    assert spray_segments(b, "ak47")[0]["shots"] == 1
    for baseline in (None, {}, {"version": 1}):
        assert weapon_threshold(baseline, "ak47", "time_to_damage_ms", "p75") is None
        assert spray_segments(baseline, "ak47") == []
    assert weapon_threshold(b, "mp9", "time_to_damage_ms", "p75") is None
    assert weapon_threshold(b, "ak47", "missing", "p75") is None
    assert spray_segments(b, "mp9") == []


def test_build_script_skips_failed_demos_and_low_coverage_players(tmp_path, monkeypatch):
    from scripts import build_baseline as script

    out = tmp_path / "baseline.json"
    match = SimpleNamespace(players={A: SimpleNamespace(name="alice", coverage=1.0),
                                     B: SimpleNamespace(name="coach", coverage=0.0)},
                            meta=SimpleNamespace(map_name="de_test"), demo_path=tmp_path / "good.dem")
    monkeypatch.setattr(script, "resolve_demo", lambda p, member=None: p)
    def parse(path, cfg):
        if path.name == "bad.dem":
            raise ValueError("synthetic failure")
        return match
    monkeypatch.setattr(script, "parse_match", parse)
    monkeypatch.setattr(script, "Sight", lambda m, cfg: SimpleNamespace(available=True))
    seen = []
    def find(m, sid, cfg, sight=None):
        seen.append(sid)
        return make_mm([make_moment()])
    monkeypatch.setattr(script, "find_moments", find)
    assert script.main([str(tmp_path / "bad.dem"), str(tmp_path / "good.dem"), "--out", str(out),
                        "--jobs", "1"]) == 0
    b = json.loads(out.read_text(encoding="utf-8"))
    assert b["version"] == 2 and len(b["demos"]) == 1 and seen == [A]
    assert b["players"] == [{"steamid": A, "name": "alice"}]
    assert "1 人次" in b["source"]
    previous = out.read_bytes()
    assert script.main([str(tmp_path / "bad.dem"), "--out", str(out), "--jobs", "1"]) == 1
    assert out.read_bytes() == previous
