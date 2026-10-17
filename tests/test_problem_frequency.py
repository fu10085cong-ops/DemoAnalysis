"""问题频率、职业校准统计、新启用状态及报告输出。"""

import copy
import json
from types import SimpleNamespace

import pytest

from cs2coach.baseline import problem_rates
from cs2coach.config import load_config
from cs2coach.features.problems import (
    CONTEXT_ONLY, DETECTED_PROBLEMS, PROBLEMS, ProblemHit, ProblemReport,
    detect_problems, problem_frequency,
)
from cs2coach.report.problems import render_problem_report
from tests.helpers.moments import make_mm, make_moment, make_shot
from tests.helpers.synthetic import A


def hit(problem, target="R1-death"):
    return ProblemHit(problem=problem, target=target, weapon="ak47", evidence={}, thresholds={})


def report(hits=None, disabled=None):
    return ProblemReport(hits=hits or [], disabled=disabled or [], skipped={}, baseline_source="手工职业样本")


def rows(hits=None, rates=None, rounds=10, cfg=None, disabled=None):
    baseline = {"problem_rates": {pid: {"hits": count, "rounds": sample_rounds}
                for pid, (count, sample_rounds) in (rates or {}).items()}}
    return problem_frequency(report(hits, disabled), make_mm(rounds=rounds), baseline, cfg or load_config())


def test_frequency_calculation_and_zero_rows():
    result = {r.problem: r for r in rows([hit("slow_reaction")] * 7, {"slow_reaction": (138, 1000)}, rounds=28)}
    r = result["slow_reaction"]
    assert r.hits == 7 and r.per_10_rounds == 2.5 and r.pro_per_10_rounds == 1.38
    assert r.ratio == pytest.approx(2.5 / 1.38)
    assert r.expected_hits == pytest.approx(3.864) and r.excess_hits == pytest.approx(3.136)
    assert r.label == PROBLEMS["slow_reaction"].label
    assert result["poor_crosshair_placement"].hits == result["poor_crosshair_placement"].per_10_rounds == 0


def test_reference_poisson_values_and_excess_sorting():
    hits = [hit("poor_crosshair_placement")] * 13 + [hit("dry_peek_with_utility")] * 2
    result = rows(hits, {"poor_crosshair_placement": (162, 1000),
                         "dry_peek_with_utility": (15, 1000)}, rounds=30)
    crosshair = next(r for r in result if r.problem == "poor_crosshair_placement")
    dry_peek = next(r for r in result if r.problem == "dry_peek_with_utility")
    assert crosshair.expected_hits == pytest.approx(4.86, abs=.001)
    assert crosshair.excess_hits == pytest.approx(8.14, abs=.001)
    assert crosshair.chance == pytest.approx(.002, abs=.001) and crosshair.prominent
    assert dry_peek.expected_hits == pytest.approx(.45, abs=.001)
    assert dry_peek.excess_hits == pytest.approx(1.55, abs=.001)
    assert dry_peek.chance == pytest.approx(.075, abs=.001) and not dry_peek.prominent
    assert dry_peek.needs_more_matches
    assert result[0].problem == "poor_crosshair_placement"


def test_baseline_rate_uses_raw_hits_and_rounds_not_rounded_display():
    baseline = {"problem_rates": {"slow_reaction": {
        "hits": 1, "rounds": 7, "per_10_rounds": 1.43}}}
    row = next(r for r in problem_frequency(report([hit("slow_reaction")] * 5),
                 make_mm(rounds=30), baseline, load_config()) if r.problem == "slow_reaction")
    assert row.expected_hits == pytest.approx(30 / 7, abs=.001)
    assert row.pro_per_10_rounds == pytest.approx(10 / 7, abs=.001)


def test_missing_raw_rate_is_not_substituted_with_rounded_rate():
    baseline = {"problem_rates": {"slow_reaction": {"per_10_rounds": 1.43}}}
    row = next(r for r in problem_frequency(report([hit("slow_reaction")] * 5),
                 make_mm(rounds=30), baseline, load_config()) if r.problem == "slow_reaction")
    assert row.expected_hits is None and row.chance is None and not row.prominent


def test_frequency_thresholds_are_configurable():
    cfg = load_config()
    sample = rows([hit("slow_reaction")] * 13, {"slow_reaction": (162, 1000)}, rounds=30)[0]
    cfg["problems"].update(frequency_max_chance=sample.chance,
                            frequency_min_excess=sample.excess_hits)
    r = next(r for r in rows([hit("slow_reaction")] * 13,
                             {"slow_reaction": (162, 1000)}, rounds=30, cfg=cfg)
             if r.problem == "slow_reaction")
    assert r.excess_hits == cfg["problems"]["frequency_min_excess"] and not r.prominent
    cfg["problems"]["frequency_max_chance"] += 1e-8
    assert next(r for r in rows([hit("slow_reaction")] * 13,
                                {"slow_reaction": (162, 1000)}, rounds=30, cfg=cfg)
                if r.problem == "slow_reaction").prominent
    cfg["problems"]["frequency_min_excess"] += 1e-8
    assert not next(r for r in rows([hit("slow_reaction")] * 13,
                                    {"slow_reaction": (162, 1000)}, rounds=30, cfg=cfg)
                    if r.problem == "slow_reaction").prominent


@pytest.mark.parametrize("baseline,expected_pro", [(None, None), ({}, None),
    ({"problem_rates": {"slow_reaction": {"hits": 0, "rounds": 100}}}, 0)])
def test_zero_or_missing_pro_frequency(baseline, expected_pro):
    result = problem_frequency(report([hit("slow_reaction")] * 5), make_mm(rounds=20), baseline, load_config())
    r = next(r for r in result if r.problem == "slow_reaction")
    assert r.pro_per_10_rounds == expected_pro and r.ratio is None and not r.prominent and r.chance is None


def test_disabled_highlights_and_unimplemented_ids_are_not_listed():
    cfg = load_config()
    result = rows([hit(pid) for pid in PROBLEMS], disabled=["slow_reaction"])
    listed = {r.problem for r in result}
    assert not set(cfg["problems"]["disabled"]).intersection(listed)
    assert "slow_reaction" not in listed
    assert all(PROBLEMS[pid].category != "highlight" for pid in listed)
    assert listed <= set(DETECTED_PROBLEMS)
    assert "other" not in listed and "awp_close_range_duel" not in listed
    assert len(DETECTED_PROBLEMS) == 21


def test_match_hits_are_excluded_and_rounds_zero_is_safe():
    hits = [hit("spray_control"), hit("spray_control", "match"), hit("spray_control", "match")]
    r = next(r for r in rows(hits, {"spray_control": (2, 100)}) if r.problem == "spray_control")
    assert r.hits == 1 and r.per_10_rounds == 1 and not r.prominent
    assert all(r.per_10_rounds == 0 and not r.prominent for r in rows(hits, rounds=0))


def test_sort_prominent_first_then_excess_with_missing_last():
    hits = ([hit("slow_reaction")] * 13 + [hit("poor_crosshair_placement")] * 10
            + [hit("dry_peek_with_utility")] * 2)
    result = rows(hits, {"slow_reaction": (162, 1000),
                         "poor_crosshair_placement": (100, 1000),
                         "dry_peek_with_utility": (15, 1000)}, rounds=30)
    assert [r.problem for r in result[:3]] == ["slow_reaction", "poor_crosshair_placement",
                                            "dry_peek_with_utility"]
    assert result[0].prominent and result[1].prominent and not result[2].prominent
    assert result[-1].excess_hits is None


def test_calibration_counts_match_filter_and_player_share_without_mutating_config(monkeypatch):
    cfg = load_config()
    original = copy.deepcopy(cfg)
    baseline = {"weapons": {"ak47": {"time_to_damage_ms": {"p75": 432}}}}
    mms = [make_mm(rounds=20), make_mm(rounds=10)]
    answers = [report([hit("slow_reaction"), hit("slow_reaction"), hit("weapon_not_ready"), hit("spray_control", "match")]),
               report([hit("slow_reaction"), hit("good_trade"), hit("spray_control", "match")])]
    calls = []
    def detect(mm, new_baseline, calibration):
        assert new_baseline is baseline
        assert calibration["problems"]["disabled"] == []
        assert calibration["problems"]["spray_match_enabled"] is True
        calls.append(mm)
        return answers[len(calls)-1]
    monkeypatch.setattr("cs2coach.features.problems.detect_problems", detect)
    rates = problem_rates(mms, baseline, cfg)
    assert cfg == original and calls == mms
    assert rates["slow_reaction"] == {"hits": 3, "rounds": 30, "per_10_rounds": 1, "player_share_pct": 100}
    assert rates["weapon_not_ready"] == {"hits": 1, "rounds": 30, "per_10_rounds": .33, "player_share_pct": 50}
    assert rates["good_trade"]["player_share_pct"] == 50
    assert rates["spray_control"]["hits"] == rates["spray_control"]["player_share_pct"] == 0
    assert "other" not in rates


def test_calibration_reenables_default_disabled_detection_and_handles_empty_samples():
    cfg = load_config()
    mm = make_mm([make_moment(tags=["weapon_not_ready"])], rounds=10)
    assert "weapon_not_ready" not in {h.problem for h in detect_problems(mm, None, cfg).hits}
    assert problem_rates([mm], {}, cfg)["weapon_not_ready"]["hits"] == 1
    assert all(v == {"hits": 0, "rounds": 0, "per_10_rounds": 0, "player_share_pct": 0} for v in problem_rates([], {}, cfg).values())


def test_build_script_uses_fresh_thresholds_instead_of_existing_file(tmp_path, monkeypatch):
    from scripts import build_baseline as script

    cfg_file = tmp_path / "sample.toml"
    cfg_file.write_text('[baseline]\nmin_engagements = 1\nmin_metric_samples = 1\n', encoding="utf-8")
    out = tmp_path / "baseline.json"
    out.write_text(json.dumps({"weapons": {"ak47": {"time_to_damage_ms": {"p75": 9999}}}}), encoding="utf-8")
    match = SimpleNamespace(players={A: SimpleNamespace(name="alice", coverage=1.0)},
                            meta=SimpleNamespace(map_name="de_test"), demo_path=tmp_path / "test.dem")
    mm = make_mm([make_moment(kind="kill", id=f"R1-kill{i}", engagement={"time_to_damage_s": t})
                  for i, t in enumerate((.2, .4, .6, .8), 1)], rounds=10)
    monkeypatch.setattr(script, "resolve_demo", lambda p, member=None: p)
    monkeypatch.setattr(script, "parse_match", lambda p, cfg: match)
    monkeypatch.setattr(script, "Sight", lambda m, cfg: SimpleNamespace(available=True))
    monkeypatch.setattr(script, "find_moments", lambda *args, **kwargs: mm)
    monkeypatch.setattr("cs2coach.baseline.load_baseline", lambda *args: pytest.fail("不能读取旧基准"))
    assert script.main([str(match.demo_path), "--config", str(cfg_file), "--out", str(out)]) == 0
    baseline = json.loads(out.read_text(encoding="utf-8"))
    assert baseline["weapons"]["ak47"]["time_to_damage_ms"]["p75"] == 650
    assert baseline["problem_rates"]["slow_reaction"] == {"hits": 1, "rounds": 10, "per_10_rounds": 1, "player_share_pct": 100}


def test_default_disabled_rules_and_match_toggle_preserve_detection_conditions():
    cfg = load_config()
    blind = dict(blinded_at_event=True, remaining_at_event_s=1, max_duration_s=2,
                 thrower=None, thrower_ambiguous=False, thrower_role="enemy")
    shots = [make_shot(tick=i, recoil_index=3, stopped=False) for i in range(30)]
    mm = make_mm([make_moment(tags=["died_without_seeing", "weapon_not_ready"], engagement={"opponent_off_screen": True}),
                  make_moment(engagement={"player_blind": blind}), make_moment(weapon="awp"),
                  make_moment(engagement={"shots": shots}), make_moment(engagement={"contact_type": "peek"})])
    baseline = {"spray": {"ak47": [{"from": 3, "to": 5, "hit_pct": 65}]}}
    current = detect_problems(mm, baseline, cfg)
    expected = {"turned_into_flash", "weapon_not_ready", "spray_while_moving", "awp_unscoped_shot",
                "peeked_into_held_angle"}  # 032 降为背景
    assert set(cfg["problems"]["disabled"]) == expected
    assert not expected.intersection(h.problem for h in current.hits)
    assert any(h.problem == "died_without_seeing" for h in current.hits)  # 2026-10-17 启用
    assert not any(h.target == "match" for h in current.hits)
    assert any(h.problem == "spray_control" and h.target != "match" for h in current.hits)
    cfg["problems"].update(disabled=[], spray_match_enabled=True)
    enabled = detect_problems(mm, baseline, cfg)
    assert expected <= {h.problem for h in enabled.hits}
    assert any(h.target == "match" for h in enabled.hits)


@pytest.mark.parametrize("pid", ["turned_into_flash", "weapon_not_ready"])
def test_background_and_disabled_status_automatically_reaches_checker(pid):
    from cs2coach.tools.checker import check_submission
    from cs2coach.tools.submission import parse_submission

    assert pid in CONTEXT_ONLY
    cfg = load_config()
    sub = parse_submission(dict(overview="需要复盘。", findings=[dict(problem=pid, targets=["R1-death"],
        severity="low", analysis="需要复盘。", training="训练。")], patterns=[], highlights=[], training_plan=["训练。"] ))
    violations = check_submission(sub, make_mm([make_moment()]), report(), "", cfg)
    assert {"context_only", "disabled_problem"} <= {v.code for v in violations}


def test_frequency_table_precedes_details_and_marks_prominent_rows():
    rep = report([hit("slow_reaction")] * 3)
    frequency = problem_frequency(rep, make_mm(rounds=10), {"problem_rates": {
        "slow_reaction": {"hits": 10, "rounds": 1000}}}, load_config())
    md = render_problem_report(rep, frequency)
    assert "| 问题 | 本场次数 | 按职业频率预期 | 多出的次数 | 偶然概率 | 倍数 |" in md
    assert "| **出枪慢** | 3 | 0.10 | 2.90 |" in md
    assert "| 准星没提前放好 | 0 | — | — | — | — |" in md
    assert md.index("## 频率对比") < md.index("## 出枪慢")
    assert "证据：" in md and "停用与缺少数据" in md


def test_table_marks_nonprominent_hits_with_high_chance():
    rep = report([hit("dry_peek_with_utility")] * 2)
    rows_ = problem_frequency(rep, make_mm(rounds=30), {"problem_rates": {
        "dry_peek_with_utility": {"hits": 15, "rounds": 1000}}}, load_config())
    md = render_problem_report(rep, rows_)
    assert "次数较少，需要更多比赛确认" in md
    assert "| 2 | 0.45 | 1.55 | 7.5% | 4.44× |" in md


@pytest.mark.parametrize("count,pro_hits,pro_rounds,expected_label", [
    (1, 10, 100, "不高于职业水平"),
    (3, 10, 100, "不高于职业水平"),
    (2, 15, 1000, "次数较少，需要更多比赛确认"),
])
def test_frequency_label_follows_excess_sign(count, pro_hits, pro_rounds, expected_label):
    rep = report([hit("dry_peek_with_utility")] * count)
    frequency = problem_frequency(rep, make_mm(rounds=30), {"problem_rates": {
        "dry_peek_with_utility": {"hits": pro_hits, "rounds": pro_rounds}}}, load_config())
    row = next(r for r in frequency if r.problem == "dry_peek_with_utility")
    md = render_problem_report(rep, frequency)
    assert not row.prominent
    assert (row.excess_hits > 0) == row.needs_more_matches
    assert f"{row.label}（{expected_label}）" in md
    other_label = ("不高于职业水平" if row.excess_hits > 0
                   else "次数较少，需要更多比赛确认")
    assert f"{row.label}（{other_label}）" not in md


def test_positive_excess_below_minimum_still_needs_more_matches():
    result = rows([hit("dry_peek_with_utility")] * 2,
                  {"dry_peek_with_utility": (1, 100)}, rounds=10)
    row = next(r for r in result if r.problem == "dry_peek_with_utility")
    assert row.excess_hits == pytest.approx(1.9)
    assert row.chance < load_config()["problems"]["frequency_max_chance"]
    assert not row.prominent and row.needs_more_matches


def test_frequency_uses_rounds_played_when_player_was_absent():
    # 30 回合中前 3 回合不在场（中途重连）：频率和预期次数按 27 个在场回合计算
    mm = make_mm(rounds=30, absent_rounds=[1, 2, 3])
    assert mm.rounds_played == 27
    baseline = {"problem_rates": {"slow_reaction": {"hits": 1, "rounds": 10}}}
    r = {x.problem: x for x in problem_frequency(report([hit("slow_reaction")] * 9), mm, baseline,
                                                  load_config())}["slow_reaction"]
    assert r.per_10_rounds == pytest.approx(90 / 27) and r.expected_hits == pytest.approx(2.7)


def test_pro_rates_use_rounds_played():
    cfg = load_config()
    out = problem_rates([make_mm(rounds=20, absent_rounds=[1, 2]), make_mm(rounds=10)], {}, cfg)
    assert out["slow_reaction"]["rounds"] == 28
