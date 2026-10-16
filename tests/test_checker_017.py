"""地图、百分比和训练白名单，以及 016 留存提交的回归。"""

import json
from pathlib import Path

from cs2coach.config import load_config
from cs2coach.features.problems import ProblemHit, ProblemReport
from cs2coach.summary.aggregate import PlayerSummary
from cs2coach.summary.digest import build_summary_digest, summary_numbers
from cs2coach.tools.checker import check_submission, check_summary_submission
from cs2coach.tools.submission import parse_submission, submit_tool
from tests.helpers.moments import make_mm, make_moment


FIXTURES = Path(__file__).parent / "fixtures"


def _report(*problems, evidence=None):
    return ProblemReport(hits=[ProblemHit(problem=p, target="R1-death", weapon="ak47",
        evidence=evidence or {}, thresholds={}) for p in problems],
        disabled=[], skipped={}, baseline_source="测试")


def _sub(**changes):
    data = dict(overview="本场值得复盘。", findings=[dict(problem="slow_reaction",
        targets=["R1-death"], severity="medium", analysis="接敌准备需改善。",
        training="在 Aim Botz 练习。", drills=["aim_botz"])],
        patterns=[], highlights=[], training_plan=["在 Aim Botz 练习。"])
    return parse_submission(data | changes)


def test_single_map_aliases_and_overview():
    cfg = load_config()
    mm = make_mm([make_moment()], map_name="de_dust2")
    report = _report("slow_reaction")
    good = _sub(findings=[_sub().findings[0].model_copy(update={"analysis": "沙二接敌准备需改善。"})])
    assert check_submission(good, mm, report, "地图 de_dust2。", cfg) == []
    bad = _sub(overview="米拉日值得复盘。", findings=[_sub().findings[0].model_copy(update={"analysis": "Mirage 接敌准备需改善。"})])
    mismatches = [v for v in check_submission(bad, mm, report, "地图 de_dust2。", cfg) if v.code == "map_mismatch"]
    assert {v.path for v in mismatches} == {"overview", "findings[0].analysis"}
    assert all("de_dust2" in v.message for v in mismatches)


def test_percent_type_and_plain_number():
    cfg = load_config()
    mm = make_mm([make_moment()], map_name="de_dust2")
    report = _report("slow_reaction", evidence={"hits": 68, "spray_accuracy_pct": 25.7, "chance": .002})
    def codes(analysis):
        sub = _sub(findings=[_sub().findings[0].model_copy(update={"analysis": analysis})])
        return [v for v in check_submission(sub, mm, report, "地图 de_dust2。", cfg) if v.code == "number_not_found"]
    assert not codes("命中 68 次，扫射命中率 25.7%，偶然概率 0.2%。")
    bad = codes("命中率 68%。")
    assert bad and "68% 不是数据中的百分比" in bad[0].message and "hits" in bad[0].message


def test_drill_applicability_text_and_schema():
    cfg = load_config()
    mm = make_mm([make_moment()], map_name="de_dust2")
    report = _report("slow_reaction")
    def violations(drills, training="在 Aim Botz 练习。", plan=None):
        finding = _sub().findings[0].model_copy(update={"drills": drills, "training": training})
        sub = _sub(findings=[finding], training_plan=plan or ["在 Aim Botz 练习。"])
        return check_submission(sub, mm, report, "地图 de_dust2。", cfg)
    assert violations(["aim_botz"]) == []
    assert any(v.code == "drill_not_allowed" for v in violations(["recoil_master"]))
    assert any(v.code == "drill_not_allowed" for v in violations(["5e_aim"]))
    assert any(v.code == "unknown_drill" for v in violations([], "在 Deagle Duel 练习。"))
    assert any(v.code == "unknown_drill" for v in violations([], "在《Spray Control》练习。"))
    assert any(v.code == "unknown_drill" for v in violations([], plan=["在《Inferno B》练习。"]))
    schema = submit_tool(report, cfg)["input_schema"]["$defs"]["Finding"]
    assert set(schema["properties"]["drills"]["items"]["enum"]) == {d["id"] for d in cfg["training"]["drills"]}
    assert schema["properties"]["drills"]["maxItems"] == 2


def test_mixed_aim_drill_requires_two_distinct_problems_and_utility_has_no_drill():
    cfg = load_config()
    mm = make_mm([make_moment()], map_name="de_dust2")
    report = _report("slow_reaction", "shot_while_moving", "dry_peek_with_utility")
    aim = _sub().findings[0].model_copy(update={"drills": ["5e_aim"], "training": "在 5E 的练枪图练习。"})
    another = _sub().findings[0].model_copy(update={"problem": "shot_while_moving", "drills": []})
    utility = _sub().findings[0].model_copy(update={"problem": "dry_peek_with_utility", "drills": ["aim_botz"]})
    good = _sub(findings=[aim, another])
    assert not any(v.code == "drill_not_allowed" for v in check_submission(good, mm, report, "地图 de_dust2。", cfg))
    bad = _sub(findings=[aim, utility])
    violations = check_submission(bad, mm, report, "地图 de_dust2。", cfg)
    assert {v.path for v in violations if v.code == "drill_not_allowed"} == {
        "findings[0].drills[0]", "findings[1].drills[0]"}


def test_summary_map_pairing_and_overview_map():
    cfg = load_config()
    summary = PlayerSummary.model_validate(json.loads((FIXTURES / "016_local_summary.json").read_text(encoding="utf-8")))
    summary._details = {match.number: (make_mm([], rounds=match.rounds, map_name=match.map_name), _report())
                        for match in summary.matches}
    base = _sub().model_dump()
    base["findings"][0]["targets"] = ["M12:R4-death"]
    base["findings"][0]["analysis"] = "de_dust2 4回合（M12）值得复盘。"
    digest = build_summary_digest(summary, None, cfg)
    def maps(data):
        sub = parse_submission(data)
        return [v for v in check_summary_submission(sub, summary, digest, cfg, summary_numbers(summary, None))
                if v.code == "map_mismatch"]
    assert maps(base) == []
    base["findings"][0]["analysis"] = "de_inferno 4回合（M12）值得复盘。"
    assert any("M12 实际地图是 de_dust2" in v.message for v in maps(base))
    base["overview"] = "Cache 值得复盘。"
    assert any(v.path == "overview" for v in maps(base))


def test_016_local_submission_regression():
    """原始 016 提交和实测汇总均固定入库；详情只提供检查器所需的空壳。"""
    cfg = load_config()
    summary = PlayerSummary.model_validate(json.loads((FIXTURES / "016_local_summary.json").read_text(encoding="utf-8")))
    summary._details = {match.number: (make_mm([], rounds=match.rounds, map_name=match.map_name), _report())
                        for match in summary.matches}
    sub = parse_submission(json.loads((FIXTURES / "016_local_submission.json").read_text(encoding="utf-8")))
    digest = build_summary_digest(summary, None, cfg)
    violations = check_summary_submission(sub, summary, digest, cfg, summary_numbers(summary, None))
    codes = {v.code for v in violations}
    assert {"map_mismatch", "number_not_found", "unknown_drill"} <= codes
    assert any("M12 实际地图是 de_dust2" in v.message for v in violations)
    assert any("68% 不是数据中的百分比" in v.message for v in violations)
    assert any("Deagle Duel" in v.message for v in violations)
