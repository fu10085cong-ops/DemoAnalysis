"""056：规则自己的可判断次数、按机会比较和跨场重复。"""

from pathlib import Path

import pytest

from cs2coach.baseline import problem_rates
from cs2coach.config import load_config
from cs2coach.features.models import MissedTrade, TradeChance
from cs2coach.features.problems import ProblemHit, ProblemReport, detect_problems, problem_frequency
from cs2coach.report.page_data import Row, _verdict, build_match_page
from cs2coach.summary.aggregate import aggregate_matches
from cs2coach.summary.discover import DemoRef
from tests.helpers.moments import make_mm, make_moment, make_shot
from tests.helpers.synthetic import A, B, C
from tests.test_radar import line, tier


def _baseline():
    return {"weapons": {"ak47": {
        "seen_to_first_shot_ms": {"p75": 500},
        "crosshair_initial_offset_m": {"p75": 2}}},
        "spray": {"ak47": [{"from": 3, "to": 9, "hit_pct": 80}]}}


def _detect(moment, **changes):
    cfg = load_config()
    cfg["problems"]["disabled"] = []
    return detect_problems(make_mm([moment], **changes), _baseline(), cfg)


@pytest.mark.parametrize("pid,hit,miss,skip", [
    ("slow_reaction",
     {"engagement": {"time_to_damage_s": .8, "seen_to_first_shot_s": .8}},
     {"engagement": {"time_to_damage_s": .3, "seen_to_first_shot_s": .3}},
     {"engagement": None}),
    ("poor_crosshair_placement",
     {"engagement": {"crosshair_initial_offset_m": 4}},
     {"engagement": {"crosshair_initial_offset_m": 1}},
     {"engagement": {"crosshair_initial_offset_m": None}}),
    ("died_without_seeing",
     {"tags": ["died_without_seeing"], "engagement": {"opponent_off_screen": True}},
     {"engagement": {"opponent_off_screen": False}},
     {"engagement": None}),
    ("spray_control",
     {"engagement": {"shots": [make_shot(tick=80+i, recoil_index=3+i) for i in range(6)]}},
     {"engagement": {"shots": [make_shot(tick=80+i, recoil_index=3+i, hit=True) for i in range(6)]}},
     {"engagement": {"shots": []}}),
])
def test_engagement_outcomes(pid, hit, miss, skip):
    reports = [_detect(make_moment(**data)) for data in (hit, miss, skip)]
    assert [sum(h.problem == pid for h in r.hits) for r in reports] == [1, 0, 0]
    assert [r.evaluated.get(pid, 0) for r in reports] == [1, 1, 0]


def test_crosshair_exemption_counts_but_exclusion_does_not():
    exempt = _detect(make_moment(engagement={"crosshair_initial_offset_m": 4},
                                 context={"distance": 2}))
    excluded = _detect(make_moment(engagement={"crosshair_initial_offset_m": 4}, through_smoke=True))
    assert exempt.evaluated["poor_crosshair_placement"] == 1
    assert exempt.skipped["poor_crosshair_placement/close_range"] == 1
    assert excluded.evaluated.get("poor_crosshair_placement", 0) == 0
    assert excluded.skipped["poor_crosshair_placement/through_smoke"] == 1
    no_cost = _detect(make_moment(kind="kill", engagement={"crosshair_initial_offset_m": 4,
                                                        "seen_to_first_shot_s": .2}))
    assert no_cost.evaluated["poor_crosshair_placement"] == 1
    assert no_cost.skipped["poor_crosshair_placement/no_cost"] == 1


def _chance(**changes):
    fields = dict(round=1, tick=100, teammate=B, killer=C, distance=10,
                  killer_traded_by_other=False, killer_in_view_s=1.0,
                  killer_first_in_view_s=0.0, alive_own=2, alive_enemy=2)
    return fields | changes


def test_trade_opportunities_include_success_and_skip_exclusions():
    cfg = load_config()
    missed = MissedTrade(**_chance())
    skipped = MissedTrade(**_chance(tick=200, killer_traded_by_other=True))
    success = TradeChance(**_chance(tick=300), traded_by_me=True)
    report = detect_problems(make_mm(missed_trades=[missed, skipped], trade_chances=[success]), _baseline(), cfg)
    assert sum(h.problem == "missed_trade" for h in report.hits) == 1
    assert report.evaluated["missed_trade"] == 2
    assert report.skipped["missed_trade/killer_traded_by_other"] == 1


def test_baseline_and_frequency_use_opportunity_rate_with_old_fallback():
    cfg = load_config()
    mm = make_mm([make_moment(engagement={"crosshair_initial_offset_m": 4})], rounds=10)
    rates = problem_rates([mm], _baseline(), cfg)
    assert rates["poor_crosshair_placement"]["evaluated"] == 1
    assert rates["poor_crosshair_placement"]["per_100_evaluated"] == 100
    hit = ProblemHit(problem="slow_reaction", target="R1-death", weapon="ak47", evidence={}, thresholds={})
    report = ProblemReport(hits=[hit] * 5, disabled=[], skipped={}, baseline_source=None,
                           evaluated={"slow_reaction": 20})
    fresh = {"problem_rates": {"slow_reaction": {"hits": 10, "rounds": 100,
                                                "evaluated": 100, "per_100_evaluated": 10}}}
    row = next(r for r in problem_frequency(report, make_mm(rounds=10), fresh, cfg)
               if r.problem == "slow_reaction")
    assert row.evaluated == 20 and row.per_100_evaluated == 25
    assert row.expected_hits == 2 and row.excess_hits == 3
    assert row.ratio == 2.5
    old = {"problem_rates": {"slow_reaction": {"hits": 10, "rounds": 100}}}
    fallback = next(r for r in problem_frequency(report, make_mm(rounds=10), old, cfg)
                    if r.problem == "slow_reaction")
    assert fallback.expected_hits == 1
    cached = ProblemReport.model_validate({"hits": [], "disabled": [], "skipped": {}, "baseline_source": None})
    assert cached.evaluated == {}
    cached_row = next(r for r in problem_frequency(cached, make_mm(rounds=10), fresh, cfg)
                      if r.problem == "slow_reaction")
    assert cached_row.expected_hits == 1
    fresh_empty = ProblemReport(hits=[], disabled=[], skipped={}, baseline_source=None, evaluated={})
    empty_row = next(r for r in problem_frequency(fresh_empty, make_mm(rounds=10), fresh, cfg)
                     if r.problem == "slow_reaction")
    assert empty_row.evaluated == 0 and empty_row.expected_hits == 0


def test_summary_merges_opportunities_and_match_counts():
    cfg = load_config()
    baseline = {"problem_rates": {"slow_reaction": {"hits": 10, "rounds": 100, "evaluated": 100}}}
    games = []
    for i, count in enumerate((2, 0, 1), 1):
        ref = DemoRef(path=Path(f"test{i}.dem"), member=None, match_id=f"test{i}",
                      map_name="de_test", player_name="测试玩家")
        hit = ProblemHit(problem="slow_reaction", target="R1-death", weapon="ak47", evidence={}, thresholds={})
        report = ProblemReport(hits=[hit] * count, disabled=[], skipped={}, baseline_source=None,
                               evaluated={"slow_reaction": 10})
        games.append((ref, make_mm(rounds=10, lobby=[line()]), report, (5, 5)))
    summary = aggregate_matches(A, games, baseline, cfg)
    row = next(r for r in summary.frequency if r.problem == "slow_reaction")
    assert (row.hits, row.evaluated, row.per_100_evaluated) == (3, 30, 10)
    assert (row.matches_with_hits, row.matches) == (2, 3)
    assert row.expected_hits == 3


def _row(pid, excess, hits, matches_with_hits):
    return Row(id=pid, name=pid, axis=None, you=10, target=5, unit="次", count=hits,
               expected=hits-excess, severity="bad", text="", examples=[],
               matches_with_hits=matches_with_hits, matches=10)


def test_focus_requires_repeat_share_and_breaks_excess_ties():
    cfg = load_config()
    rare = _row("少数场", 10, 20, 2)
    common = _row("多场", 8, 20, 7)
    text = _verdict("summary", [None] * 10, "A 段", [rare, common], [], [], cfg)
    assert "最该练的是多场" in text
    text = _verdict("summary", [None] * 10, "A 段", [rare], [], [], cfg)
    assert "集中在少数几场" in text and "最该练" not in text
    text = _verdict("match", [None], "A 段", [rare], [], [], cfg)
    assert "最该练的是少数场" in text
    tied = _row("重复更多", 8, 20, 8)
    text = _verdict("summary", [None] * 10, "A 段", [common, tied], [], [], cfg)
    assert "最该练的是重复更多" in text


def test_page_uses_opportunity_unit_and_match_scale():
    cfg = load_config()
    mm = make_mm(rounds=10, lobby=[line()])
    hit = ProblemHit(problem="missed_trade", target="R1", weapon=None, evidence={}, thresholds={})
    report = ProblemReport(hits=[hit] * 3, disabled=[], skipped={}, baseline_source=None,
                           evaluated={"missed_trade": 10})
    baseline = tier()
    baseline["problem_rates"] = {"missed_trade": {"hits": 10, "rounds": 100, "evaluated": 100}}
    frequency = problem_frequency(report, mm, {"problem_rates": baseline["problem_rates"]}, cfg)
    page = build_match_page(mm, report, frequency, cfg, {baseline["id"]: baseline})
    row, = page.tiers[0].problems
    assert row.you == 30 and row.target == 10
    assert row.opportunity_label == "补枪机会" and row.per_10_rounds == 3


def test_page_keeps_repeated_bad_problem_with_display_limit():
    cfg = load_config()
    cfg["radar"]["max_problems"] = 1
    mm = make_mm(rounds=100, lobby=[line()])
    report = ProblemReport(hits=[], disabled=[], skipped={}, baseline_source=None)
    from cs2coach.features.problems import FrequencyRow
    rows = [FrequencyRow(problem="spray_control", label="压枪差", hits=20,
                         per_10_rounds=2, pro_per_10_rounds=1, ratio=2, prominent=True,
                         matches_with_hits=1, matches=10),
            FrequencyRow(problem="poor_crosshair_placement", label="准星没提前放好", hits=15,
                         per_10_rounds=1.5, pro_per_10_rounds=1, ratio=1.5, prominent=True,
                         matches_with_hits=8, matches=10)]
    baseline = tier()
    baseline["problem_rates"] = {"spray_control": {"hits": 1, "rounds": 100},
                                  "poor_crosshair_placement": {"hits": 1, "rounds": 100}}
    from cs2coach.report.page_data import _rows
    _, problems = _rows([(mm, report, "M1"), (mm, report, "M2")], rows, cfg, baseline)
    assert [row.id for row in problems] == ["poor_crosshair_placement"]
