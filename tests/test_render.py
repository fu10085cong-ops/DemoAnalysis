"""结构化报告的程序事实、检测依据、跳转、审计输出和提示词约定。"""

import json

from cs2coach.ai.diagnose import Diagnosis, DroppedItem, diagnose
from cs2coach.ai.prompts import SYSTEM_PROMPT
from cs2coach.baseline import build_baseline
from cs2coach.config import load_config
from cs2coach.features.models import MissedTrade
from cs2coach.features.problems import ProblemHit, ProblemReport, detect_problems, problem_frequency
from cs2coach.report.render import render_diagnosis
from cs2coach.tools.checker import Violation
from cs2coach.tools.digest import build_digest
from cs2coach.tools.submission import parse_submission
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import A, B, C
from tests.test_ai import claude, payload, submit_response
from tests.test_moments import find_moments, scenario


def sample():
    mm = make_mm([make_moment(), make_moment(kind="kill", weapon="m4a1_silencer", id="R2-kill1", round=2,
        tags=["trade_kill"], engagement={"time_to_damage_s": .812}, jump_command="demo_gototick 222")], rounds=2,
        missed_trades=[MissedTrade(round=1, tick=90, teammate=B, killer=C, distance=200, killer_traded_by_other=False)])
    baseline = {"source": "手工样本", "weapons": {
        "ak47": {"time_to_damage_ms": {"p75": 250}},
        "m4a1_silencer": {"time_to_damage_ms": {"p75": 484}}},
        "problem_rates": {"slow_reaction": {"hits": 5, "rounds": 100}}}
    cfg = load_config()
    cfg["problems"]["frequency_min_excess"] = 1
    problems = detect_problems(mm, baseline, cfg)
    frequency = problem_frequency(problems, mm, baseline, cfg)
    args = payload(findings=[
        dict(problem="slow_reaction", targets=["R2-kill1"], severity="medium", analysis="接敌准备需要改进。", training="每天练100次。"),
        dict(problem="other", targets=["R1-death"], severity="low", analysis="其他处理值得复盘。", training="每天练30次。"),
        dict(problem="missed_trade", targets=["R1"], severity="low", analysis="队友阵亡后的处理值得复盘。", training="练习双人配合。")],
        patterns=[dict(problem="slow_reaction", targets=["R1-death", "R2-kill1"], analysis="同类问题反复出现。")],
        highlights=[dict(problem="good_trade", targets=["R2-kill1"], analysis="这次配合值得保留。")])
    d = Diagnosis(submission=parse_submission(args), problems=problems, frequency=frequency,
                  digest="", provider="fake", model="fake-model", score=(16, 12))
    return d, mm, {A: "alice", B: "bob", C: "carol"}


def test_render_has_all_sections_program_facts_evidence_and_unique_jumps():
    d, mm, names = sample()
    text = render_diagnosis(d, mm, names)
    assert text.startswith("# 复盘报告：alice · de_test")
    assert "比分 16:12" in text and "fake / fake-model" in text
    for section in ("总览", "突出问题", "关键问题", "反复出现的问题", "亮点", "训练计划", "跳转命令"):
        assert f"## {section}" in text
    assert "### 出枪慢 · 中" in text and "AI 判断，无程序依据" in text
    assert "| 问题 | 本场次数 | 按职业频率预期 | 多出的次数 | 偶然概率 | 倍数 |" in text
    assert "| **出枪慢** |" in text
    assert "你在 Mid" in text and "812 毫秒" in text and "高于职业同武器 75 分位 484 毫秒" in text
    assert "队友 bob 阵亡时离你 200 单位" in text
    assert "涉及：〔R1-death〕、〔R2-kill1〕" in text
    assert "程序检测依据" in text and "**分析**" in text and "**怎么练**" in text
    assert text.count("`demo_gototick 222`") == 1 and text.count("`demo_gototick 1`") == 1
    assert "职业样本来源：手工样本" in text
    assert "**突出问题**" not in text  # 报告用表格承载，不复制 AI 摘要的标记


def test_digest_contains_prominence_and_human_evidence_before_moments():
    d, mm, _ = sample()
    text = build_digest(scenario(), mm, None, d.problems, d.frequency)
    assert "**出枪慢**" in text and "R2-kill1：TTD 812 毫秒" in text
    assert "偶然概率低于 5%" in text and "至少多 2 次" in text
    assert "多出次数大于 0 但不突出的情况需要更多比赛确认" in text
    assert "标为“不高于职业水平”的项目不是问题，不要作为问题提出" in text
    assert "标为“不高于职业水平”的项目不是问题，不要作为问题提出" in SYSTEM_PROMPT
    assert "75 分位 484 毫秒" in text
    assert text.index("## 问题频率") < text.index("## 检测到的问题") < text.index("## 决定性瞬间")
    assert "只能在打中过对手的交火中判断" in text
    assert "击杀和死亡的比例不说明问题" in text
    assert "看见对手到第一次命中" in text and "不按击杀和死亡分开" in text


def test_single_digest_shows_missed_trade_context():
    match = scenario()
    mm = find_moments(match, A, load_config())
    text = build_digest(match, mm, None)
    assert "下包前，队友阵亡后 1v1" in text


def test_digest_pro_training_notice_only_for_pro_players():
    d, mm, _ = sample()
    cfg = load_config()
    baseline = {"players": [{"steamid": A, "name": "alice"}]}
    pro = build_digest(scenario(), mm, baseline, cfg=cfg)
    assert pro.startswith("目标玩家是职业选手，本人就在职业样本中。")
    ordinary = build_digest(scenario(), mm, {}, cfg=cfg)
    assert "训练建议面向职业水平" not in ordinary
    cfg["training"]["extra_pro_steamids"] = [A]
    extra = build_digest(scenario(), mm, {}, cfg=cfg)
    assert extra.startswith("目标玩家是职业选手，本人不在职业样本中。")
    assert "只有摘要明确标明目标玩家是职业选手时" in SYSTEM_PROMPT
    assert "否则一律按上述普通用户训练标准" in SYSTEM_PROMPT


def test_match_target_fact_uses_aggregate_numbers():
    d, mm, names = sample()
    args = payload(findings=[dict(problem="spray_control", targets=["match"], severity="low", analysis="命中率值得复盘。", training="练习控制连射。")])
    d.submission = parse_submission(args)
    d.problems = ProblemReport(hits=[ProblemHit(problem="spray_control", target="match", weapon="ak47",
        evidence={"from": 3, "to": 5, "shots": 30, "hit_pct": 20},
        thresholds={"expected_hit_pct": 65, "margin_pct": 15})], disabled=[], skipped={}, baseline_source="测试")
    text = render_diagnosis(d, mm, names)
    assert "AK-47 的第 4–6 发共 30 发，命中率 20%" in text
    assert "整场该发数段 30 发" in text and "职业期望 65% 减去 15 个百分点" in text
    assert "本报告未引用可跳转的瞬间" in text


def test_deleted_count_excludes_replaced_overview_and_does_not_repeat_removed_content():
    d, mm, names = sample()
    violation = Violation(path="findings[9].analysis", code="number_not_found", message="错误数字")
    d.dropped = [DroppedItem(path="findings[9]", content={"analysis": "被删除的错误内容"}, violations=[violation]),
                 DroppedItem(path="overview", content="旧总览", violations=[violation], action="replaced")]
    text = render_diagnosis(d, mm, names)
    assert "有 1 条结论未通过数据检查，已删除" in text
    assert "被删除的错误内容" not in text and "旧总览" not in text


def test_cli_writes_report_and_submission_audit_with_fake_diagnosis(tmp_path, monkeypatch):
    from cs2coach.cli import _diagnose

    cfg = load_config()
    match = scenario()
    mm = find_moments(match, A, cfg)
    d = diagnose(match, mm, cfg, build_baseline({}, "测试"), claude([submit_response()])[0])
    monkeypatch.setattr("cs2coach.ai.diagnose.diagnose", lambda *a, **kw: d)
    # 直接测输出路径，凭据加载替换为空操作，不读取真实 .env，也不运行 --ai。
    monkeypatch.setattr("cs2coach.env.load_env_file", lambda *a: [])
    out = tmp_path / "report.md"
    assert _diagnose(match, mm, cfg, out) == 0
    assert out.read_text(encoding="utf-8") == d.report
    audit = json.loads((tmp_path / "submission.json").read_text(encoding="utf-8"))
    assert audit == {"submission": d.submission.model_dump(), "violations_history": [[]], "dropped": [],
                     "raw_calls": d.raw_calls}


def test_prompts_keep_metric_definitions_and_require_structured_workflow():
    for term in ("Time to Damage", "不是反应时间", "Crosshair Placement", "34%", "AWP", "SSG 08", "1.45", "1.25",
                 "单位", "opponent_time_to_damage_s", "0.25", "120", "submit_report", "patterns", "示例，不是本场数据"):
        # “距离...米”就是距离单位说明，提示词中不必额外重复“单位”二字。
        if term == "单位":
            assert "距离 N 米" in SYSTEM_PROMPT
        else:
            assert term in SYSTEM_PROMPT
    assert "[[" not in SYSTEM_PROMPT and "报告结构（Markdown）" not in SYSTEM_PROMPT
    assert "finding 只讲 1–2 次代表性交火" in SYSTEM_PROMPT
    assert "不要根据击杀和死亡的分布推断心理状态或压力" in SYSTEM_PROMPT
