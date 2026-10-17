"""022：说法按句子定位、分段主次、残局与回防。"""

from pathlib import Path
import copy

import pytest

from cs2coach.config import ConfigError, _validate_checker, load_config
from cs2coach.features.labels import WEAPON_LABELS
from cs2coach.features.models import MissedTrade
from cs2coach.features.problems import ProblemHit, ProblemReport
from cs2coach.summary.aggregate import aggregate_matches
from cs2coach.summary.discover import DemoRef
from cs2coach.tools.checker import check_submission, check_summary_submission
from cs2coach.tools.submission import parse_submission
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import A, B, C

CFG = load_config()


def test_segment_rule_requires_all_scope_and_config_ratio():
    config = copy.deepcopy(CFG["checker"])
    rule = next(p for p in config["phrases"] if p["require"] == "seen_to_shot_major")
    rule["scope"] = "any"
    with pytest.raises(ConfigError):
        _validate_checker(config)
    config = copy.deepcopy(CFG["checker"])
    config["segment_major_ratio"] = 0.7
    mm = make_mm([make_moment(engagement={"time_to_damage_s": 1.0,
        "seen_to_first_shot_s": 0.6, "first_shot_to_damage_s": 0.4})])
    config_full = copy.deepcopy(CFG)
    config_full["checker"] = config
    sub = parse_submission(dict(overview="值得复盘。", findings=[dict(problem="slow_reaction",
        targets=["R1-death"], severity="medium", analysis="主要慢在看见到开枪。",
        training="回看录像。", drills=[])], patterns=[], highlights=[], training_plan=[]))
    assert any(v.code == "phrase_unsupported" for v in check_submission(
        sub, mm, _hits("slow_reaction", "R1-death"), "地图 de_test。", config_full))


def _hits(problem, *targets):
    return ProblemReport(hits=[ProblemHit(problem=problem, target=t, weapon="ak47",
                                         evidence={}, thresholds={}) for t in targets],
                         disabled=[], skipped={}, baseline_source="测试")


def _bad(mm, report, targets, analysis, problem="slow_reaction", summary=None):
    sub = parse_submission(dict(overview="值得复盘。", findings=[dict(problem=problem, targets=list(targets),
        severity="medium", analysis=analysis, training="回看录像。", drills=[])],
        patterns=[], highlights=[], training_plan=[]))
    result = (check_summary_submission(sub, summary, "地图 de_test。", CFG, []) if summary else
              check_submission(sub, mm, report, "地图 de_test。", CFG))
    return [v for v in result if v.code == "phrase_unsupported"]


def test_distance_sentence_selects_the_mentioned_moment_and_checks_second_occurrence():
    near = make_moment(id="R1-death", context={"distance": 4.8})
    far = make_moment(id="R2-death", round=2, context={"distance": 28.0})
    mm = make_mm([near, far], rounds=2)
    report = _hits("slow_reaction", "R1-death", "R2-death")
    assert _bad(mm, report, ["R1-death", "R2-death"], "短距离交火值得复盘。") == []
    bad = _bad(mm, report, ["R1-death", "R2-death"],
               "短距离交火有问题。{R2-death.distance}这次短距离交火更明显。")
    assert len(bad) == 1 and "R2-death" in bad[0].message
    assert len(_bad(mm, report, ["R1-death", "R2-death"],
                    "{R2-death.distance}这次短距离交火，短距离对枪都较慢。")) == 1


@pytest.mark.parametrize("phrase,distance,should_fail", [
    ("近距离 AK 对拼", 19, True), ("近距离 AK 对拼", 8, False),
    ("近距离的 AWP 对狙", 19, True), ("远距离用 AK 对枪", 8, True),
    ("短距离拿沙鹰对枪", 19, True), ("中远距离的步枪交火", 8, True),
])
def test_distance_phrase_with_weapon(phrase, distance, should_fail):
    mm = make_mm([make_moment(context={"distance": distance})])
    bad = _bad(mm, _hits("slow_reaction", "R1-death"), ["R1-death"], phrase)
    assert bool(bad) == should_fail


@pytest.mark.parametrize("phrase", ["近距离和对枪节奏一快就更容易吃亏",
    "避免在中远距离与步枪正面对枪", "近距离 AK 的站位需要调整"])
def test_distance_advice_does_not_match(phrase):
    mm = make_mm([make_moment(context={"distance": 19})])
    assert _bad(mm, _hits("slow_reaction", "R1-death"), ["R1-death"], phrase) == []


def test_distance_weapon_keeps_original_sentence_offsets():
    near = make_moment(id="R1-death", context={"distance": 8})
    far = make_moment(id="R2-death", round=2, context={"distance": 19})
    mm = make_mm([near, far], rounds=2)
    report = _hits("slow_reaction", "R1-death", "R2-death")
    bad = _bad(mm, report, ["R1-death", "R2-death"],
               "近距离 AK 对拼没问题。{R2-death.distance}这次近距离的 AWP 对狙要复盘。")
    assert len(bad) == 1 and "R2-death" in bad[0].message


def test_range_weapon_words_are_complete_and_validated():
    from cs2coach.config import _validate_checker
    words = CFG["checker"]["range_weapon_words"]
    assert set(WEAPON_LABELS.values()) <= set(words)
    assert {"AK", "M4", "AWP", "沙鹰", "大狙", "步枪", "手枪", "冲锋枪"} <= set(words)
    for invalid in ([], ["AK", "AK"], [""], [123]):
        checker = copy.deepcopy(CFG["checker"])
        checker["range_weapon_words"] = invalid
        with pytest.raises(ConfigError, match="range_weapon_words"):
            _validate_checker(checker)


@pytest.mark.parametrize("word", ["慢主要出在看见到开枪的这段", "其中大半时间花在看见到开枪之间",
    "共同点是从看见对手到打出第一枪的这一段偏长", "慢的主要是看见→开枪这一段", "都集中在开枪前",
    "主要慢在看见到开枪", "慢主要在看见到开枪这一段", "大半时间花在看见到开枪",
    "看见到开枪占了大半", "问题主要出在看见到开枪", "时间大部分耗在看见→开枪"])
def test_seen_to_shot_major(word):
    mm = make_mm([make_moment(engagement={"time_to_damage_s": 0.8,
        "seen_to_first_shot_s": 0.3, "first_shot_to_damage_s": 0.5})])
    report = _hits("slow_reaction", "R1-death")
    bad = _bad(mm, report, ["R1-death"], word)
    assert len(bad) == 1 and "R1-death" in bad[0].message
    mm.moments[0].engagement.seen_to_first_shot_s = 0.5
    assert _bad(mm, report, ["R1-death"], word) == []


@pytest.mark.parametrize("word", ["主要慢在开枪到命中", "大半时间花在开枪到命中",
    "慢主要出在开枪到命中", "问题主要出在开枪→命中这一段"])
def test_shot_to_hit_major_and_missing_ttd(word):
    mm = make_mm([make_moment(engagement={"time_to_damage_s": 0.8,
        "seen_to_first_shot_s": 0.5, "first_shot_to_damage_s": 0.3})])
    report = _hits("slow_reaction", "R1-death")
    assert _bad(mm, report, ["R1-death"], word)
    mm.moments[0].engagement.first_shot_to_damage_s = 0.5
    assert _bad(mm, report, ["R1-death"], word) == []
    mm.moments[0].engagement.time_to_damage_s = None
    assert _bad(mm, report, ["R1-death"], word)


def test_segment_major_checks_every_target():
    first = make_moment(id="R1-death", engagement={"time_to_damage_s": 0.8,
        "seen_to_first_shot_s": 0.5, "first_shot_to_damage_s": 0.3})
    second = make_moment(id="R2-death", round=2, engagement={"time_to_damage_s": 0.8,
        "seen_to_first_shot_s": 0.3, "first_shot_to_damage_s": 0.5})
    mm = make_mm([first, second], rounds=2)
    report = _hits("slow_reaction", "R1-death", "R2-death")
    bad = _bad(mm, report, ["R1-death", "R2-death"], "都集中在开枪前。")
    assert len(bad) == 1 and "R2-death" in bad[0].message
    assert "R1-death" in bad[0].message and "R2-death" in bad[0].message


@pytest.mark.parametrize("word", ["其中看见到开枪占了{R1-death.seen_to_shot}",
    "其中看见到开枪就占了 {R1-death.seen_to_shot}", "看见→开枪 594 毫秒",
    "大半时间花在偏长"])
def test_segment_values_do_not_claim_majority(word):
    mm = make_mm([make_moment(engagement={"time_to_damage_s": 0.8,
        "seen_to_first_shot_s": 0.3})])
    assert _bad(mm, _hits("slow_reaction", "R1-death"), ["R1-death"], word) == []


@pytest.mark.parametrize("word", ["回防或残局里确实来不及", "另一次回防中也是拉出撞上守点的 AWP",
    "在回防阶段被击杀",
    "残局对枪中", "这次残局击杀里", "一次残局里你主动拉出时"])
def test_retake_and_clutch_phrases(word):
    mm = make_mm([make_moment(context={"side": "CT", "alive_own": 4,
        "alive_enemy": 4, "bomb_planted": False})])
    report = _hits("slow_reaction", "R1-death")
    assert _bad(mm, report, ["R1-death"], word)
    mm.moments[0].context.alive_own = 1
    mm.moments[0].context.bomb_planted = True
    mm.moments[0].engagement.contact_type = "peek"
    assert _bad(mm, report, ["R1-death"], word) == []


@pytest.mark.parametrize("word", ["先用燃烧瓶封住对手可能回防的通道", "可以作为后续处理残局的参考",
    "局面从开局对枪到残局", "等对手回防时再补枪", "等敌人回防时再补枪", "等对面回防时再补枪"])
def test_generic_retake_and_clutch_words_are_ignored(word):
    mm = make_mm([make_moment()])
    assert _bad(mm, _hits("slow_reaction", "R1-death"), ["R1-death"], word) == []


def test_indexed_record_only_checks_second_record():
    rows = [MissedTrade(round=11, tick=100 + i, teammate=B, killer=C, distance=100,
                        killer_traded_by_other=False, side="CT", alive_own=own,
                        alive_enemy=3, bomb_planted=planted)
            for i, (own, planted) in enumerate(((1, True), (4, False)))]
    mm = make_mm(rounds=11, missed_trades=rows)
    report = _hits("missed_trade", "R11")
    bad = _bad(mm, report, ["R11"], "{R11[2].situation}这次回防或残局里来不及。", "missed_trade")
    assert len(bad) == 2 and all("R11[2]" in v.message for v in bad)
    assert _bad(mm, report, ["R11"], "{R11[1].situation}这次回防或残局里来不及。", "missed_trade") == []


def test_summary_objects_select_match_and_indexed_record():
    first = make_mm([make_moment()], map_name="de_test")
    rows = [MissedTrade(round=11, tick=100 + i, teammate=B, killer=C, distance=100,
                        killer_traded_by_other=False, side="CT", alive_own=own,
                        alive_enemy=3, bomb_planted=planted)
            for i, (own, planted) in enumerate(((1, True), (4, False)))]
    second = make_mm(rounds=11, missed_trades=rows)
    games = [(DemoRef(path=Path(f"{i}.dem"), member=None, match_id=str(i),
                      map_name="de_test", player_name="alice"), mm, report, (6, 4))
             for i, mm, report in ((1, first, _hits("slow_reaction", "R1-death")),
                                    (2, second, _hits("missed_trade", "R11")))]
    summary = aggregate_matches(A, games, None, CFG)
    bad = _bad(None, None, ["M1:R1-death"],
               "{M1:R1-death.situation}这次回防中交火。", summary=summary)
    assert len(bad) == 1 and "M1:R1-death" in bad[0].message
    bad = _bad(None, None, ["M2:R11"], "{M2:R11[2].situation}这次残局对枪中来不及。",
               problem="missed_trade", summary=summary)
    assert len(bad) == 1 and "M2:R11[2]" in bad[0].message
