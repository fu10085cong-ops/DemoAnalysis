"""局面句与局势说法：用 2026-10-16 flash 评测中的真实错句做回归。"""

from cs2coach.config import load_config
from cs2coach.features.models import MissedTrade
from cs2coach.features.problems import ProblemHit, ProblemReport
from cs2coach.tools.checker import check_submission
from cs2coach.tools.slots import make_slot_resolver, render_slots, situation_text
from cs2coach.tools.submission import parse_submission
from tests.helpers.moments import make_mm, make_moment, make_shot
from tests.helpers.synthetic import B, C

CFG = load_config()


def test_situation_sentence():
    assert situation_text("CT", 1, 3, True, 47.2) == "CT 方，1v3 残局，对方已下包、回防"
    assert situation_text("T", 3, 1, True, 70) == "T 方，3v1，人数占优，已下包、守包"
    assert situation_text("T", 4, 5, False, 12) == "T 方，4v5，人数劣势，未下包"
    assert situation_text("CT", 5, 5, None, None) == "CT 方，5v5"
    assert situation_text(None, 5, 5, False, 3) is None


def moments():
    """卢文笙实测：R15 先开枪 0/13；R11 同一 tick 开枪、11 发中 3；R9 闪光助攻 5v5。汇总实测：M10 R2 CT 下包后 1v3。"""
    r15 = make_moment(id="R15-death", round=15, context={"side": "CT", "alive_own": 5, "alive_enemy": 4},
                      engagement={"first_shot_lead_s": 0.73, "shots_fired": 13, "shots_hit": 0,
                                  "shots": [make_shot(hit=False)]})
    r11 = make_moment(id="R11-death", round=11, context={"side": "T", "alive_own": 5, "alive_enemy": 3},
                      engagement={"first_shot_lead_s": 0.0, "shots_fired": 11, "shots_hit": 3,
                                  "shots": [make_shot(hit=True)]})
    assist = make_moment(kind="assist", id="R9-assist1", round=9, context={"alive_own": 5, "alive_enemy": 5})
    retake = make_moment(kind="kill", id="R2-kill1", round=2,
                         context={"side": "CT", "alive_own": 1, "alive_enemy": 3, "bomb_planted": True})
    hold = make_moment(kind="kill", id="R8-kill2", round=8,
                       context={"side": "T", "alive_own": 3, "alive_enemy": 1, "bomb_planted": True})
    row = MissedTrade(round=9, tick=900, teammate=B, killer=C, distance=37.0, killer_traded_by_other=False,
                      side="T", alive_own=2, alive_enemy=4, round_time_s=88.8, bomb_planted=False)
    return make_mm([r15, r11, assist, retake, hold], rounds=15, missed_trades=[row])


def report():
    hits = [ProblemHit(problem="spray_control", target=t, weapon="ak47", evidence={}, thresholds={})
            for t in ("R15-death", "R11-death")]
    hits += [ProblemHit(problem="flash_assist", target="R9-assist1", weapon=None, evidence={}, thresholds={}),
             ProblemHit(problem="good_trade", target="R2-kill1", weapon=None, evidence={}, thresholds={}),
             ProblemHit(problem="won_duel_seen_first", target="R8-kill2", weapon=None, evidence={}, thresholds={}),
             ProblemHit(problem="missed_trade", target="R9", weapon=None, evidence={}, thresholds={})]
    return ProblemReport(hits=hits, disabled=[], skipped={}, baseline_source="测试")


def codes(group, problem, targets, analysis):
    entry = dict(problem=problem, targets=targets, analysis=analysis)
    findings = [dict(problem="spray_control", targets=["R15-death"], severity="low", analysis="这次值得复盘。",
                     training="练。", drills=[])]
    if group == "findings":
        findings = [entry | dict(severity="low", training="练。", drills=[])]
    sub = parse_submission(dict(overview="本场值得复盘。", findings=findings, patterns=[],
                                highlights=[entry] if group == "highlights" else [], training_plan=["练。"]))
    return [v for v in check_submission(sub, moments(), report(), "地图 de_test。", CFG, None, (1, 0), [], None)
            if v.code == "phrase_unsupported"]


def test_all_claims_are_checked_per_engagement():
    # 卢文笙实测原句：R11 是同时开枪且打中 3 发
    bad = codes("findings", "spray_control", ["R11-death", "R15-death"], "你都先开枪却始终拿不到伤害。")
    assert {v.message.split("满足 ")[1].split("，")[0] for v in bad} == {"shot_first", "no_hits"}
    assert all("R11-death" in v.message for v in bad)
    assert codes("findings", "spray_control", ["R15-death"], "你都先开枪却始终拿不到伤害。") == []


def test_man_advantage_claims():
    # 卢文笙实测原句：闪光助攻时是 5v5
    assert codes("highlights", "flash_assist", ["R9-assist1"], "帮助队伍在人数劣势下换掉对手。")
    assert codes("highlights", "flash_assist", ["R9-assist1"], "帮助队伍换掉对手。") == []
    assert codes("highlights", "good_trade", ["R2-kill1"], "在人数劣势时完成补枪。") == []  # 1v3
    assert codes("findings", "missed_trade", ["R9"], "队友阵亡时人数劣势。") == []  # 漏补记录 2v4 也算


def test_hold_bomb_only_for_t_after_plant():
    # 汇总实测原句：CT 下包后 1v3 被写成"守包残局"
    assert codes("highlights", "good_trade", ["R2-kill1"], "一次守包残局里队友刚被击杀你就补上。")
    assert codes("highlights", "won_duel_seen_first", ["R8-kill2"], "守包时完成击杀。") == []


def test_situation_slot_renders_for_moments_and_records():
    resolve = make_slot_resolver(moments(), report(), (1, 0), [], None, targets=("R2-kill1", "R9"))
    out, errors = render_slots("{R2-kill1.situation}；{R9.situation}", resolve)
    assert errors == []
    assert out == "CT 方，1v3 残局，对方已下包、回防；T 方，2v4，人数劣势，未下包"


def test_range_words_and_segment_vs_threshold():
    # 评测实测：28 米被说成"短距离"；"看见到开枪 406 毫秒，高于职业同武器 75 分位 398 毫秒"
    mm = make_mm([make_moment(id="R5-death", round=5, context={"distance": 28.3},
                              engagement={"seen_to_first_shot_s": 0.406})], rounds=5)
    rep = ProblemReport(hits=[ProblemHit(problem="slow_reaction", target="R5-death", weapon="ak47",
                                         evidence={"ttd_ms": 891.0}, thresholds={"ttd_ms": 516.0})],
                        disabled=[], skipped={}, baseline_source="测试")
    def found(analysis):
        sub = parse_submission(dict(overview="本场值得复盘。", findings=[dict(problem="slow_reaction",
            targets=["R5-death"], severity="low", analysis=analysis, training="练。", drills=[])],
            patterns=[], highlights=[], training_plan=["练。"]))
        return {v.code for v in check_submission(sub, mm, rep, "地图 de_test。", CFG, None, (1, 0), [], None)}
    assert "phrase_unsupported" in found("一次短距离交火中出枪偏慢。")
    assert found("一次远距离交火中出枪偏慢。") == set()
    # 评测实测的误伤：建议里的“拉近距离”不是在描述这次交火
    assert found("更好的处理是靠队友配合拉近距离，近距离时再打。") == set()
    assert "slot_misuse" in found("其中看见到开枪就占{R5-death.seen_to_shot}，高于职业同武器 75 分位{R5-death.threshold}。")
    assert found("首次命中用了{R5-death.value}，高于职业同武器 75 分位{R5-death.threshold}，"
                 "其中看见到开枪占{R5-death.seen_to_shot}。") == set()
