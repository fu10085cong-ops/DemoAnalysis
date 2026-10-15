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
    baseline = {"problem_rates": {pid: {"per_10_rounds": rate} for pid, rate in (rates or {}).items()}}
    return problem_frequency(report(hits, disabled), make_mm(rounds=rounds), baseline, cfg or load_config())


def test_frequency_calculation_and_zero_rows():
    result = {r.problem: r for r in rows([hit("slow_reaction")] * 7, {"slow_reaction": 1.38}, rounds=28)}
    r = result["slow_reaction"]
    assert r.hits == 7 and r.per_10_rounds == 2.5 and r.pro_per_10_rounds == 1.38
    assert r.ratio == pytest.approx(2.5 / 1.38) and r.prominent
    assert r.label == PROBLEMS["slow_reaction"].label
    assert result["poor_crosshair_placement"].hits == result["poor_crosshair_placement"].per_10_rounds == 0


@pytest.mark.parametrize("count,pro,expected", [(2, 4/3, True), (1, 1/3, False), (2, 2/1.49, False), (3, 1, True)])
def test_prominence_requires_both_conditions_and_includes_equality(count, pro, expected):
    r = next(r for r in rows([hit("slow_reaction")] * count, {"slow_reaction": pro}) if r.problem == "slow_reaction")
    assert r.prominent == expected


def test_frequency_thresholds_are_configurable():
    cfg = load_config()
    cfg["problems"].update(frequency_min_hits=3, frequency_prominent_ratio=2)
    r = next(r for r in rows([hit("slow_reaction")] * 2, {"slow_reaction": 1}, cfg=cfg) if r.problem == "slow_reaction")
    assert not r.prominent
    r = next(r for r in rows([hit("slow_reaction")] * 3, {"slow_reaction": 1.5}, cfg=cfg) if r.problem == "slow_reaction")
    assert r.prominent


@pytest.mark.parametrize("baseline,expected_pro", [(None, None), ({}, None),
    ({"problem_rates": {"slow_reaction": {"per_10_rounds": 0}}}, 0)])
def test_zero_or_missing_pro_frequency(baseline, expected_pro):
    result = problem_frequency(report([hit("slow_reaction")] * 5), make_mm(rounds=20), baseline, load_config())
    r = next(r for r in result if r.problem == "slow_reaction")
    assert r.pro_per_10_rounds == expected_pro and r.ratio is None and not r.prominent


def test_disabled_highlights_and_unimplemented_ids_are_not_listed():
    cfg = load_config()
    result = rows([hit(pid) for pid in PROBLEMS], disabled=["slow_reaction"])
    listed = {r.problem for r in result}
    assert not set(cfg["problems"]["disabled"]).intersection(listed)
    assert "slow_reaction" not in listed
    assert all(PROBLEMS[pid].category != "highlight" for pid in listed)
    assert listed <= set(DETECTED_PROBLEMS)
    assert "other" not in listed and "awp_close_range_duel" not in listed
    assert len(DETECTED_PROBLEMS) == 19


def test_match_hits_are_excluded_and_rounds_zero_is_safe():
    hits = [hit("spray_control"), hit("spray_control", "match"), hit("spray_control", "match")]
    r = next(r for r in rows(hits, {"spray_control": .2}) if r.problem == "spray_control")
    assert r.hits == 1 and r.per_10_rounds == 1 and not r.prominent
    assert all(r.per_10_rounds == 0 and not r.prominent for r in rows(hits, rounds=0))


def test_sort_prominent_first_then_ratio_with_missing_last():
    hits = [hit("slow_reaction")] * 2 + [hit("poor_crosshair_placement")] + [hit("shot_while_moving")] * 3
    result = rows(hits, {"slow_reaction": 1, "poor_crosshair_placement": .1, "shot_while_moving": 3,
                         "dry_peek_with_utility": .2, "peeked_into_held_angle": 0})
    assert [r.problem for r in result[:4]] == ["slow_reaction", "poor_crosshair_placement", "shot_while_moving", "dry_peek_with_utility"]
    assert all(r.ratio is None for r in result[4:])


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
    cfg_file.write_text('[baseline]\nmin_engagements = 1\n', encoding="utf-8")
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
    mm = make_mm([make_moment(tags=["died_without_seeing", "weapon_not_ready"]),
                  make_moment(engagement={"player_blind": blind}), make_moment(weapon="awp"),
                  make_moment(engagement={"shots": shots})])
    baseline = {"spray": {"ak47": [{"from": 3, "to": 5, "hit_pct": 65}]}}
    current = detect_problems(mm, baseline, cfg)
    expected = {"died_without_seeing", "turned_into_flash", "weapon_not_ready", "spray_while_moving", "awp_unscoped_shot"}
    assert set(cfg["problems"]["disabled"]) == expected
    assert not expected.intersection(h.problem for h in current.hits)
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
    rep = report([hit("slow_reaction")] * 2)
    frequency = problem_frequency(rep, make_mm(rounds=10), {"problem_rates": {"slow_reaction": {"per_10_rounds": 1}}}, load_config())
    md = render_problem_report(rep, frequency)
    assert "| **出枪慢** | 2 | 2.00 / 1.00 | 2.00× |" in md
    assert "| 准星没提前放好 | 0 | 0.00 / — | — |" in md
    assert md.index("## 频率对比") < md.index("## 出枪慢")
    assert "证据：" in md and "停用与缺少数据" in md
