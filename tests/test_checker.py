"""检查器逐类违规、数字口径、敏感说法与真实数据回归。"""

import json
from pathlib import Path

import pytest

from cs2coach.config import ConfigError, load_config
from cs2coach.baseline import build_baseline
from cs2coach.features.models import MatchMoments, MissedTrade
from cs2coach.features.problems import FrequencyRow, ProblemHit, ProblemReport
from cs2coach.tools.checker import check_submission
from cs2coach.tools.digest import whole_match_numbers
from cs2coach.tools.submission import parse_submission
from tests.helpers.moments import make_mm, make_moment, make_shot
from tests.helpers.synthetic import B, C

FIXTURES = Path(__file__).parent / "fixtures"


def finding(problem="slow_reaction", targets=None, analysis="接敌准备需要加强。", **overrides):
    return dict(problem=problem, targets=["R1-death"] if targets is None else targets,
                severity="medium", analysis=analysis, training="每天练100次，持续987分钟。") | overrides


def submit(findings=None, **overrides):
    args = dict(overview="这是一次复盘。优先改善接敌准备。训练应持续进行。",
                findings=[finding()] if findings is None else findings,
                patterns=[], highlights=[], training_plan=["练习999次。"])
    return parse_submission(args | overrides)


def hit(problem="slow_reaction", target="R1-death", evidence=None, thresholds=None):
    return ProblemHit(problem=problem, target=target, weapon="ak47", evidence=evidence or {}, thresholds=thresholds or {})


def report(hits=None, disabled=None):
    return ProblemReport(hits=[hit()] if hits is None else hits, disabled=disabled or [], skipped={}, baseline_source="测试")


def check(sub=None, mm=None, rep=None, digest="本场共1回合。", cfg=None):
    return check_submission(sub or submit(), mm or make_mm([make_moment()]),
                            rep or report(), digest, cfg or load_config())


def codes(violations):
    return {v.code for v in violations}


def test_fully_legal_submission_and_training_numbers_ignored():
    assert check() == []


@pytest.mark.parametrize("target", ["R40-death", "R40", "R1-kill99", "invalid", "match"])
def test_unknown_target(target):
    violations = check(submit([finding(targets=[target])]))
    assert "unknown_target" in codes(violations)
    assert target in violations[0].message
    assert "1" in violations[0].message or "spray_control" in violations[0].message


def test_unknown_problem_and_context_only_are_distinguished():
    assert "unknown_problem" in codes(check(submit([finding(problem="invented")])) )
    violations = check(submit([finding(problem="unused_utility")]))
    assert codes(violations) == {"context_only"}
    assert "正相关" in violations[0].message


def test_disabled_problem_from_report_and_configuration():
    for rep in (report(disabled=["slow_reaction"]), report()):
        cfg = load_config()
        if not rep.disabled:
            cfg["problems"]["disabled"].append("slow_reaction")
        v = check(rep=rep, cfg=cfg)
        assert "disabled_problem" in codes(v)
        assert "slow_reaction" in v[0].message


def test_wrong_category_in_each_direction():
    assert "wrong_category" in codes(check(submit([finding(problem="good_trade")]), rep=report([hit("good_trade")])))
    s = submit(highlights=[dict(problem="slow_reaction", targets=["R1-death"], analysis="需要复盘。")])
    assert "wrong_category" in codes(check(s))
    s = submit(patterns=[dict(problem="good_trade", targets=["R1-death", "R2-death"], analysis="持续复盘。")])
    assert "wrong_category" in codes(check(s))


def test_failed_precondition_lists_actual_hits_and_empty_case():
    v = check(rep=report([hit("turned_into_flash"), hit("weapon_not_ready")]))
    assert codes(v) == {"precondition_failed"}
    assert "turned_into_flash" in v[0].message and "weapon_not_ready" in v[0].message
    assert "出枪慢" in v[0].message and v[0].path == "findings[0].targets[0]"
    assert "没有任何命中" in check(rep=report([]))[0].message


def test_every_target_requires_matching_problem_hit():
    mm = make_mm([make_moment(), make_moment(id="R2-death", round=2)], rounds=2)
    v = check(submit([finding(targets=["R1-death", "R2-death"])]), mm)
    assert len(v) == 1 and v[0].path == "findings[0].targets[1]"
    assert "R2-death" in v[0].message


def test_finding_target_limit_and_duplicate_problem():
    mm = make_mm([make_moment(), make_moment(id="R2-death", round=2),
                  make_moment(id="R3-death", round=3)], rounds=3)
    rep = report([hit(target=f"R{n}-death") for n in (1, 2, 3)])
    s = submit([finding(targets=["R1-death", "R2-death", "R3-death"]),
                finding(targets=["R2-death"])])
    v = check(s, mm, rep)
    assert [(x.path, x.code) for x in v] == [
        ("findings[0].targets", "too_many_targets"), ("findings[1].problem", "duplicate_problem")]
    s.findings[0].targets = ["R1-death", "R2-death"]
    s.findings[1].problem = "other"
    assert check(s, mm, rep) == []
    # 同一编号可以分别出现在一条 finding 和一条 pattern 中。
    s = parse_submission(s.model_dump() | {"patterns": [dict(problem="slow_reaction",
        targets=["R1-death", "R2-death"], analysis="共同原因值得复盘。") ]})
    assert check(s, mm, rep) == []


def test_whole_match_numbers_allowed_in_all_analysis_but_other_moment_blocked():
    mm = make_mm([make_moment(), make_moment(id="R2-death", round=2,
                  engagement={"time_to_damage_s": .933})], rounds=28,
        aim_stats={"time_to_damage_ms": 469, "time_to_damage_samples": 12,
                   "crosshair_placement_deg": None, "crosshair_placement_samples": 0,
                   "counter_strafing_pct": None, "counter_strafing_shots": 0,
                   "spray_accuracy_pct": None, "spray_shots": 0,
                   "spotted_accuracy_pct": None, "spotted_shots": 0,
                   "headshot_accuracy_pct": None, "hits": 0})
    baseline = build_baseline({"time_to_damage_ms": [350, 400]}, "测试")
    frequency = [FrequencyRow(problem="slow_reaction", label="出枪慢", hits=7,
        per_10_rounds=2.5, pro_per_10_rounds=2.5 / 1.81, ratio=1.81, prominent=True,
        expected_hits=4.86, excess_hits=2.14, chance=.002)]
    numbers = whole_match_numbers(mm, baseline, frequency, (12, 16))
    assert 4.86 in numbers and 2.14 in numbers and .2 in numbers
    rep = report([hit(target="R1-death"), hit(target="R2-death"), hit("good_trade", "R1-death")])
    text = ("整场 Time to Damage 469毫秒，职业中位数375毫秒；每10回合2.5次，"
            "是职业的1.81倍，预期4.86次、多出2.14次，偶然概率0.2%，比分12:16。")
    s = submit([finding(analysis=text)],
        patterns=[dict(problem="slow_reaction", targets=["R1-death", "R2-death"], analysis=text)],
        highlights=[dict(problem="good_trade", targets=["R1-death"], analysis=text)])
    assert check_submission(s, mm, rep, "整场汇总。", load_config(), numbers) == []
    for group in (s.findings, s.patterns, s.highlights):
        group[0].analysis = "另一次交火的 TTD 是933毫秒。"
    v = check_submission(s, mm, rep, "整场汇总。", load_config(), numbers)
    assert [(x.path, x.code) for x in v] == [
        ("findings[0].analysis", "number_not_found"),
        ("highlights[0].analysis", "number_not_found")]


@pytest.mark.parametrize("metric,value,pro_values,analysis", [
    ("time_to_damage_ms", 469, [350, 400], "整场 TTD 469毫秒，职业中位数375毫秒。"),
    ("time_to_damage_ms", 656, [350, 400], "整场 TTD 656毫秒，职业中位数375毫秒。"),
    ("crosshair_placement_deg", 25.7, [30, 42.4], "整场准星移动25.7度，职业中位数36.2度。"),
], ids=["history_player_03310441", "history_player_36581033", "history_player_11881069"])
def test_first_rejection_match_statistics_regression(metric, value, pro_values, analysis):
    """旧审计没有原始提交，按三份首轮拒绝消息中的整场数字重建。"""
    stats = {"time_to_damage_ms": None, "time_to_damage_samples": 0,
             "crosshair_placement_deg": None, "crosshair_placement_samples": 0,
             "counter_strafing_pct": None, "counter_strafing_shots": 0,
             "spray_accuracy_pct": None, "spray_shots": 0,
             "spotted_accuracy_pct": None, "spotted_shots": 0,
             "headshot_accuracy_pct": None, "hits": 0}
    stats[metric] = value
    stats["time_to_damage_samples" if metric == "time_to_damage_ms" else "crosshair_placement_samples"] = 10
    mm = make_mm([make_moment(), make_moment(id="R2-death", round=2,
                  engagement={"time_to_damage_s": .933})], rounds=28, aim_stats=stats)
    baseline = build_baseline({metric: pro_values}, "测试")
    numbers = whole_match_numbers(mm, baseline, [], (12, 16))
    s = submit([finding(problem="other", analysis=analysis)])
    assert check_submission(s, mm, report([]), "另一瞬间 TTD 933 毫秒。", load_config(), numbers) == []
    s.findings[0].analysis = "另一次交火的 TTD 是933毫秒。"
    assert "number_not_found" in codes(check_submission(s, mm, report([]), "另一瞬间 TTD 933 毫秒。",
                                                        load_config(), numbers))


def test_too_many_other_across_groups():
    s = submit([finding(problem="other")] * 3,
               patterns=[dict(problem="other", targets=["R1-death", "R2-death"], analysis="需要改进。")])
    mm = make_mm([make_moment(), make_moment(id="R2-death", round=2)], rounds=2)
    v = check(s, mm, report([]))
    assert codes(v) == {"too_many_other", "duplicate_problem"}
    excess = next(x for x in v if x.code == "too_many_other")
    assert "4" in excess.message and "3" in excess.message
    assert not check(submit([finding(problem="other")]), rep=report([]))


@pytest.mark.parametrize("targets", [[], ["R1-death"], ["R1-death", "R1-death"]])
def test_pattern_needs_two_distinct_targets(targets):
    s = submit(patterns=[dict(problem="slow_reaction", targets=targets, analysis="出现同类问题。")])
    v = check(s)
    assert "pattern_too_few" in codes(v)
    assert "2" in next(x.message for x in v if x.code == "pattern_too_few")


def test_number_not_found_describes_actual_data():
    v = check(submit([finding(analysis="首次命中用了987654321毫秒。")]))
    assert codes(v) == {"number_not_found"}
    assert "987654321" in v[0].message and '"time_to_damage_s": 0.3' in v[0].message


@pytest.mark.parametrize("text,expected", [("第2回合需要复盘。", True), ("R2需要复盘。", True),
                                          ("第1回合需要复盘。", False), ("R1需要复盘。", False)])
def test_round_mismatch(text, expected):
    v = check(submit([finding(analysis=text)]))
    assert ("round_mismatch" in codes(v)) == expected
    if expected:
        assert "[1]" in next(x.message for x in v if x.code == "round_mismatch")


@pytest.mark.parametrize("text", ["R1-death需复盘。", "[[R1]]需复盘。", "[[ R1-death ]]需复盘。"])
def test_id_in_analysis(text):
    v = check(submit([finding(analysis=text)]))
    assert "id_in_text" in codes(v)
    assert "targets" in next(x.message for x in v if x.code == "id_in_text")


@pytest.mark.parametrize("text,eng", [
    ("首次命中花了790毫秒。", {"time_to_damage_s": .79}),
    ("首次命中花了0.8秒。", {"time_to_damage_s": .79}),
    ("首次命中花了0.79秒。", {"time_to_damage_s": .79}),
    ("开枪用时0.3秒。", {"time_to_damage_s": .812}),
])
def test_units_and_rounding(text, eng):
    # 300 秒 / 1000 的换算也明确允许；所有 Moment 数值叶子都参与。
    m = make_moment(engagement=eng)
    assert check(submit([finding(analysis=text)]), make_mm([m]), report([hit(evidence={"duration": 300})])) == []


def test_percentage_json_lists_and_recoil_number():
    m = make_moment(engagement={"shots": [make_shot(recoil_index=6.25)]})
    r = report([hit(evidence={"ratio": .687, "recoil_indices": "[12.5]", "samples": "[432.19, true, null]"}, thresholds={"threshold": 765.42})])
    s = submit([finding(analysis="命中率68.7%，第7.25发和第13.5发，以及432.19、765.42。")])
    assert check(s, make_mm([m]), r) == []


@pytest.mark.parametrize("text,ok", [("约879毫秒", True), ("大约721毫秒", True),
    ("接近879毫秒", True), ("879毫秒左右", True), ("约881毫秒", False), ("879毫秒", False)])
def test_approximate_tolerance(text, ok):
    m = make_moment(engagement={"time_to_damage_s": .8})
    assert ("number_not_found" not in codes(check(submit([finding(analysis=text)]), make_mm([m])))) == ok


def test_approximate_tolerance_can_be_configured():
    cfg = load_config()
    cfg["checker"]["number_relative_tolerance"] = .2
    m = make_moment(engagement={"time_to_damage_s": .8})
    assert check(submit([finding(analysis="约950毫秒")]), make_mm([m]), cfg=cfg) == []


def test_half_up_rounding():
    m = make_moment(engagement={"time_to_damage_s": .785})
    assert check(submit([finding(analysis="0.79秒")]), make_mm([m])) == []


@pytest.mark.parametrize("text,valid", [("0.12秒", True), ("0.13秒", True), ("0.11秒", False)])
def test_last_decimal_place_accepts_truncation_and_rounding(text, valid):
    m = make_moment(engagement={"time_to_damage_s": .125})
    violations = check(submit([finding(analysis=text)]), make_mm([m]))
    assert ("number_not_found" not in codes(violations)) == valid


@pytest.mark.parametrize("text,valid", [("75 分位", True), ("p90", True), ("P25", True),
                                         ("85 分位", False), ("p85", False)])
def test_quantile_labels_are_exempt_only_when_configured(text, valid):
    mm = make_mm([make_moment(engagement=None)])
    violations = check(submit([finding(analysis=f"职业参考 {text}。")]), mm)
    assert ("number_not_found" not in codes(violations)) == valid


def test_quantile_labels_follow_configuration(tmp_path):
    override = tmp_path / "quantiles.toml"
    override.write_text("[checker]\nquantile_labels = [85]\n", encoding="utf-8")
    cfg = load_config(override)
    mm = make_mm([make_moment(engagement=None)])
    assert check(submit([finding(analysis="85 分位。")]), mm=mm, cfg=cfg) == []
    assert "number_not_found" in codes(check(submit([finding(analysis="75 分位。")]), mm=mm, cfg=cfg))


def test_weapon_numbers_are_ignored():
    text = "AK-47、M4A1-S、M4A4、SSG 08、Glock-18、P250、SG 553值得复盘。"
    assert check(submit([finding(analysis=text)])) == []


@pytest.mark.parametrize("people,ok", [("这是2v2的交火。", True), ("这是1v2的交火。", False), ("2V2", True)])
def test_personnel_pair_is_checked_separately(people, ok):
    v = check(submit([finding(analysis=people)]))
    assert ("number_not_found" not in codes(v)) == ok
    if not ok:
        assert "2v2" in v[0].message


def test_personnel_pair_must_match_one_whole_target():
    mm = make_mm([make_moment(context={"alive_own": 1, "alive_enemy": 3}),
                  make_moment(id="R2-death", round=2, context={"alive_own": 2, "alive_enemy": 4})], rounds=2)
    s = submit([finding(problem="other", targets=["R1-death", "R2-death"], analysis="1v4交火。")])
    assert "number_not_found" in codes(check(s, mm, report([])))


def test_overview_uses_only_digest_numbers_and_personnel():
    assert check(submit(overview="共28回合，2v3交火。"), digest="28回合，2v3交火。") == []
    v = check(submit(overview="共29回合，3v2交火。"), digest="28回合，2v3交火。")
    assert len(v) == 2 and all(x.path == "overview" and x.code == "number_not_found" for x in v)
    # 即使 100 是 Moment.tick，总览没有 targets，也不能凭它放行。
    assert "number_not_found" in codes(check(submit(overview="100毫秒。"), digest="28回合。"))


def test_round_target_uses_missed_trades_and_supports_trade_phrase():
    row = MissedTrade(round=1, tick=500, teammate=B, killer=C, distance=432.1, killer_traded_by_other=False)
    mm = make_mm(missed_trades=[row])
    s = submit([finding(problem="missed_trade", targets=["R1"], analysis="距离432.1单位，存在漏补枪记录。")])
    assert check(s, mm, report([hit("missed_trade", "R1")])) == []


def test_match_is_only_valid_for_spray_and_uses_aggregate_evidence():
    s = submit([finding(problem="spray_control", targets=["match"], analysis="该发数段命中率20%。")])
    r = report([hit("spray_control", "match", evidence={"hit_pct": 20})])
    assert check(s, rep=r) == []


def test_match_sensitive_phrases_use_whole_match_without_allowing_individual_numbers():
    s = submit([finding(problem="spray_control", targets=["match"], analysis="扫射命中率20%。")])
    r = report([hit("spray_control", "match", evidence={"hit_pct": 20})])
    mm = make_mm([make_moment(engagement={"shots": [make_shot(recoil_index=1)]})])
    assert check(s, mm, r) == []
    s.findings[0].analysis = "扫射用时300毫秒。"
    assert "number_not_found" in codes(check(s, mm, r))
    s.findings[0].analysis = "扫射命中率20%。"
    mm.moments[0].engagement.shots = [make_shot(recoil_index=0)]
    assert "phrase_unsupported" in codes(check(s, mm, r))


PREDICATE_CASES = [
    ("not_seen", "没看到", dict(engagement={"visibility_note": "not_seen"}), dict(engagement={"visibility_note": "seen_since_previous_kill"})),
    ("blind", "吃闪", dict(death={"blinded": True}), dict(death={"blinded": False})),
    ("peek", "主动拉出", dict(engagement={"contact_type": "peek"}), dict(engagement={"contact_type": "held"})),
    ("held", "架点", dict(engagement={"contact_type": "held"}), dict(engagement={"contact_type": "both_moving"})),
    ("seen_order", "先看见", dict(engagement={"seen_first_lead_s": 0}), dict(engagement={"seen_first_lead_s": None})),
    ("shot_order", "先开枪", dict(engagement={"first_shot_lead_s": 0}), dict(engagement={"first_shot_lead_s": None})),
    ("trade", "没人补", dict(trade={"traded": False}), dict(trade=None)),
    ("stopped", "没停稳", dict(engagement={"first_shot_stopped": False}), dict(engagement={"first_shot_stopped": None, "shots": [make_shot(stopped=None)]})),
    ("spray", "扫射", dict(engagement={"shots": [make_shot(recoil_index=1)]}), dict(engagement={"shots": [make_shot(recoil_index=0)]})),
    ("weapon_not_ready", "拉栓", dict(death={"weapon_ready": False}), dict(death={"weapon_ready": True})),
]


@pytest.mark.parametrize("name,phrase,positive,negative", PREDICATE_CASES, ids=[x[0] for x in PREDICATE_CASES])
def test_each_sensitive_predicate_both_sides(name, phrase, positive, negative):
    s = submit([finding(analysis=phrase)])
    assert check(s, make_mm([make_moment(**positive)])) == []
    v = check(s, make_mm([make_moment(**negative)]))
    assert codes(v) == {"phrase_unsupported"}
    assert name in v[0].message and "R1-death" in v[0].message
    if name == "not_seen":
        assert "seen_since_previous_kill" in v[0].message


def test_sensitive_phrase_requires_only_one_supported_target():
    mm = make_mm([make_moment(), make_moment(id="R2-death", round=2, death={"blinded": True})], rounds=2)
    s = submit([finding(problem="other", targets=["R1-death", "R2-death"], analysis="其中有一次吃闪。")])
    assert check(s, mm, report([])) == []


def test_opponent_blind_and_peek_held_and_per_shot_stopped():
    blind = dict(blinded_at_event=False, remaining_at_event_s=None, max_duration_s=1,
                 thrower=None, thrower_ambiguous=True, thrower_role=None)
    m = make_moment(engagement={"opponent_blind": blind, "contact_type": "peek", "first_shot_stopped": None,
                               "shots": [make_shot(stopped=False)]})
    assert check(submit([finding(analysis="对手被闪，架点情况和急停值得复盘。")]), make_mm([m])) == []


def test_missing_engagement_does_not_crash_predicates():
    mm = make_mm([make_moment(engagement=None, death=None, trade=None)])
    s = submit([finding(problem="other", analysis="吃闪、主动拉出、架点、先看见、先开枪、补枪、急停、扫射、拉栓、没看到。")])
    v = check(s, mm, report([]))
    assert len(v) == 10 and codes(v) == {"phrase_unsupported"}


@pytest.mark.parametrize("require", ["not_a_predicate", 123])
def test_unknown_predicate_is_rejected_by_config(tmp_path, require):
    p = tmp_path / "checker.toml"
    p.write_text('[[checker.phrases]]\npattern = "测试"\nrequire = ' + json.dumps(require) + '\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="未知谓词"):
        load_config(p)


def test_config_can_add_phrase_and_reject_bad_regex(tmp_path):
    p = tmp_path / "checker.toml"
    p.write_text('[[checker.phrases]]\npattern = "直接冲出"\nrequire = "peek"\n', encoding="utf-8")
    cfg = load_config(p)
    assert "phrase_unsupported" in codes(check(submit([finding(analysis="直接冲出")]), cfg=cfg))
    p.write_text('[[checker.phrases]]\npattern = "["\nrequire = "peek"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="正则无效"):
        load_config(p)


def test_real_mirage_submissions_regression():
    data = json.loads((FIXTURES / "mirage_checker_data.json").read_text(encoding="utf-8"))
    mm = MatchMoments.model_validate(data["match_moments"])
    rep = ProblemReport.model_validate(data["problem_report"])
    valid = parse_submission(json.loads((FIXTURES / "mirage_submission_valid.json").read_text(encoding="utf-8")))
    invalid = parse_submission(json.loads((FIXTURES / "mirage_submission_invalid.json").read_text(encoding="utf-8")))
    # 007 的历史合法样例含 weapon_not_ready；008 将它降为背景且默认停用。
    legacy = check(valid, mm, rep, data["digest"])
    assert [(v.path, v.code) for v in legacy] == [
        ("findings[2].problem", "context_only"), ("findings[2].problem", "disabled_problem")]
    valid.findings = [f for f in valid.findings if f.problem != "weapon_not_ready"]
    assert check(valid, mm, rep, data["digest"]) == []
    v = check(invalid, mm, rep, data["digest"])
    assert [x.code for x in v] == ["unknown_target", "precondition_failed", "duplicate_problem",
                                 "number_not_found", "phrase_unsupported"]
    assert "28" in v[0].message
    assert "turned_into_flash" in v[1].message
    assert "650" in v[3].message and "0.812" in v[3].message
    assert "吃闪" in v[4].message and '"player_blind": false' in v[4].message
