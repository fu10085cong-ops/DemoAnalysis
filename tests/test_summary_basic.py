"""多场 L0：问题选择、代表交火、事实卡和跳转命令。"""

from cs2coach.config import load_config
from cs2coach.features.models import MissedTrade
from cs2coach.features.problems import ProblemHit
from cs2coach.summary.basic import build_summary_l0, render_summary_basic_report
from cs2coach.summary.render_report import summary_target_card
from tests.helpers.synthetic import A, B, C
from tests.test_summary_ai import sample


def test_prominent_problem_uses_diverse_representatives_and_no_placeholders():
    summary, baseline, cfg = sample()
    row = next(row for row in summary.frequency if row.problem == "slow_reaction")
    row.prominent = True
    cfg["summary"]["representatives_per_problem"] = 2
    sections = build_summary_l0(summary, cfg)
    assert [section.problem for section in sections] == ["slow_reaction"]
    assert [e.target for e in sections[0].examples] == ["M2:R1-death", "M1:R1-death"]
    report = render_summary_basic_report(summary, sections, baseline)
    assert report.startswith("# 基础复盘（多场）：alice · 3 场")
    assert "#### 〔M2:R1-death〕M2 · game2 · de_test" in report
    assert "| M2 · game2 | M2:R1-death | 100 | `demo_gototick 1` |" in report
    assert "{" not in report and "}" not in report
    assert "## 附录：每场概况" in report
    assert "## 附录：跳转命令" in report
    assert "为什么是问题" in report and "怎么改" in report and "练习" in report


def test_fallback_problem_selection_and_missing_aim_rows():
    summary, _, cfg = sample()
    summary.frequency[0].excess_hits = 2
    summary.aim_overall["time_to_damage_ms"].median = 500
    summary.aim_overall["time_to_damage_ms"].matches = 2
    sections = build_summary_l0(summary, cfg)
    assert sections and all((row.excess_hits or 0) > 0 for row in summary.frequency
                            if row.problem in {section.problem for section in sections})
    report = render_summary_basic_report(summary, sections, None)
    assert "需要更多比赛确认" in report
    assert "| Time to Damage | 500ms | — | — | 2 场 |" in report
    assert "| Crosshair Placement |" not in report
    for row in summary.frequency:
        row.excess_hits = 0
        row.prominent = False
    assert "这批比赛没有比职业频率偏多的问题。" in render_summary_basic_report(
        summary, build_summary_l0(summary, cfg), None)


def test_missed_trade_uses_record_facts_without_jump():
    summary, _, cfg = sample()
    mm, problem_report = summary.detail("M1")
    record = MissedTrade(round=2, tick=200, teammate=B, killer=C, distance=100,
                         killer_traded_by_other=False, side="T", alive_own=2,
                         alive_enemy=3, bomb_planted=False, round_time_s=43)
    mm.missed_trades.append(record)
    problem_report.hits.append(ProblemHit(problem="missed_trade", target="R2", weapon=None,
                                          evidence=record.model_dump(), thresholds={}))
    summary.frequency.insert(0, summary.frequency[0].model_copy(update={
        "problem": "missed_trade", "label": "漏补枪", "hits": 1, "excess_hits": 1,
        "prominent": True, "matches_with_hits": 1, "by_map": {"de_test": 1}}))
    sections = build_summary_l0(summary, cfg)
    assert [e.target for e in sections[0].examples] == ["M1:R2"]
    report = render_summary_basic_report(summary, sections, None)
    assert "队友" in report and "阵亡时离你 100 单位" in report
    assert "| M1 · game1 | M1:R2 |" not in report
    # 042：每场缓存里带了玩家名，多场汇总的漏补记录显示队友名
    mm.player_names = {B: "bob"}
    assert "队友 bob 阵亡时离你 100 单位" in render_summary_basic_report(summary, build_summary_l0(summary, cfg), None)
    assert "## 附录：每场概况" in report
    assert "## 附录：跳转命令" not in report
    assert "| 比赛 | 编号 | tick | 命令 |" not in report


def test_shared_card_has_stable_heading_and_body():
    summary, _, _ = sample()
    card = summary_target_card(summary, "M1:R1-death")
    assert card[0] == "#### 〔M1:R1-death〕M1 · game1 · de_test"
    assert card[1] == "" and card[-1] == ""
    assert "你在 Mid" in card[2]
