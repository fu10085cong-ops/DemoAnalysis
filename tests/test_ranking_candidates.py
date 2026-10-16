"""020：代表性排序与单场 / 多场候选限制，以及"下包前"说法。"""

from cs2coach.config import load_config
from cs2coach.features.models import MissedTrade
from cs2coach.features.problems import ProblemHit, ProblemReport
from cs2coach.features.ranking import candidate_targets, ranked_hits
from cs2coach.summary.aggregate import aggregate_matches
from cs2coach.summary.digest import build_summary_digest
from cs2coach.summary.discover import DemoRef
from cs2coach.tools.checker import check_submission, check_summary_submission
from cs2coach.tools.digest import build_digest
from cs2coach.tools.submission import parse_submission
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import A, B, C
from tests.test_moments import find_moments, scenario

CFG = load_config()
TTDS = {"R1-kill1": 520, "R2-kill1": 900, "R3-kill1": 700, "R4-kill1": 1000}  # 阈值 500：偏离 4%、80%、40%、100%


def hit(target, ttd, problem="slow_reaction"):
    return ProblemHit(problem=problem, target=target, weapon="ak47", evidence={"ttd_ms": ttd},
                      thresholds={"ttd_ms": 500, "source": "pro_p75"})


def sample():
    moments = [make_moment(kind="kill", id=mid, round=int(mid[1])) for mid in TTDS]
    moments.append(make_moment(kind="kill", id="R5-kill1", round=5, context={"bomb_planted": True}))
    rows = [MissedTrade(round=6, tick=600, teammate=B, killer=C, distance=300.0, killer_traded_by_other=False,
                        bomb_planted=True),
            MissedTrade(round=7, tick=700, teammate=B, killer=C, distance=200.0, killer_traded_by_other=False,
                        bomb_planted=False)]
    mm = make_mm(moments, rounds=7, missed_trades=rows)
    report = ProblemReport(hits=[hit(t, v) for t, v in TTDS.items()] + [
        ProblemHit(problem="won_duel_seen_first", target="R5-kill1", weapon="ak47", evidence={}, thresholds={}),
        ProblemHit(problem="missed_trade", target="R6", weapon=None, evidence={"distance": 300.0}, thresholds={}),
        ProblemHit(problem="missed_trade", target="R7", weapon=None, evidence={"distance": 200.0}, thresholds={})],
        disabled=[], skipped={}, baseline_source="测试")
    return mm, report


def check(findings=None, patterns=(), highlights=()):
    mm, report = sample()
    sub = parse_submission(dict(overview="本场值得复盘。", findings=findings or [finding(["R4-kill1"])],
                                patterns=list(patterns), highlights=list(highlights), training_plan=["练。"]))
    return check_submission(sub, mm, report, "地图 de_test。", CFG, None, (1, 0), [], None)


def finding(targets, problem="slow_reaction", analysis="这次交火值得复盘。"):
    return dict(problem=problem, targets=targets, severity="medium", analysis=analysis, training="练。", drills=[])


def codes(v):
    return {x.code for x in v}


def test_ranking_by_deviation_and_missed_trade_rule():
    mm, report = sample()
    assert [h.target for h in ranked_hits(mm, report.hits, "slow_reaction")] == \
        ["R4-kill1", "R2-kill1", "R3-kill1", "R1-kill1"]
    assert candidate_targets(mm, report.hits, "slow_reaction", 3) == ["R4-kill1", "R2-kill1", "R3-kill1"]
    assert candidate_targets(mm, report.hits, "missed_trade", 3) == ["R7", "R6"]  # 近的在前


def test_single_finding_must_use_top_candidates():
    assert check() == []
    assert check([finding(["R2-kill1", "R3-kill1"])]) == []
    v = check([finding(["R1-kill1"])])  # 只慢 4%，排第 4
    assert codes(v) == {"not_representative"}
    assert "R4-kill1、R2-kill1、R3-kill1" in v[0].message and "patterns" in v[0].message


def test_few_hits_are_all_candidates_and_other_entries_unrestricted():
    assert check([finding(["R6"], problem="missed_trade")]) == []
    assert check([finding(["R4-kill1"])],
                 patterns=[dict(problem="slow_reaction", targets=list(TTDS), analysis="同类问题。")],
                 highlights=[dict(problem="won_duel_seen_first", targets=["R5-kill1"], analysis="赢下对枪。")]) == []


def test_target_without_hit_reports_only_precondition():
    assert codes(check([finding(["R5-kill1"])])) == {"precondition_failed"}


def test_digest_marks_candidates():
    match = scenario()
    mm = find_moments(match, A, CFG)
    report = ProblemReport(hits=[hit(f"R{i}-kill1", 500 + 100 * i) for i in range(1, 5)],
                           disabled=[], skipped={}, baseline_source="测试")
    text = build_digest(match, mm, None, report, [], CFG)
    assert text.index("- 〔候选〕R4-kill1") < text.index("- 〔候选〕R3-kill1") < text.index("- 〔候选〕R2-kill1")
    assert "- R1-kill1：" in text and "〔候选〕R1-kill1" not in text


def test_summary_finding_must_use_listed_representatives():
    games = []
    for i, ttds in enumerate(((600, 900, 1000), (700,)), 1):
        moments = [make_moment(kind="kill", id=f"R{j}-kill1", round=j) for j in range(1, len(ttds) + 1)]
        report = ProblemReport(hits=[hit(f"R{j}-kill1", ttd) for j, ttd in enumerate(ttds, 1)],
                               disabled=[], skipped={}, baseline_source="测试")
        ref = DemoRef(path=f"g{i}.dem", member=None, match_id=f"g{i}", map_name="de_test", player_name="alice")
        games.append((ref, make_mm(moments, rounds=10, map_name="de_test", player_name="alice"), report, (6, 4)))
    summary = aggregate_matches(A, games, None, CFG)
    digest = build_summary_digest(summary, None, CFG)
    cfg = {**CFG, "summary": {**CFG["summary"], "representatives_per_problem": 2}}

    def summary_codes(targets):
        sub = parse_submission(dict(overview="这些比赛值得复盘。", findings=[finding(targets)],
                                    patterns=[], highlights=[], training_plan=["练。"]))
        return codes(check_summary_submission(sub, summary, digest, cfg, []))

    assert summary_codes(["M1:R3-kill1", "M2:R1-kill1"]) == set()  # 每场最偏的一个
    assert summary_codes(["M1:R1-kill1"]) == {"not_representative"}


def test_before_plant_phrase():
    assert codes(check([finding(["R4-kill1"], analysis="下包前的这次交火值得复盘。")])) == set()
    v = check([finding(["R5-kill1"], problem="other", analysis="下包前的这次交火值得复盘。")])
    assert "phrase_unsupported" in codes(v) and "not_planted" in next(x.message for x in v if x.code == "phrase_unsupported")
    assert codes(check([finding(["R7"], problem="missed_trade", analysis="包还没下时没有补上。")])) == set()
    assert "phrase_unsupported" in codes(check([finding(["R6"], problem="missed_trade", analysis="包还没下时没有补上。")]))
    # 原有的“已下包”规则不变
    assert codes(check([finding(["R4-kill1"], analysis="已下包后的交火。")])) == {"phrase_unsupported"}
