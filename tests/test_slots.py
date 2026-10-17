"""019 占位符：取值与显示、方向句、问题阈值、漏补序号、违规代码、排版兜底与 2026-10-16 真实错误回归。"""

from cs2coach.baseline import build_baseline
from cs2coach.config import load_config
from cs2coach.features.models import MissedTrade
from cs2coach.features.problems import FrequencyRow, ProblemHit, ProblemReport
from cs2coach.report.evidence import evidence_sentence
from cs2coach.tools.checker import check_submission
from cs2coach.tools.slots import SlotError, count_values, make_slot_resolver, render_slots, render_submission
from cs2coach.tools.submission import parse_submission
from tests.helpers.moments import make_mm, make_moment, make_shot
from tests.helpers.synthetic import B, C

CFG = load_config()
BASELINE = build_baseline({"time_to_damage_ms": [300, 350, 400, 450]}, "测试样本")
AIM = {"time_to_damage_ms": 484.0, "time_to_damage_samples": 31, "crosshair_placement_deg": 6.27,
       "crosshair_placement_samples": 31, "counter_strafing_pct": 88.8, "counter_strafing_shots": 170,
       "spray_accuracy_pct": 32.9, "spray_shots": 207, "spotted_accuracy_pct": 32.2, "spotted_shots": 258,
       "headshot_accuracy_pct": 17.4, "hits": 86}


def shots(*hits):
    return [make_shot(tick=90 + i, hit=h, recoil_index=float(i)) for i, h in enumerate(hits)]


def moments():
    """R5-kill2：TTD 875、3 发中 2、第 2 发首中、自己先开枪；R19-kill1：对手先开枪、第 8 发首中。"""
    m1 = make_moment(kind="kill", id="R5-kill2", round=5,
                     context={"alive_own": 1, "alive_enemy": 3, "round_time_s": 47.2, "distance": 12.4,
                              "bomb_planted": True},
                     engagement={"time_to_damage_s": 0.875, "seen_to_first_shot_s": 0.578,
                                 "first_shot_to_damage_s": 0.297, "crosshair_placement_deg": 46.35,
                                 "crosshair_initial_offset_m": 4.62,
                                 "first_shot_lead_s": 0.27, "seen_first_lead_s": -0.42,
                                 "shots": shots(False, True, True), "shots_fired": 3, "shots_hit": 2,
                                 "contact_type": "peek", "player_speed_at_contact": 209.4,
                                 "opponent_speed_at_contact": 0.0},
                     tags=["opening_kill", "shot_first"])
    m2 = make_moment(kind="kill", id="R19-kill1", round=19,
                     engagement={"first_shot_lead_s": -0.03, "seen_first_lead_s": 0.0,
                                 "shots": shots(*([False] * 7 + [True, False])), "shots_fired": 9, "shots_hit": 1},
                     tags=["saw_opponent_first", "trade_kill"])
    death = make_moment(id="R3-death", round=3, tags=["no_shots_fired", "unused_utility"],
                        death={"unused_grenades": ["flashbang"]})
    return [m1, m2, death]


def report():
    return ProblemReport(hits=[
        ProblemHit(problem="slow_reaction", target="R5-kill2", weapon="ak47",
                   evidence={"ttd_ms": 875.0}, thresholds={"ttd_ms": 531.0, "source": "pro_p75"}),
        ProblemHit(problem="poor_crosshair_placement", target="R5-kill2", weapon="fiveseven",
                   evidence={"crosshair_initial_offset_m": 4.62}, thresholds={"crosshair_initial_offset_m": 2.34}),
        ProblemHit(problem="spray_control", target="R19-kill1", weapon="ak47",
                   evidence={"hit_pct": 15.38}, thresholds={"expected_hit_pct": 39.18}),
        ProblemHit(problem="missed_trade", target="R9", weapon=None, evidence={}, thresholds={}),
    ], disabled=[], skipped={}, baseline_source="测试")


def match_moments():
    rows = [MissedTrade(round=9, tick=500 + i, teammate=B, killer=C, distance=d, killer_traded_by_other=False,
                        side="T", alive_own=own, alive_enemy=4, round_time_s=88.8 + i, bomb_planted=False)
            for i, (d, own) in enumerate(((37.3, 2), (674.0, 1)))]
    return make_mm(moments(), rounds=30, missed_trades=rows, aim_stats=AIM)


FREQ = [FrequencyRow(problem="slow_reaction", label="出枪慢", hits=12, per_10_rounds=4.0, pro_per_10_rounds=1.37,
                     ratio=2.927, prominent=True, expected_hits=4.1, excess_hits=7.9, chance=0.0012)]


def resolver(problem=None, targets=("R5-kill2", "R19-kill1", "R3-death", "R9"), overview=False):
    return make_slot_resolver(match_moments(), report(), (16, 14), FREQ, BASELINE,
                              problem=problem, targets=targets, overview=overview)


def render(text, **kw):
    out, errors = render_slots(text, resolver(**kw))
    assert errors == [], errors
    return out


def test_moment_fields_display_like_fact_cards():
    assert render("{R5-kill2.round}") == "第 5 回合"
    assert render("回合第{R5-kill2.round_time}，{R5-kill2.people}") == "回合第47 秒，1v3"
    assert render("{R5-kill2.distance}") == "12 米"
    assert render("{R5-kill2.ttd}={R5-kill2.seen_to_shot}+{R5-kill2.shot_to_hit}") == "875 毫秒=578 毫秒+297 毫秒"
    assert render("{R5-kill2.crosshair_turn}") == "46.4°"
    assert render("{R5-kill2.shots}/{R5-kill2.hits}/{R5-kill2.hit_rate}") == "3/2/66.7%"
    assert render("{R5-kill2.speed}") == "209 单位/秒"
    assert render("{R19-kill1.first_hit_shot}") == "第 8 发"
    assert render("{R5-kill2.first_hit_shot}") == "第 2 发"


def test_order_sentences_follow_sign():
    assert render("{R5-kill2.shot_order}") == "你先开枪，领先 0.27 秒"
    assert render("{R19-kill1.shot_order}") == "对手先开枪，领先 0.03 秒"
    assert render("{R5-kill2.seen_order}") == "对手先看见你，领先 0.42 秒"
    assert render("{R19-kill1.seen_order}") == "双方同时看见对方"
    assert "拉出" in render("{R5-kill2.contact}")


def test_basis_uses_the_entry_problem():
    for problem, target in (("slow_reaction", "R5-kill2"), ("poor_crosshair_placement", "R5-kill2"),
                            ("spray_control", "R19-kill1")):
        hit = next(h for h in report().hits if h.problem == problem and h.target == target)
        assert render("{" + target + ".basis}", problem=problem) == evidence_sentence(hit).removesuffix("。")
    _, errors = render_slots("{R19-kill1.basis}", resolver(problem="slow_reaction"))
    assert errors and errors[0].code == "slot_unavailable"
    for field in ("value", "threshold", "gap"):
        _, errors = render_slots("{R5-kill2." + field + "}", resolver(problem="slow_reaction"))
        assert errors and errors[0].code == "comparison_slot_removed"


def test_missed_trade_round_index():
    assert render("{R9.count}") == "2"
    assert render("{R9[1].distance}、{R9[2].distance}") == "37 单位、674 单位"
    assert render("{R9[2].people}") == "1v4"
    _, errors = render_slots("{R9.distance}", resolver())
    assert errors[0].code == "slot_unavailable" and "[1]" in errors[0].message
    _, errors = render_slots("{R9[3].distance}", resolver())
    assert errors[0].code == "slot_unavailable"


def test_match_pro_freq_count():
    assert render("{match.score}、{match.ttd}（{match.ttd_samples}）", overview=True) == "16:14、484 毫秒（31）"
    assert render("{match.spray}", overview=True) == "32.9%"
    assert render("{pro.ttd.median}，优于 {pro.ttd.better_than}", overview=True) == "375 毫秒，优于 0%"
    assert render("{freq.slow_reaction.hits}/{freq.slow_reaction.expected}/{freq.slow_reaction.chance}/"
                  "{freq.slow_reaction.ratio}", overview=True) == "12/4.1/0.1%/2.93 倍"
    counts = count_values(match_moments())
    assert counts["opening_kills"] == 1 and counts["saw_first_kills"] == 1 and counts["missed_trades"] == 2
    assert render("{count.saw_first_kills}、{count.opening_kills}、{count.trade_kills}、{count.no_shot_deaths}",
                  overview=True) == "1、1、1、1"


def _check(analysis="这次交火值得复盘。", overview="本场值得复盘。", problem="slow_reaction", targets=("R5-kill2",)):
    sub = parse_submission(dict(overview=overview, findings=[dict(problem=problem, targets=list(targets),
        severity="medium", analysis=analysis, training="每组30次、共3组。", drills=[])],
        patterns=[], highlights=[], training_plan=["每天练15分钟。"]))
    return {v.code for v in check_submission(sub, match_moments(), report(), "地图 de_test。", CFG,
                                             None, (16, 14), FREQ, BASELINE)}


def test_violation_codes():
    assert _check() == set()
    assert "raw_number" in _check("首次命中用了875毫秒。")
    assert "raw_number" in _check("当时是1v3。")
    assert not {"raw_number", "chinese_number"} & _check("AK-47、M4A1-S、Five-SeveN、SG 553、5E 的练枪图。")
    assert "chinese_number" in _check("其中两次随后阵亡。")
    assert "chinese_number" in _check("前七发都没有命中。")
    assert "chinese_number" in _check("十几次交火都这样。")
    assert not {"chinese_number"} & _check("一次在中路，另一次在B点；这几次都值得复盘。")
    assert "unknown_slot" in _check("{R5-kill2.nonsense}")
    assert "slot_target_not_cited" in _check("{R19-kill1.ttd}")
    assert "slot_unavailable" in _check("{R5-kill2.trade_delay}")
    assert "slot_target_not_cited" in _check(overview="本场 {R5-kill2.ttd}。")
    assert _check(overview="本场 {match.score}，{count.kills} 次击杀。") == set()


def test_training_text_keeps_plan_numbers():
    assert _check() == set()  # training="每组30次、共3组。"、training_plan="每天练15分钟。"


def test_render_submission_never_prints_braces():
    sub = parse_submission(dict(overview="本场 {match.score}。", findings=[dict(problem="slow_reaction",
        targets=["R5-kill2"], severity="medium", analysis="用了 {R5-kill2.ttd}，另有 {R5-kill2.nonsense}。",
        training="练。", drills=[])], patterns=[], highlights=[], training_plan=["练。"]))
    result = render_submission(sub, lambda problem, targets, overview=False:
                               resolver(problem, targets, overview))
    assert result["overview"] == "本场 16:14。"
    assert result["findings"][0]["analysis"] == "用了 875 毫秒，另有 （数据缺失）。"
    assert result["slot_errors"][0]["code"] == "unknown_slot"
    assert "{" not in result["overview"] and "{" not in result["findings"][0]["analysis"]


def test_regression_2026_10_16_sentences_render_true_values():
    """当日报告中被标出的错误句，改用占位符后由程序给出正确数值。"""
    # 本地 M7："补枪命中率 100%"（实际 3 发中 2）→ hit_rate
    assert render("{R5-kill2.hit_rate}") == "66.7%"
    # 本地 M7："7 次先发现对手的击杀"（7 是首杀）→ 两个计数分开
    extra = make_moment(kind="kill", id="R7-kill1", round=7, tags=["opening_kill"])
    counts = count_values(make_mm([*moments(), extra], rounds=30))
    assert (counts["opening_kills"], counts["saw_first_kills"]) == (2, 1)  # 两个计数口径不同，不会混用
    # 本地 M7：准星问题的比较由程序依据句按命中武器拼好
    assert "2.3 米" in render("{R5-kill2.basis}", problem="poor_crosshair_placement")
    # flash 玩家02："其中两次随后阵亡"→ 只能写计数占位符或不写数量
    assert "chinese_number" in _check("其中两次随后阵亡。")
    # flash 汇总："整段九发只中两发"（9 发其实是后段）→ shots/hits 取整段
    assert render("整段 {R5-kill2.shots}，命中 {R5-kill2.hits}") == "整段 3，命中 2"
    # 本地 M7（旧报告正确且有用的句子）："第八发才命中" → first_hit_shot
    assert render("直到{R19-kill1.first_hit_shot}才命中") == "直到第 8 发才命中"
    # 本地 M7："3 次漏补"（实际 1 次）→ count.missed_trades
    assert render("{count.missed_trades}", overview=True) == "2"  # 本测试数据中为 2 条


def test_slot_error_type_is_public():
    assert SlotError("unknown_slot", "x", "y").code == "unknown_slot"


def test_duplicate_units_and_ordinal_are_removed():
    # 本地千问实测（2026-10-16）：在带单位的占位符后面又写单位
    assert render("用了{R5-kill2.ttd}ms") == "用了875 毫秒"
    assert render("用了{R5-kill2.ttd} 毫秒，距离{R5-kill2.distance}米") == "用了875 毫秒，距离12 米"
    assert render("转了{R5-kill2.crosshair_turn}°，速度{R5-kill2.speed}u/s") == "转了46.4°，速度209 单位/秒"
    assert render("第{R5-kill2.round}回合") == "第 5 回合"
    assert render("直到第{R19-kill1.first_hit_shot}才命中") == "直到第 8 发才命中"
    assert render("{freq.slow_reaction.chance}%", overview=True) == "0.1%"
    assert render("{R5-kill2.distance} meters") == "12 米 meters"  # 后面是英文单词时不当作单位
    assert render("共{R5-kill2.seen_to_shot}ms", problem="slow_reaction") == "共578 毫秒"
    # 评测实测（2026-10-16）："你你先看见对手"、"从第 第 5 发"、"多 7.90291 次"
    assert render("你{R5-kill2.shot_order}") == "你先开枪，领先 0.27 秒"
    assert render("而对手{R19-kill1.shot_order}") == "而对手先开枪，领先 0.03 秒"
    assert render("从第 {R19-kill1.first_hit_shot}") == "从第 8 发"
    assert render("多 {freq.slow_reaction.excess} 次", overview=True) == "多 7.9 次"


def test_plain_number_followed_by_unit_is_a_meaning_error():
    _, errors = render_slots("占交火的{freq.slow_reaction.excess}%", resolver(overview=True))
    assert errors and errors[0].code == "slot_unit_mismatch" and "%" in errors[0].message
    _, errors = render_slots("{count.kills} 次击杀", resolver(overview=True))
    assert errors == []


def test_raw_number_message_suggests_matching_slots():
    sub = parse_submission(dict(overview="TTD 484 毫秒，比分 16:14。", findings=[dict(problem="slow_reaction",
        targets=["R5-kill2"], severity="medium", analysis="首次命中用了875毫秒。", training="练。", drills=[])],
        patterns=[], highlights=[], training_plan=["练。"]))
    v = {x.path: x for x in check_submission(sub, match_moments(), report(), "地图 de_test。", CFG,
                                              None, (16, 14), FREQ, BASELINE)}
    assert "484 → {match.ttd}" in v["overview"].message and "{match.score}" in v["overview"].message
    assert "875 → {R5-kill2.ttd}" in v["findings[0].analysis"].message
    assert v["overview"].code == "raw_number"  # 同一段只报一条


def test_training_text_may_use_slots():
    assert _check() == set()
    sub = parse_submission(dict(overview="本场值得复盘。", findings=[dict(problem="slow_reaction",
        targets=["R5-kill2"], severity="medium", analysis="这次交火值得复盘。",
        training="目标是把 {R5-kill2.ttd} 压到 500 毫秒以内。", drills=[])],
        patterns=[], highlights=[], training_plan=["把整场 {match.ttd} 压到 450 毫秒。", "用 {R5-kill2.ttd} 对比。"]))
    v = check_submission(sub, match_moments(), report(), "地图 de_test。", CFG, None, (16, 14), FREQ, BASELINE)
    assert v == []  # 训练计划不针对具体交火，可以引用本场任意瞬间
    result = render_submission(sub, lambda problem, targets, overview=False: resolver(problem, targets, overview))
    assert result["findings"][0]["training"] == "目标是把 875 毫秒 压到 500 毫秒以内。"
    assert result["training_plan"] == ["把整场 484 毫秒 压到 450 毫秒。", "用 875 毫秒 对比。"]


def test_quantile_names_people_and_ambiguous_suggestions():
    # 本地千问实测：“75分位”“残局1v3”被当成数字；小整数推荐了无关问题的频率
    assert _check("{R5-kill2.basis}，职业同武器 75 分位与 p90 是不同基准。") == set()
    sub = parse_submission(dict(overview="本场值得复盘。", findings=[dict(problem="slow_reaction",
        targets=["R5-kill2"], severity="medium", analysis="残局1v3，开了3枪。", training="练。", drills=[])],
        patterns=[], highlights=[], training_plan=["练。"]))
    v = check_submission(sub, match_moments(), report(), "地图 de_test。", CFG, None, (16, 14), FREQ, BASELINE)
    message = next(x.message for x in v if x.code == "raw_number")
    assert "1v3 → {R5-kill2.people}" in message
    assert "3 → {R5-kill2.shots}" in message  # 本条瞬间的字段优先于整场计数和频率


def test_s03_crosshair_slots_and_metric_wording():
    assert render("{R5-kill2.crosshair_offset}") == "4.6 米"
    _, errors = render_slots("{R5-kill2.crosshair}", resolver())
    assert errors[0].code == "unknown_slot" and "crosshair_turn" in errors[0].message
    assert _check("刚看见对手时准星离头部{R5-kill2.crosshair_turn}。", problem="poor_crosshair_placement") >= {"metric_mismatch"}
    assert "metric_mismatch" not in _check("准星转过{R5-kill2.crosshair_turn}。", problem="poor_crosshair_placement")
    assert "metric_mismatch" in _check("准星转过{R5-kill2.crosshair_offset}。", problem="poor_crosshair_placement")
    assert "metric_mismatch" not in _check("刚看见对手时准星离头部{R5-kill2.crosshair_offset}。", problem="poor_crosshair_placement")
    assert "unknown_slot" in _check("刚看见对手时准星离他很远，准星已经转过{R5-kill2.crosshair}。",
                                    problem="poor_crosshair_placement")


def test_s03_overview_and_training_targets():
    def check(overview="本场值得复盘。", training="每组 20 次，共 3 组。", plan="每周练 3 次。"):
        sub = parse_submission(dict(overview=overview, findings=[dict(problem="poor_crosshair_placement",
            targets=["R5-kill2"], severity="low", analysis="准星值得调整。", training=training, drills=[])],
            patterns=[], highlights=[], training_plan=[plan]))
        return {v.code for v in check_submission(sub, match_moments(), report(), "地图 de_test。", CFG,
                                                  None, (16, 14), FREQ, BASELINE)}
    assert "overview_contradiction" in check(overview="本场准星不差。")
    assert "overview_contradiction" in check(overview="本场 Crosshair Placement 为{match.crosshair}。")
    assert "overview_contradiction" not in check()
    assert "training_target_number" not in check()
    for target in ("命中率达到 90%", "10 发至少中 8 发", "首枪命中率低于九成", "前 10 发全部落在靶心区域"):
        assert "training_target_number" in check(training=target)
        assert "training_target_number" in check(plan=target)
    assert "slot_unavailable" in check(training="{R5-kill2.basis}")
    assert "slot_target_not_cited" in check(overview="{R5-kill2.basis}")


def test_s03_baseline_comparison_sentence_is_rejected_and_overview_without_hit_is_allowed():
    assert "comparison_slot_removed" in _check(
        "从看见到第一次命中一共用了{R5-kill2.value}，职业同武器 75 分位是{R5-kill2.threshold}。")
    sub = parse_submission(dict(overview="本场准星不差。", findings=[dict(problem="slow_reaction",
        targets=["R5-kill2"], severity="low", analysis="出枪值得复盘。", training="练。", drills=[])],
        patterns=[], highlights=[], training_plan=["练。 "]))
    no_crosshair = ProblemReport(hits=[h for h in report().hits if h.problem != "poor_crosshair_placement"],
                                 disabled=[], skipped={}, baseline_source="测试")
    codes = {v.code for v in check_submission(sub, match_moments(), no_crosshair, "地图 de_test。", CFG,
                                               None, (16, 14), FREQ, BASELINE)}
    assert "overview_contradiction" not in codes


def test_unit_value_used_as_count_is_flagged():
    # 本地千问实测："{pro.headshot.p25}次死亡携带未用道具" → "17%次"
    _, errors = render_slots("{match.spray}次死亡", resolver(overview=True))
    assert errors and errors[0].code == "slot_unit_mismatch" and "不是次数" in errors[0].message
    _, errors = render_slots("{match.ttd}毫秒次", resolver(overview=True))  # 先去掉重复单位再判断
    assert errors and errors[0].code == "slot_unit_mismatch"
    assert render_slots("{count.unused_utility_deaths}次死亡", resolver(overview=True))[1] == []
