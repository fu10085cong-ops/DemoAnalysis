"""击杀/死亡的统称、与队友距离的说法、地图官方中文名（DeepSeek flash 汇总报告暴露，2026-10-16）。"""

import copy

import pytest

from cs2coach.config import ConfigError, _validate_checker, load_config
from cs2coach.features.models import MissedTrade
from cs2coach.features.problems import ProblemHit, ProblemReport
from cs2coach.tools.checker import check_submission
from cs2coach.tools.submission import parse_submission
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import B, C


def _report(problem, *targets):
    return ProblemReport(hits=[ProblemHit(problem=problem, target=t, weapon=None, evidence={}, thresholds={})
                               for t in targets], disabled=[], skipped={}, baseline_source="测试")


def _phrases(analysis, mm, report, problem="slow_reaction", targets=("R1-death", "R2-kill1")):
    sub = parse_submission(dict(overview="本场值得复盘。", findings=[dict(
        problem=problem, targets=list(targets), severity="medium", analysis=analysis,
        training="回看录像。", drills=[])], patterns=[], highlights=[], training_plan=["回看录像。"]))
    return [v for v in check_submission(sub, mm, report, "地图 de_test。", load_config())
            if v.code == "phrase_unsupported"]


def _mixed():
    death = make_moment()
    kill = make_moment(kind="kill", id="R2-kill1", round=2)
    return make_mm([death, kill], rounds=2), _report("slow_reaction", "R1-death", "R2-kill1")


def test_death_summary_requires_every_target_to_be_a_death():
    mm, report = _mixed()
    bad = _phrases("仅凭这两次死亡还不能断言直接输掉回合。", mm, report)  # flash 原文
    assert len(bad) == 1 and "R2-kill1" in bad[0].message and "death" in bad[0].message
    two_deaths = make_mm([make_moment(), make_moment(id="R2-death", round=2)], rounds=2)
    assert _phrases("这两次死亡都发生在接敌之后。", two_deaths, _report("slow_reaction", "R1-death", "R2-death"),
                    targets=("R1-death", "R2-death")) == []


def test_kill_summary_requires_every_target_to_be_a_kill():
    mm, report = _mixed()
    assert _phrases("这两次击杀都靠甩枪。", mm, report)
    assert _phrases("两处都完成击杀。", mm, report)
    # “都被击杀”说的是死亡，不触发击杀规则；单独描述其中一次也不触发
    killed = _phrases("两次都被击杀。", mm, report)
    assert killed and all("都满足 death" in v.message for v in killed)
    assert _phrases("一次死亡、一次击杀，共同点是出枪慢。", mm, report) == []


def test_round_targets_about_teammate_deaths_are_not_checked_by_kind():
    row = MissedTrade(round=1, tick=500, teammate=B, killer=C, distance=37.3, killer_traded_by_other=False)
    mm = make_mm(missed_trades=[row])
    assert _phrases("队友两次阵亡都没补上。", mm, _report("missed_trade", "R1"),
                    problem="missed_trade", targets=("R1",)) == []


def _trade_mm(*distances):
    rows = [MissedTrade(round=1, tick=500 + i, teammate=B, killer=C, distance=d, killer_traded_by_other=False)
            for i, d in enumerate(distances)]
    return make_mm(missed_trades=rows), _report("missed_trade", "R1")


def test_spread_out_claim_needs_a_far_teammate_distance():
    mm, report = _trade_mm(37.3, 674.0)
    bad = _phrases("这类情况多发生在自己和队友站位偏散时。", mm, report, problem="missed_trade", targets=("R1",))
    assert len(bad) == 1 and "37.3" in bad[0].message and "674" in bad[0].message and "1000" in bad[0].message
    mm, report = _trade_mm(37.3, 1200.0)
    assert _phrases("这一次离队友太远。", mm, report, problem="missed_trade", targets=("R1",)) == []


def test_close_claim_needs_a_near_teammate_distance():
    mm, report = _trade_mm(674.0)
    assert _phrases("当时离队友很近却没补上。", mm, report, problem="missed_trade", targets=("R1",))
    mm, report = _trade_mm(37.3)
    assert _phrases("当时离队友很近却没补上。", mm, report, problem="missed_trade", targets=("R1",)) == []


def test_death_moment_nearest_teammate_distance_counts():
    mm = make_mm([make_moment(trade={"nearest_teammate_distance": 1500.0})])
    report = _report("slow_reaction", "R1-death")
    assert _phrases("阵亡时离队友太远，没人能补。", mm, report, targets=("R1-death",)) == []
    near = make_mm([make_moment(trade={"nearest_teammate_distance": 1500.0})])
    assert _phrases("队友就在身边。", near, report, targets=("R1-death",))


def test_opponent_distance_wording_is_not_a_teammate_claim():
    mm, report = _trade_mm(37.3)
    assert _phrases("和对手距离较远，属于远距离对枪。", mm, report, problem="missed_trade", targets=("R1",)) == []


def test_official_chinese_map_names_are_checked():
    cfg = load_config()
    mm = make_mm([make_moment()], map_name="de_mirage")
    sub = parse_submission(dict(overview="本场值得复盘。", findings=[dict(
        problem="slow_reaction", targets=["R1-death"], severity="medium", analysis="炙热沙城的这次交火出枪慢。",
        training="回看录像。", drills=[])], patterns=[], highlights=[], training_plan=["回看录像。"]))
    bad = [v for v in check_submission(sub, mm, _report("slow_reaction", "R1-death"), "地图 de_mirage。", cfg)
           if v.code == "map_mismatch"]
    assert bad and "de_mirage" in bad[0].message
    ok = sub.model_copy(update={"findings": [sub.findings[0].model_copy(update={"analysis": "荒漠迷城的这次交火出枪慢。"})]})
    assert not [v for v in check_submission(ok, mm, _report("slow_reaction", "R1-death"), "地图 de_mirage。", cfg)
                if v.code == "map_mismatch"]


def test_phrase_scope_and_distance_config_validation():
    base = load_config()["checker"]
    bad_scope = copy.deepcopy(base)
    bad_scope["phrases"][0]["scope"] = "most"
    with pytest.raises(ConfigError):
        _validate_checker(bad_scope)
    distance_all = copy.deepcopy(base)
    distance_all["phrases"].append({"pattern": "远", "require": "teammate_far", "scope": "all"})
    with pytest.raises(ConfigError):
        _validate_checker(distance_all)
    no_far = copy.deepcopy(base)
    del no_far["teammate_far_distance"]
    with pytest.raises(ConfigError):
        _validate_checker(no_far)
