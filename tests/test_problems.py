"""问题目录的必要条件、阈值边界、缺失数据及审查证据。"""

import copy
import json

import pytest

from cs2coach.config import load_config
from cs2coach.features.models import MissedTrade
from cs2coach.features.problems import CONTEXT_ONLY, PROBLEMS, detect_problems, engagement_weapon, problem_frequency
from cs2coach.report.problems import render_problem_report
from tests.helpers.moments import make_mm, make_moment, make_shot
from tests.helpers.synthetic import A, B, C


@pytest.fixture
def cfg():
    # 这里验证原有检测条件；默认启用状态由 test_problem_frequency 独立验证。
    cfg = load_config()
    cfg["problems"]["disabled"] = ["died_without_seeing"]
    cfg["problems"]["spray_match_enabled"] = True
    return cfg


@pytest.fixture
def baseline():
    stats = {"time_to_damage_ms": {"p75": 500.0}, "crosshair_placement_deg": {"p75": 10.0},
             "distance_m": {"p90": 30.0}}
    segments = [{"from": start, "to": end, "hit_pct": 65.0, "shots": 100}
                for start, end in [(0, 0), (1, 2), (3, 5), (6, 9), (10, 14), (15, None)]]
    return {"source": "手工职业样本", "weapons": {w: copy.deepcopy(stats) for w in
            ("ak47", "m4a1", "m4a1_silencer", "awp", "deagle", "glock")},
            "spray": {w: copy.deepcopy(segments) for w in ("ak47", "m4a1", "m4a1_silencer")}}


def blind(role="enemy", at_event=True):
    return dict(blinded_at_event=at_event, remaining_at_event_s=1.0, max_duration_s=2.0,
                thrower=C, thrower_ambiguous=False, thrower_role=role)


def bullets(n=6, start=3, hit=False, stopped=True, weapon="ak47"):
    return [make_shot(tick=80+i, weapon=weapon, recoil_index=start+i, hit=hit, stopped=stopped)
            for i in range(n)]


# 每个已实现的瞬间规则有一个命中与一个仅改变必要条件的不命中样本。
CASES = [
    ("slow_reaction", dict(engagement={"time_to_damage_s": .6}), dict(engagement={"time_to_damage_s": .5})),
    ("poor_crosshair_placement", dict(engagement={"crosshair_placement_deg": 11}), dict(engagement={"crosshair_placement_deg": 10})),
    ("shot_while_moving", dict(engagement={"first_shot_stopped": False}), dict(engagement={"first_shot_stopped": False, "first_shot_hit": True})),
    ("peeked_into_held_angle", dict(engagement={"contact_type": "peek"}), dict(engagement={"contact_type": "held"})),
    ("dry_peek_with_utility", dict(engagement={"contact_type": "peek"}, death={"unused_grenades": ["flashbang"]}),
     dict(engagement={"contact_type": "peek", "opponent_blind": blind()}, death={"unused_grenades": ["flashbang"]})),
    ("overpeek_in_advantage", dict(tags=["man_advantage"], engagement={"contact_type": "peek"}),
     dict(tags=["man_advantage"], engagement={"contact_type": "peek"}, trade={"traded": True})),
    ("died_without_seeing", dict(tags=["died_without_seeing"]), dict(tags=["died_without_seeing"], wallbang=True)),
    ("turned_into_flash", dict(engagement={"player_blind": blind()}), dict(engagement={"player_blind": blind("teammate")})),
    ("post_plant_utility_unused", dict(context={"bomb_planted": True}, death={"unused_grenades": ["molotov"]}),
     dict(context={"bomb_planted": True, "side": "CT"}, death={"unused_grenades": ["molotov"]})),
    ("weapon_not_ready", dict(tags=["weapon_not_ready"]), dict(tags=[])),
    ("awp_unscoped_shot", dict(weapon="awp"), dict(weapon="awp", context={"distance": 5})),
    ("deagle_moving_shot", dict(weapon="deagle", engagement={"first_shot_stopped": False, "first_shot_hit": True}),
     dict(weapon="deagle", engagement={"first_shot_stopped": True})),
    ("glock_long_range", dict(weapon="glock", context={"distance": 31}), dict(weapon="glock", context={"distance": 30})),
    ("spray_control", dict(engagement={"shots": bullets()}), dict(engagement={"shots": bullets(n=5)})),
    ("spray_while_moving", dict(engagement={"shots": bullets(n=4, start=1, stopped=False)}),
     dict(engagement={"shots": bullets(n=3, start=1, stopped=False)})),
    ("good_trade", dict(kind="kill", tags=["trade_kill"]), dict(kind="kill", tags=[])),
    ("flash_assist", dict(kind="assist", assist={"flash_assist": True}), dict(kind="assist", assist={"flash_assist": False})),
    ("won_duel_seen_first", dict(kind="kill", tags=["saw_opponent_first", "shot_first"]), dict(kind="kill", tags=["shot_first"])),
]


@pytest.mark.parametrize("pid,positive,negative", CASES, ids=[c[0] for c in CASES])
def test_each_moment_rule_has_both_sides(pid, positive, negative, cfg, baseline):
    cfg["problems"]["disabled"] = []
    yes = detect_problems(make_mm([make_moment(**positive)]), baseline, cfg)
    no = detect_problems(make_mm([make_moment(**negative)]), baseline, cfg)
    assert pid in {h.problem for h in yes.hits}
    assert pid not in {h.problem for h in no.hits}
    hit = next(h for h in yes.hits if h.problem == pid)
    assert hit.target.startswith("R1-")
    assert {"weapon", "round", "tick", "kind"} <= hit.evidence.keys()


def test_missed_trade_records_are_preserved(cfg, baseline):
    record = MissedTrade(round=5, tick=100, teammate=B, killer=C, distance=200, killer_traded_by_other=True)
    report = detect_problems(make_mm(missed_trades=[record, record]), baseline, cfg)
    assert len(report.hits) == 2
    assert report.hits[0].target == "R5"
    assert report.hits[0].weapon is None
    assert report.hits[0].evidence == {"weapon": None, **record.model_dump()}
    assert not detect_problems(make_mm(), baseline, cfg).hits


@pytest.mark.parametrize("tag", ["after_round_end", "teamkill", "victim_is_bot", "world_damage"])
def test_excluded_moments_never_contribute(tag, cfg, baseline):
    m = make_moment(tags=[tag, "weapon_not_ready"], engagement={"time_to_damage_s": .8, "shots": bullets(30)})
    report = detect_problems(make_mm([m]), baseline, cfg)
    assert report.hits == [] and report.skipped == {}


def test_disabled_rules(cfg, baseline):
    report = detect_problems(make_mm([make_moment(tags=["died_without_seeing"])]), baseline, cfg)
    assert "died_without_seeing" in report.disabled
    assert "died_without_seeing" not in {h.problem for h in report.hits}
    cfg["problems"]["disabled"] += ["slow_reaction", "spray_control", "missed_trade"]
    record = MissedTrade(round=1, tick=100, teammate=B, killer=C, distance=100, killer_traded_by_other=False)
    report = detect_problems(make_mm([make_moment(engagement={"time_to_damage_s": .8, "shots": bullets(30)})],
                                    missed_trades=[record]), None, cfg)
    assert not report.hits
    assert not set(cfg["problems"]["disabled"]).intersection(report.skipped)


@pytest.mark.parametrize("base", [None, {}, {"metrics": {}}])
def test_absent_baseline_runs_universal_rules(base, cfg):
    report = detect_problems(make_mm([make_moment(engagement={"time_to_damage_s": .8,
        "contact_type": "peek", "first_shot_stopped": False})]), base, cfg)
    assert {"slow_reaction", "poor_crosshair_placement"} <= report.skipped.keys()
    assert {"peeked_into_held_angle", "shot_while_moving"} <= {h.problem for h in report.hits}


def test_unknown_weapon_and_missing_quantile_are_skipped(cfg, baseline):
    m = make_moment(weapon="mp9", engagement={"time_to_damage_s": .8})
    report = detect_problems(make_mm([m]), baseline, cfg)
    assert report.skipped["slow_reaction"] == report.skipped["poor_crosshair_placement"] == 1
    baseline["weapons"]["ak47"]["time_to_damage_ms"]["p75"] = None
    assert detect_problems(make_mm([make_moment()]), baseline, cfg).skipped["slow_reaction"] == 1


@pytest.mark.parametrize("eng", [
    {"time_to_damage_s": 1}, {"time_to_damage_s": .8, "visibility_note": "smoke"},
    {"time_to_damage_s": .8, "visibility_note": "blind"},
    {"time_to_damage_s": .8, "player_seen_while_blind": True},
    {"time_to_damage_s": .8, "opponent_time_to_damage_s": None},
    {"time_to_damage_s": .8, "opponent_time_to_damage_s": .8},
    {"time_to_damage_s": None}, {"time_to_damage_s": float("nan")},
])
def test_slow_reaction_filters(eng, cfg, baseline):
    report = detect_problems(make_mm([make_moment(engagement=eng)]), baseline, cfg)
    assert "slow_reaction" not in {h.problem for h in report.hits}


def test_slow_reaction_kill_does_not_require_opponent_ttd(cfg, baseline):
    m = make_moment(kind="kill", engagement={"time_to_damage_s": .8, "opponent_time_to_damage_s": None})
    h = next(h for h in detect_problems(make_mm([m]), baseline, cfg).hits if h.problem == "slow_reaction")
    assert h.evidence["ttd_ms"] == 800 and h.evidence["opponent_ttd_ms"] is None
    assert h.thresholds == {"ttd_ms": 500, "source": "pro_p75", "ttd_max_seconds": 1.0, "ttd_max_source": "config"}


@pytest.mark.parametrize("role,at_event", [("self", True), (None, True), ("enemy", False), ("teammate", True)])
def test_blind_info_takes_priority_over_fallback(role, at_event, cfg, baseline):
    m = make_moment(engagement={"player_blind": blind(role, at_event)},
                    death={"blinded": True, "blinded_by_teammate": False})
    assert "turned_into_flash" not in {h.problem for h in detect_problems(make_mm([m]), baseline, cfg).hits}


@pytest.mark.parametrize("role", ["enemy", "opponent"])
def test_opponent_blind_roles(role, cfg, baseline):
    m = make_moment(engagement={"player_blind": blind(role)})
    assert "turned_into_flash" in {h.problem for h in detect_problems(make_mm([m]), baseline, cfg).hits}


@pytest.mark.parametrize("teammate,expected", [(False, True), (True, False), (None, False)])
def test_blind_fallback(teammate, expected, cfg, baseline):
    m = make_moment(death={"blinded": True, "blinded_by_teammate": teammate})
    assert ("turned_into_flash" in {h.problem for h in detect_problems(make_mm([m]), baseline, cfg).hits}) == expected


@pytest.mark.parametrize("overrides", [dict(through_smoke=True), dict(death={"blinded": True})])
def test_without_seeing_additional_guards(overrides, cfg, baseline):
    cfg["problems"]["disabled"] = []
    m = make_moment(tags=["died_without_seeing"], **overrides)
    assert "died_without_seeing" not in {h.problem for h in detect_problems(make_mm([m]), baseline, cfg).hits}


@pytest.mark.parametrize("overrides", [dict(engagement={"shots": []}),
    dict(engagement={"shots": [make_shot(weapon="awp", scoped=True)]}), dict(context={"distance": None})])
def test_awp_requires_real_unscoped_shot_and_distance(overrides, cfg, baseline):
    m = make_moment(weapon="awp", **overrides)
    assert "awp_unscoped_shot" not in {h.problem for h in detect_problems(make_mm([m]), baseline, cfg).hits}


def test_weapon_recognition_is_shared_and_uses_earliest_shot(cfg, baseline):
    from cs2coach.baseline import engagement_weapon as common
    assert engagement_weapon is common
    m = make_moment(weapon="awp", engagement={"shots": [make_shot(tick=95, weapon="awp"), make_shot(tick=90, weapon="deagle", stopped=False)],
                                            "first_shot_stopped": False})
    assert "deagle_moving_shot" in {h.problem for h in detect_problems(make_mm([m]), baseline, cfg).hits}


def test_spray_weighted_expectation_and_strict_boundary(cfg, baseline):
    # 三发落在 3–5 段（三发 90%），三发在 6–9 段（三发 40%）；期望 65%。
    baseline["spray"]["ak47"][2]["hit_pct"] = 90
    baseline["spray"]["ak47"][3]["hit_pct"] = 40
    shots = bullets()
    for s in shots[:3]:
        s.hit = True
    m = make_moment(engagement={"shots": shots})
    assert "spray_control" not in {h.problem for h in detect_problems(make_mm([m]), baseline, cfg).hits}
    shots[0].hit = False
    h = next(h for h in detect_problems(make_mm([make_moment(engagement={"shots": shots})]), baseline, cfg).hits
             if h.problem == "spray_control")
    assert h.thresholds["expected_hit_pct"] == 65
    assert h.thresholds["margin_pct"] == 15
    assert json.loads(h.thresholds["pro_hit_pcts"]) == [90, 90, 90, 40, 40, 40]
    assert h.evidence["shots"] == 6 and h.evidence["hits"] == 2


@pytest.mark.parametrize("missing", ["weapon", "segment", "pct"])
def test_spray_missing_reference_does_not_guess(missing, cfg, baseline):
    if missing == "weapon":
        baseline["spray"] = {}
    elif missing == "segment":
        del baseline["spray"]["ak47"][2]
    else:
        baseline["spray"]["ak47"][2]["hit_pct"] = None
    report = detect_problems(make_mm([make_moment(engagement={"shots": bullets()})]), baseline, cfg)
    assert report.skipped["spray_control"] == 1
    assert "spray_control" not in {h.problem for h in report.hits}


@pytest.mark.parametrize("n,hits,expected", [(30, 0, True), (29, 0, False), (30, 15, False), (30, 16, False)])
def test_match_spray_sample_and_margin_boundaries(n, hits, expected, cfg, baseline):
    shots = [make_shot(tick=i, recoil_index=3.0, hit=i < hits) for i in range(n)]
    report = detect_problems(make_mm([make_moment(engagement={"shots": shots})]), baseline, cfg)
    found = [h for h in report.hits if h.problem == "spray_control" and h.target == "match"]
    assert bool(found) == expected
    if found:
        assert found[0].evidence["from"] == 3 and found[0].evidence["to"] == 5
        assert found[0].evidence["shots"] == n and found[0].evidence["weapon"] == "ak47"
        assert found[0].thresholds["source"] == "pro_segment"


def test_match_spray_missing_baseline_and_mixed_weapon(cfg, baseline):
    shots = [make_shot(tick=i, recoil_index=3, weapon="m4a1") for i in range(30)]
    # 第一枪是 AK，整场仍按每发子弹实际武器统计，不能算成 AK 的压枪。
    shots.insert(0, make_shot(tick=-1))
    mm = make_mm([make_moment(engagement={"shots": shots})])
    report = detect_problems(mm, baseline, cfg)
    assert [(h.target, h.weapon) for h in report.hits if h.problem == "spray_control"] == [("match", "m4a1")]
    assert detect_problems(mm, None, cfg).skipped["spray_control"] == 1


@pytest.mark.parametrize("values,expected", [([False, False, True, True], False),
    ([False, False, False, True], True), ([False, False, False, None], False)])
def test_moving_spray_ratio_and_unknown_stopped(values, expected, cfg, baseline):
    shots = bullets(n=4, start=1)
    for s, value in zip(shots, values):
        s.stopped = value
    report = detect_problems(make_mm([make_moment(engagement={"shots": shots})]), baseline, cfg)
    assert ("spray_while_moving" in {h.problem for h in report.hits}) == expected
    if None in values:
        assert report.skipped["spray_while_moving"] == 1


def test_nonautomatic_and_unknown_recoil_not_counted(cfg, baseline):
    shots = bullets(30, weapon="deagle", stopped=False)
    shots += [make_shot(recoil_index=None, stopped=False)] * 10
    report = detect_problems(make_mm([make_moment(weapon="deagle", engagement={"shots": shots})]), baseline, cfg)
    assert not {"spray_control", "spray_while_moving"}.intersection(h.problem for h in report.hits)


def test_registry_context_and_markdown(cfg, baseline):
    assert len(PROBLEMS) == 25
    for pid in ("awp_close_range_duel", "awp_moving_shot", "overspray_at_range", "pistol_spam", "other"):
        assert pid in PROBLEMS
    assert {"unused_utility", "isolated_death", "opponent_saw_first", "opening_death", "man_advantage", "friendly_flash",
            "turned_into_flash", "weapon_not_ready"} == CONTEXT_ONLY.keys()
    report = detect_problems(make_mm([make_moment(engagement={"time_to_damage_s": .8})]), baseline, cfg)
    md = render_problem_report(report)
    assert "R1-death" in md and "出枪慢" in md and "手工职业样本" in md
    assert '"ttd_ms": 800.0' in md and '"source": "pro_p75"' in md
    assert "died_without_seeing" in md and "skipped" in md
    assert "无，仅检测" in render_problem_report(detect_problems(make_mm(), None, cfg))


def test_missing_engagement_is_safe(cfg, baseline):
    report = detect_problems(make_mm([make_moment(engagement=None, death=None, trade=None)]), baseline, cfg)
    assert report.hits == []
    assert report.skipped["slow_reaction"] == report.skipped["poor_crosshair_placement"] == 1


def test_missing_decision_inputs_are_counted(cfg, baseline):
    m = make_moment(tags=["man_advantage"], engagement={"contact_type": None,
        "first_shot_stopped": None, "player_blind": blind(None)})
    r = detect_problems(make_mm([m]), baseline, cfg)
    for pid in ("shot_while_moving", "peeked_into_held_angle", "dry_peek_with_utility",
                "overpeek_in_advantage", "turned_into_flash"):
        assert r.skipped[pid] == 1


def test_configured_thresholds_override_defaults(tmp_path, baseline):
    path = tmp_path / "thresholds.toml"
    path.write_text('[problems]\ndisabled = ["died_without_seeing"]\nspray_match_enabled = true\n'
                    'awp_noscope_max_distance_m = 11.0\nspray_min_late_shots = 7\n'
                    'spray_moving_ratio = 1.0\n', encoding="utf-8")
    cfg = load_config(path)
    mm = make_mm([make_moment(weapon="awp"),
                  make_moment(engagement={"shots": bullets(n=6, stopped=False)})])
    assert not {"awp_unscoped_shot", "spray_control", "spray_while_moving"}.intersection(
        h.problem for h in detect_problems(mm, baseline, cfg).hits)


def test_cli_writes_problem_report_without_ai_or_baseline(tmp_path, monkeypatch, cfg):
    from cs2coach.cli import main
    from tests.test_facts import scenario

    cfg = load_config()
    match = scenario()
    mm = make_mm([make_moment(tags=["weapon_not_ready"])])
    monkeypatch.setattr("cs2coach.cli.resolve_demo", lambda path, member=None: path)
    monkeypatch.setattr("cs2coach.parsing.match.parse_match", lambda path, cfg: match)
    monkeypatch.setattr("cs2coach.features.moments.find_moments", lambda match, sid, cfg, **kwargs: mm)
    monkeypatch.setattr("cs2coach.baseline.load_baseline", lambda: None)
    # 一旦误入 AI 分支就失败，确保无 AI 的新输出不需要密钥或外部请求。
    def unexpected(*args, **kwargs):
        pytest.fail("无 AI 模式不应进入诊断")
    monkeypatch.setattr("cs2coach.cli._diagnose", unexpected)
    assert main(["analyze", "test.dem", "--player", str(A), "--out", str(tmp_path)]) == 0
    detected = detect_problems(mm, None, cfg)
    expected = render_problem_report(detected, problem_frequency(detected, mm, None, cfg))
    assert (tmp_path / f"alice_{A}" / "test" / "problems.md").read_text(encoding="utf-8") == expected
