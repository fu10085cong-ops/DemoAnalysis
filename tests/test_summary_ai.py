"""多场 AI 摘要、跨场检查、共用诊断循环与排版；不访问真实模型。"""

import json
import hashlib

import pytest

from cs2coach.ai.providers import ToolCall, Turn, Usage
from cs2coach.config import load_config
from cs2coach.features.models import MissedTrade
from cs2coach.features.problems import ProblemHit, ProblemReport
from cs2coach.report.evidence import evidence_sentence
from cs2coach.summary.aggregate import aggregate_matches
from cs2coach.summary.diagnose import diagnose_summary
from cs2coach.summary.diagnose import SummaryDiagnosis
from cs2coach.summary.render_report import render_summary_diagnosis
from cs2coach.summary.digest import build_summary_digest, representative_hits, summary_numbers
from cs2coach.summary.discover import DemoRef
from cs2coach.summary.queries import SUMMARY_TOOLS, run_summary_tool
from cs2coach.summary.render import render_summary_problems
from cs2coach.tools.checker import check_summary_submission
from cs2coach.tools.slots import make_slot_resolver, render_slots
from cs2coach.tools.queries import ToolError
from cs2coach.tools.submission import parse_submission, submit_tool
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import A


def sample():
    cfg = load_config()
    baseline = {"source": "职业测试样本", "players": [{"steamid": A, "name": "alice"}],
                "problem_rates": {"slow_reaction": {"hits": 10, "rounds": 100}}}
    games = []
    for i, ttd in enumerate((937, 1234, 450), 1):
        moment = make_moment(engagement={"time_to_damage_s": ttd / 1000})
        mm = make_mm([moment], rounds=10, map_name="de_test", player_name="alice")
        problem = "dry_peek_with_utility" if i == 3 else "slow_reaction"
        hit = ProblemHit(problem=problem, target=moment.id, weapon="ak47",
            evidence={"ttd_ms": ttd}, thresholds={"ttd_ms": 500})
        report = ProblemReport(hits=[hit], disabled=[], skipped={}, baseline_source="职业测试样本")
        ref = DemoRef(path=f"game{i}.dem", member=None, match_id=f"game{i}",
                      map_name="de_test", player_name="alice")
        games.append((ref, mm, report, (6, 4)))
    return aggregate_matches(A, games, baseline, cfg), baseline, cfg


def test_summary_ai_fact_card_snapshot():
    """抽取共用事实卡前的完整 AI 报告散列，防止排版变动。"""
    summary, baseline, cfg = sample()
    d = SummaryDiagnosis(submission=parse_submission(submission()), digest="",
                         provider="fake", model="test")
    report = render_summary_diagnosis(d, summary, baseline, cfg)
    assert hashlib.sha256(report.encode("utf-8")).hexdigest() == (
        "69eb17f7a6f7e8bd0d0b5ba85a0bf5040c8bbed03890c99f068975325543c3bc")


def submission(target="M1:R1-death", analysis="这次交火的准备值得复盘。", overview="这些比赛合计值得复盘。"):
    return dict(overview=overview, findings=[dict(problem="slow_reaction", targets=[target],
        severity="medium", analysis=analysis, training="每周练习三次接敌准备。")],
        patterns=[], highlights=[], training_plan=["每周复盘两次首枪准备。"])


def test_summary_basis_uses_cross_match_target_and_rejects_old_comparison_slots():
    summary, baseline, cfg = sample()
    resolve = make_slot_resolver(summary=summary, baseline=baseline, problem="slow_reaction",
                                 targets=("M1:R1-death",))
    shown, errors = render_slots("{M1:R1-death.basis}", resolve)
    assert errors == []
    assert shown == evidence_sentence(summary.detail("M1")[1].hits[0]).removesuffix("。")
    sub = parse_submission(submission(analysis="{M1:R1-death.value} 高于 {M1:R1-death.threshold}。"))
    codes = {v.code for v in check_summary_submission(sub, summary, "地图 de_test。", cfg, [], baseline)}
    assert "comparison_slot_removed" in codes


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, payloads):
        self.turns = [Turn(stop="tool_use", tool_calls=[ToolCall(id=f"t{i}", name="submit_report",
                      args=payload, raw_args=json.dumps(payload, ensure_ascii=False))],
                      usage=Usage(input_tokens=10, output_tokens=20))
                      for i, payload in enumerate(payloads)]
        self.results = []

    def start(self, system, user_texts, tools):
        self.system, self.user_texts, self.tools = system, user_texts, tools

    def send(self):
        return self.turns.pop(0)

    def add_tool_results(self, results):
        self.results.extend(results)

    def add_user(self, text):
        pass


def test_digest_sections_representatives_and_pro_notice():
    summary, baseline, cfg = sample()
    digest = build_summary_digest(summary, baseline, cfg)
    for section in ("## 多场比赛", "## 每场概况", "## 整场枪法", "## 问题频率", "## 检测到的问题"):
        assert section in digest
    assert digest.startswith("目标玩家是职业选手，本人就在职业样本中。")
    assert "8 场中 7 场高于职业" in digest
    refs = [ref for ref, _ in representative_hits(summary, "slow_reaction", 2)]
    assert refs == ["M2:R1-death", "M1:R1-death"]
    assert "M2:R1-death：TTD 1234" in digest
    assert "M1:R1-death：TTD 937" in digest
    assert "本人就在职业样本中" not in build_summary_digest(summary, {}, cfg)


def test_summary_system_prompt_is_fixed_between_batches():
    summary, baseline, cfg = sample()
    other, _, _ = sample()
    other.names = ["另一位玩家"]
    for match in other.matches:
        match.map_name = "de_other"
    first = FakeProvider([submission()])
    second = FakeProvider([submission()])
    diagnose_summary(summary, cfg, baseline, first)
    diagnose_summary(other, cfg, baseline, second)
    assert first.system == second.system
    assert "## 占位符" in first.system and "## 训练练习图白名单" in first.system
    assert "## 训练练习图白名单" not in first.user_texts[0]


def test_representatives_sort_within_match_by_deviation():
    summary, _, _ = sample()
    mm, report = summary.detail("M1")
    mm.moments.append(make_moment(id="R2-death", round=2, tick=200))
    report.hits.append(ProblemHit(problem="slow_reaction", target="R2-death", weapon="ak47",
        evidence={"ttd_ms": 1200}, thresholds={"ttd_ms": 500}))
    refs = [ref for ref, _ in representative_hits(summary, "slow_reaction", 3)]
    assert refs == ["M2:R1-death", "M1:R2-death", "M1:R1-death"]


def test_missed_trade_representatives_prefer_untraded_then_nearby_and_diverse():
    summary, _, _ = sample()
    for number, round_no, traded, distance in (
        ("M1", 2, True, 20), ("M1", 3, False, 100), ("M2", 2, False, 200)):
        mm, report = summary.detail(number)
        record = MissedTrade(round=round_no, tick=round_no * 100,
            teammate=A + 1, killer=A + 2, distance=distance,
            killer_traded_by_other=traded, side="T", alive_own=2,
            alive_enemy=3, round_time_s=43, bomb_planted=False)
        mm.missed_trades.append(record)
        report.hits.append(ProblemHit(problem="missed_trade", target=f"R{round_no}", weapon=None,
            evidence=record.model_dump(), thresholds={}))
    refs = [ref for ref, _ in representative_hits(summary, "missed_trade", 3)]
    assert refs == ["M1:R3", "M2:R2", "M1:R2"]
    digest = build_summary_digest(summary, None, load_config())
    assert "M1:R3：漏补枪记录：第 43 秒，T 方，下包前，队友阵亡后 2v3。" in digest
    hits = json.loads(run_summary_tool("list_problem_hits", {"problem": "missed_trade"}, summary))
    assert "下包前，队友阵亡后 2v3" in hits[0]["evidence"]


def test_primary_name_and_summary_totals_in_digest_and_checker():
    summary, baseline, cfg = sample()
    summary.detail("M1")[0].player_name = "first"
    summary.detail("M2")[0].player_name = "popular"
    summary.detail("M3")[0].player_name = "popular"
    summary.names = ["first", "popular"]
    assert summary.primary_name == "popular"
    assert render_summary_problems(summary).startswith("# 多场问题汇总：popular")
    summary.detail("M3")[0].player_name = "third"
    summary.names.append("third")
    assert summary.primary_name == "first"
    summary.detail("M3")[0].player_name = "popular"
    for match, kills, deaths in zip(summary.matches, (101, 202, 152), (91, 82, 100)):
        match.kills, match.deaths = kills, deaths
    digest = build_summary_digest(summary, baseline, cfg)
    assert "玩家：popular。用过的名字：first、popular、third。" in digest
    assert "合计：455 杀、273 死、0 次关键助攻；3 胜、0 负、0 平。" in digest
    payload = submission(overview="共 {match.kills} 杀、{match.deaths} 死。")
    payload["findings"][0]["drills"] = ["aim_botz"]
    sub = parse_submission(payload)
    assert check_summary_submission(sub, summary, digest, cfg, summary_numbers(summary, baseline)) == []
    result = diagnose_summary(summary, cfg, baseline, FakeProvider([payload]))
    assert result.report.startswith("# 多场复盘：popular · 3 场")
    assert "共 455 杀、273 死。" in result.report  # 占位符按整批合计填写
    assert "**怎么练**\n\n- Aim Botz" in result.report


def test_summary_queries_and_target_schema():
    summary, _, cfg = sample()
    tool = submit_tool(summary.detail("M1")[1], cfg, summary=True)
    pattern = tool["input_schema"]["$defs"]["Finding"]["properties"]["targets"]["items"]["pattern"]
    assert pattern.startswith("^M")
    assert {item["name"] for item in SUMMARY_TOOLS} == {"get_moment", "list_problem_hits"}
    value = json.loads(run_summary_tool("get_moment", {"match": "M2", "moment_id": "R1-death"}, summary))
    assert value["target"] == "M2:R1-death" and value["problems"][0]["problem"] == "slow_reaction"
    hits = json.loads(run_summary_tool("list_problem_hits", {"problem": "slow_reaction"}, summary))
    assert {item["target"] for item in hits} == {"M1:R1-death", "M2:R1-death"}
    with pytest.raises(ToolError, match="没有场次"):
        run_summary_tool("get_moment", {"match": "M9", "moment_id": "R1-death"}, summary)
    with pytest.raises(ToolError, match="没有编号"):
        run_summary_tool("get_moment", {"match": "M1", "moment_id": "R9-death"}, summary)


def test_checker_resolves_hits_per_match_and_restricts_numbers_and_ids():
    summary, baseline, cfg = sample()
    digest = build_summary_digest(summary, baseline, cfg)
    numbers = summary_numbers(summary, baseline)
    def codes(**changes):
        sub = parse_submission(submission(**changes))
        return {v.code for v in check_summary_submission(sub, summary, digest, cfg, numbers)}
    assert codes() == set()
    assert "precondition_failed" in codes(target="M3:R1-death")
    assert "raw_number" in codes(analysis="本次 TTD 为 1234 毫秒。")
    assert codes(analysis="本次 TTD 为 {M1:R1-death.ttd}，汇总在场 {match.rounds_played}。") == set()
    assert "slot_target_not_cited" in codes(analysis="{M2:R1-death.ttd} 值得复盘。")
    assert "id_in_text" in codes(analysis="M1:R1-death 值得复盘。")
    assert "match_mismatch" in codes(analysis="第 5 场值得复盘。")
    assert "match_mismatch" in codes(overview="第 5 场值得复盘。")


def test_summary_loop_valid_repair_prune_replace_and_unwrap():
    summary, baseline, cfg = sample()
    good = submission()
    provider = FakeProvider([good])
    result = diagnose_summary(summary, cfg, baseline, provider)
    assert result.repairs == 0 and "## 关键问题" in result.report
    assert "M1 · game1 · de_test" in result.report and "你在 Mid" in result.report
    assert "先在游戏里打开对应的 demo" in result.report
    assert "| M1 · game1 | M1:R1-death | 100 |" in result.report
    bad = submission(analysis="本次 TTD 为 1234 毫秒。")
    repaired = diagnose_summary(summary, cfg, baseline, FakeProvider([bad, good]))
    assert repaired.repairs == 1 and repaired.violations_history[0]
    wrapped = diagnose_summary(summary, cfg, baseline,
                               FakeProvider([{"report": json.dumps(good, ensure_ascii=False)}]))
    assert wrapped.raw_calls[0]["unwrapped"] == "report"
    cfg["ai"]["max_repairs"] = 0
    bad_overview = submission(overview="总计 999 场值得复盘。")
    replaced = diagnose_summary(summary, cfg, baseline, FakeProvider([bad_overview]))
    assert "AI 总览未通过检查，已替换" in replaced.report
    bad_extra = submission()
    bad_extra["findings"].append(dict(problem="other", targets=["M2:R1-death"],
        severity="low", analysis="本次 TTD 为 999 毫秒。", training="练习。"))
    pruned = diagnose_summary(summary, cfg, baseline, FakeProvider([bad_extra]))
    assert len(pruned.submission.findings) == 1
    assert pruned.dropped[0].path == "findings[1]"


def test_every_match_claim_checked_against_coverage():
    # 实测（2026-10-16 正式汇总）："压枪差……每一场都有出现"，实际 12/21 场
    summary, baseline, cfg = sample()   # slow_reaction 只在 M1、M2 命中，共 3 场
    digest = build_summary_digest(summary, baseline, cfg)
    def codes(analysis):
        sub = parse_submission(submission(analysis=analysis))
        return [v for v in check_summary_submission(sub, summary, digest, cfg, []) if v.code == "phrase_unsupported"]
    bad = codes("出枪慢每一场都有出现。")
    assert bad and "2/3" in bad[0].message
    assert codes("出枪慢出现在 {freq.slow_reaction.matches_with_hits} 场。") == []
