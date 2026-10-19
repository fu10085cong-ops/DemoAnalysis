"""报告页分段比较、问题行、跳转模板与匿名基准。"""

import json

import pytest

from cs2coach.config import load_config as _load_config


def load_config(*args, **kwargs):
    """生产配置加上补枪轴：这些测试用补枪指标做优点和问题样例，检验的是页面逻辑，与雷达实际配了哪些维度无关
    （2026-10-18 补枪移出雷达，生产配置见 tests/test_radar.py::test_configured_axes）。"""
    cfg = _load_config(*args, **kwargs)
    cfg["radar"]["axes"].append({"id": "trade", "name": "补枪", "metrics": ["missed_trade_pct"]})
    return cfg
from cs2coach.features.problems import FrequencyRow, ProblemHit, ProblemReport
from cs2coach.features.highlights import find_highlights
from cs2coach.features.radar import axis_values, player_metrics
from cs2coach.report.page_data import build_match_page, build_summary_page, load_tiers
from cs2coach.summary.aggregate import aggregate_matches
from cs2coach.summary.discover import DemoRef
from scripts.build_tier_baseline import build_tier_baseline, demo_entries
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.moments import make_shot
from tests.helpers.synthetic import A, B, C, make_round
from tests.test_radar import line, tier


def report():
    return ProblemReport(hits=[ProblemHit(problem="slow_reaction", target="R1-death", weapon="ak47",
        evidence={}, thresholds={})], disabled=[], skipped={}, baseline_source=None)


def frequency(you, hits=3):
    return [FrequencyRow(problem="slow_reaction", label="出枪慢", hits=hits, per_10_rounds=you,
        pro_per_10_rounds=1, ratio=you, prominent=True)]


def sample():
    data = tier(trade_kill_events_per_round=[0, .1, .2], traded_death_pct=[0, 20, 40], missed_trade_pct=[0, 20, 40])
    data["problem_rates"] = {"slow_reaction": {"per_10_rounds": 2, "hits": 20, "rounds": 100}}
    return {data["id"]: data}


# 10 回合期望 2 次；实际 5 次的泊松尾概率仍大于 5%，6 次才判 bad。
@pytest.mark.parametrize("hits,severity", [(2, None), (3, "warn"), (4, "warn"), (5, "warn"), (6, "bad")])
def test_severity_and_problem_template(hits, severity):
    mm = make_mm([make_moment()], lobby=[line()], rounds=10)
    page = build_match_page(mm, report(), frequency(hits, hits), load_config(), sample())
    if severity is None:
        assert page.tiers[0].problems == []
        return
    problem, = page.tiers[0].problems
    assert problem.severity == severity and problem.target == 2 and problem.count == hits and problem.expected == 2
    assert "。" == problem.text[-1] and problem.text.count("。") == 1
    assert len(problem.examples) == 1
    assert problem.examples[0].match_label == "本场"
    assert problem.examples[0].situation == "T 方，2v2，未下包"
    if severity == "bad":
        assert page.tiers[0].verdict.startswith("相对业余（临时），最该练的是出枪慢：这场出现 6 次，按业余（临时）的频率约 2 次。")
    else:
        assert page.tiers[0].verdict == "相对业余（临时），这场没有明显偏多的问题。可用的指标不够多，暂不判断整体强弱。"


def test_missing_tier_and_empty_samples(tmp_path, monkeypatch):
    monkeypatch.setattr("cs2coach.report.page_data.TIER_DIR", tmp_path)
    assert load_tiers(load_config()) == {}
    page = build_match_page(make_mm(), report(), frequency(3), load_config(), {})
    assert page.tiers == []
    assert "verdict" not in page.model_dump()
    data = sample()
    (tmp_path / "amateur_tmp.json").write_text(json.dumps(data["amateur_tmp"]), encoding="utf-8")
    assert load_tiers(load_config()) == data


@pytest.mark.parametrize("lines,expected", [
    ([line(kills=20, deaths=10, trade_chances=5), line(steamid=B, name="队友甲", kills=21, deaths=15, trade_chances=5)], "目标玩家，20/10"),
    ([line(kills=20, deaths=10, trade_chances=5), line(steamid=B, name="队友甲", kills=21, deaths=11, trade_chances=5)], "队友甲，21/11"),
    ([line(steamid=B, name="队友甲", trade_chances=5), line(trade_chances=5)], "目标玩家，10/5"),
])
def test_best_player_and_average_include_target(lines, expected):
    page = build_match_page(make_mm(lobby=lines), report(), [], load_config(), sample())
    avg, best, target = page.tiers[0].comparisons
    assert best.name == f"本场最佳（{expected}）"
    assert avg.id == "match_avg" and target.values == [50] * len(load_config()["radar"]["axes"])
    expected_average = sum(axis_values(player_metrics(item, load_config()), sample()["amateur_tmp"],
                                      load_config()["radar"]["axes"], load_config())[4] for item in lines) / len(lines)
    assert avg.values[4] == expected_average


def test_commands_follow_review_pack_templates():
    cfg = load_config()
    cfg["review"].update(demo_subdir="review/custom", playdemo_template="playdemo {subdir}/{demo_stem}",
                         jump_template="demo_gototick {tick}; demo_pause")
    mm = make_mm([make_moment()], demo="example.dem", lobby=[line()])
    command = build_match_page(mm, report(), frequency(3), cfg, sample()).tiers[0].problems[0].examples[0].command
    assert command.playdemo == cfg["review"]["playdemo_template"].format(subdir="review/custom", demo_stem="example")
    assert command.jump == cfg["review"]["jump_template"].format(tick=1)


def test_anonymous_baseline_uses_pro_thresholds(monkeypatch):
    seen = []
    monkeypatch.setattr("scripts.build_tier_baseline.problem_rates", lambda mms, pro, cfg: seen.append(pro) or {})
    pro = {"职业阈值": 123}
    mm = make_mm(lobby=[line()])
    data = build_tier_baseline({"id": "amateur_tmp", "name": "业余（临时）", "dirs": ["private.dem"]},
                               [mm], 1, pro, load_config())
    serialized = json.dumps(data, ensure_ascii=False)
    assert seen == [pro]
    for private in (str(A), "目标玩家", "alice", "synthetic.dem", "private.dem", "steamid", "dirs"):
        assert private not in serialized
    assert data["source"] == "1 份 demo、1 人次"
    assert data["metrics"]["trade_kill_events_per_round"] == {"median": .2, "n": 1, "values": [.2]}


def test_recursive_demo_discovery_all_formats_and_extraction_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.build_tier_baseline.ROOT", tmp_path)
    paths = ["d/a.dem", "d/sub/b.dem.gz", "d/c.dem.bz2", "d/archive.zip", "d/archive.rar", "d/.extracted/a.dem"]
    for name in paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr("scripts.build_tier_baseline._members", lambda p:
                        ["zip.dem"] if p.suffix == ".zip" else ["rar.dem"] if p.suffix == ".rar" else [None])
    entries = demo_entries({"dirs": ["d"]})
    assert len(entries) == 5 and all(".extracted" not in p.parts for p, _ in entries)


def test_demo_discovery_skips_excluded_matches_and_broken_archives(tmp_path, monkeypatch, capsys):
    # 5E 分段：同时出现在两个段位文件夹的比赛用 exclude 跳过；下载不完整的压缩包跳过并提示
    import zipfile
    monkeypatch.setattr("scripts.build_tier_baseline.ROOT", tmp_path)
    folder = tmp_path / "d"
    folder.mkdir()
    for name in ("keep.dem", "dup.dem", "broken.zip"):
        (folder / name).touch()
    entries = demo_entries({"dirs": ["d"], "exclude": ["dup"]})
    assert [p.name for p, _ in entries] == ["keep.dem"]
    assert "broken.zip" in capsys.readouterr().err
    assert not zipfile.is_zipfile(folder / "broken.zip")


def test_summary_median_of_percentile_axes_and_highlights():
    cfg = load_config()
    games = []
    for number, trades in enumerate([0, 1, 2]):
        mm = make_mm([make_moment("kill", tags=["opening_kill"])],
                     lobby=[line(trade_chances=5, missed_trade_chances=trades)], rounds=10)
        mm.highlights = find_highlights(mm, [make_round()])
        ref = DemoRef(path="test.dem", member=None, match_id=f"a{number}", map_name="de_dust2", player_name="目标玩家")
        games.append((ref, mm, report(), (6, 4)))
    summary = aggregate_matches(A, games, None, cfg)
    page = build_summary_page(summary, cfg, sample())
    # 漏补率分别为 0%、20%、40%，补枪维度百分位的中位数为 50。
    assert page.tiers[0].you[4] == 50
    assert [c.id for c in page.tiers[0].comparisons] == ["tier_median"]
    assert len(page.highlights) == 3 and page.highlights[0].command.jump == "demo_gototick 1"


def test_strength_examples_pick_best_then_sort_by_tick():
    moments = [make_moment("kill", tick=tick, jump_tick=tick-10, engagement={"time_to_damage_s": ttd})
               for tick, ttd in [(300, .1), (100, .2), (200, .8)]]
    from tests.test_summary import stats
    mm = make_mm(moments, lobby=[line(aim=stats(100))])
    cfg = load_config()
    cfg["radar"]["max_examples"] = 2
    baseline = tier(time_to_damage_ms=[200, 300, 400])
    page = build_match_page(mm, report(), [], cfg, {baseline["id"]: baseline})
    row, = page.tiers[0].strengths
    assert row.id == "time_to_damage_ms" and row.target == 300
    assert [e.tick for e in row.examples] == [90, 290]
    assert len(row.examples) == 2


def test_crosshair_strength_uses_eligible_kills_in_offset_order():
    from cs2coach.report.page_data import _strength_moments
    from tests.test_summary import stats
    cfg = load_config()
    cfg["radar"]["max_examples"] = 2
    moments = [make_moment("kill", tick=tick, jump_tick=tick,
                           engagement={"crosshair_initial_offset_m": value, **extra})
               for tick, value, extra in [(300, .2, {}), (100, .8, {}), (200, 1.5, {}),
                                          (400, .01, {"airborne_near_seen": True})]]
    moments.append(make_moment("death", engagement={"crosshair_initial_offset_m": .01}))
    aim = stats(100).model_copy(update={"crosshair_offset_m": 1.03, "crosshair_offset_samples": 5})
    mm = make_mm(moments, lobby=[line(aim=aim)], aim_stats=aim)
    assert [m.tick for m in _strength_moments(mm, "crosshair_offset_m", 2, cfg)] == [300, 100]
    data = tier(crosshair_offset_m=[2, 3, 4])
    row, = build_match_page(mm, report(), [], cfg, {data["id"]: data}).tiers[0].strengths
    assert row.name == "预瞄" and row.unit == "米" and row.you == 1.03 and row.samples == 5
    # 先按偏差选出最小的两次，再与命中速度一样按比赛和时间顺序展示。
    assert [e.tick for e in row.examples] == [100, 300]
    # 多场也按同一指标跨场选择。
    other = make_mm([make_moment("kill", tick=500, jump_tick=500,
                                 engagement={"crosshair_initial_offset_m": .1})], lobby=[line(aim=aim)], aim_stats=aim)
    games = [(DemoRef(path="test.dem", member=None, match_id=str(i), map_name="de_test", player_name="目标玩家"),
              match, report(), (5, 5)) for i, match in enumerate([mm, other])]
    summary = aggregate_matches(A, games, None, cfg)
    row, = build_summary_page(summary, cfg, {data["id"]: data}).tiers[0].strengths
    assert [e.tick for e in row.examples] == [300, 500]
    assert row.samples == 10 and summary.aim_overall["crosshair_offset_m"].median == 1.03


@pytest.mark.parametrize("metric", ["utility_damage_per_round", "wasted_utility_avg"])
def test_utility_damage_does_not_borrow_flash_examples(metric):
    from cs2coach.report.page_data import _strength_moments
    mm = make_mm([make_moment("assist", tags=["flash_assist"])])
    assert _strength_moments(mm, metric, 5, load_config()) == []


def test_single_shot_with_high_recoil_is_not_spray_example():
    from cs2coach.report.page_data import _strength_moments
    mm = make_mm([make_moment("kill", engagement={"shots": [make_shot(recoil_index=4)]})])
    assert _strength_moments(mm, "spray_accuracy_pct", 5, load_config()) == []


def test_build_tier_command_coverage_failures_and_pro_manifest(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from scripts import build_tier_baseline as builder

    config = tmp_path / "tiers.toml"
    config.write_text('[[tier]]\nid = "pro"\nname = "职业"\nmanifest = "manifest.toml"\n', encoding="utf-8")
    (tmp_path / "manifest.toml").write_text(
        '[[demos]]\narchive = "bad.zip"\nmember = "bad.dem"\n'
        '[[demos]]\narchive = "good.zip"\nmember = "good.dem"\n', encoding="utf-8")
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "TIER_CONFIG", config)
    monkeypatch.setattr(builder, "TIER_DIR", tmp_path / "output")
    monkeypatch.setattr(builder, "resolve_demo", lambda path, member=None: path)
    from tests.test_moments import scenario
    match = scenario()
    match.players[B].coverage = 0

    def parse(path, cfg):
        if path.name == "bad.zip":
            raise ValueError("损坏的样本")
        return match

    seen = []
    pro = {"职业阈值": 123}
    monkeypatch.setattr(builder, "parse_match", parse)
    monkeypatch.setattr(builder, "load_baseline", lambda: pro)
    monkeypatch.setattr(builder, "Sight", lambda *args: SimpleNamespace(available=True))
    monkeypatch.setattr(builder, "find_moments", lambda match, sid, *args, **kwargs: make_mm(player=sid))
    monkeypatch.setattr(builder, "lobby_line", lambda match, sid, *args, **kwargs: line(steamid=sid))
    monkeypatch.setattr(builder, "problem_rates", lambda mms, baseline, cfg:
                        seen.append((len(mms), baseline)) or {})
    assert builder.main(["--all", "--jobs", "1"]) == 0
    data = json.loads((tmp_path / "output/pro.json").read_text(encoding="utf-8"))
    assert seen == [(3, pro)] and data["source"] == "1 份 demo、3 人次"
    assert data["metrics"]["trade_kill_events_per_round"]["values"] == [.2, .2, .2]
    assert str(A) not in json.dumps(data)


def test_highlights_capped_and_ranked_by_weight():
    """一场能挑出近十个高光，汇总近百个；页面只留最有分量的几条（2026-10-18 审查 043）。"""
    from cs2coach.features.models import Highlight as FeatureHighlight
    cfg = load_config()
    cfg["radar"]["max_highlights"] = 3
    mm = make_mm([make_moment()], lobby=[line()])
    mm.highlights = [
        FeatureHighlight(round=1, kind="flash_assist", title="R1 闪光助攻", time="0:10", what="", tick=10),
        FeatureHighlight(round=2, kind="multikill", title="R2 三杀", time="0:10", what="", tick=20, size=3),
        FeatureHighlight(round=3, kind="clutch", title="R3 1v2 残局获胜", time="0:10", what="", tick=30, size=2),
        FeatureHighlight(round=4, kind="multikill", title="R4 五杀", time="0:10", what="", tick=40, size=5),
        FeatureHighlight(round=5, kind="opening_win", title="R5 首杀并赢下", time="0:10", what="", tick=50),
    ]
    page = build_match_page(mm, report(), frequency(1), cfg, sample())
    assert [h.title for h in page.highlights] == ["R3 1v2 残局获胜", "R4 五杀", "R2 三杀"]


def test_verdict_names_weakest_axis_when_nothing_is_bad():
    data = sample()
    # 道具伤害 10/回合，排在样本的后段：雷达上是短板
    data["amateur_tmp"]["metrics"]["utility_damage_per_round"] = {"median": 30, "n": 3, "values": [20, 30, 40]}
    mm = make_mm([make_moment()], lobby=[line()])
    page = build_match_page(mm, report(), frequency(1), load_config(), data)
    assert "可用的指标不够多" in page.tiers[0].verdict
    assert "最弱的是" not in page.tiers[0].verdict


def test_axis_listed_as_problem_is_not_a_strength_and_units_match_metrics():
    """问题维度不能同时列优点；漏补率用百分比，问题频率仍用每十回合次数。"""
    from tests.test_summary import stats
    mm = make_mm([make_moment("kill", tags=["trade_kill"])],
                 lobby=[line(aim=stats(100), trade_chances=5)], rounds=10)
    baseline = tier(time_to_damage_ms=[200, 300, 400], missed_trade_pct=[20, 40, 60])
    baseline["problem_rates"] = {"slow_reaction": {"per_10_rounds": 2, "hits": 20, "rounds": 100}}
    tiers = {baseline["id"]: baseline}
    # 出枪慢没有命中偏多：出枪速度可以算优点
    page = build_match_page(mm, report(), frequency(1, 1), load_config(), tiers)
    assert {row.id for row in page.tiers[0].strengths} == {"time_to_damage_ms", "missed_trade_pct"}
    trade = next(row for row in page.tiers[0].strengths if row.id == "missed_trade_pct")
    assert trade.you == 0 and trade.target == 40 and trade.unit == "%" and "越低越好" in trade.measure
    # 出枪慢命中偏多（warn）：出枪速度维度不再列为优点
    page = build_match_page(mm, report(), frequency(3, 3), load_config(), tiers)
    assert {row.id for row in page.tiers[0].strengths} == {"missed_trade_pct"}
    assert page.tiers[0].problems[0].measure == "每 10 回合出现的次数" and page.tiers[0].problems[0].unit == "次"


def test_verdict_picks_largest_excess_not_largest_ratio():
    """产品负责人 2026-10-18（另一名业余玩家）：压枪差 7 次、倍数 1.59 排到了准星 34 次、倍数 1.48 前面。"""
    data = sample()
    data["amateur_tmp"]["problem_rates"] = {"spray_control": {"per_10_rounds": 0.26, "hits": 26, "rounds": 1000},
                                            "poor_crosshair_placement": {"per_10_rounds": 1.36, "hits": 136, "rounds": 1000}}
    rows = [FrequencyRow(problem="spray_control", label="压枪差", hits=7, per_10_rounds=0.414,
                         pro_per_10_rounds=0.07, ratio=5.97, prominent=True),
            FrequencyRow(problem="poor_crosshair_placement", label="准星没提前放好", hits=34, per_10_rounds=2.012,
                         pro_per_10_rounds=0.67, ratio=3.01, prominent=True)]
    mm = make_mm([make_moment()], lobby=[line()], rounds=169)
    page = build_match_page(mm, report(), rows, load_config(), data)
    assert [p.id for p in page.tiers[0].problems] == ["poor_crosshair_placement", "spray_control"]
    assert page.tiers[0].verdict.startswith("相对业余（临时），最该练的是准星没提前放好：这场出现 34 次，按业余（临时）的频率约 23 次。")
    # 压枪差倍数够高，但只多出约 2.6 次：黄色，不是红色
    assert [p.severity for p in page.tiers[0].problems] == ["bad", "warn"]


def test_tier_switch_reselects_all_sections():
    from tests.test_summary import stats
    high = tier(time_to_damage_ms=[200, 300, 400])
    low = tier(time_to_damage_ms=[10, 20, 30])
    high.update(id="high", name="分段甲", problem_rates={"slow_reaction": {"hits": 5, "rounds": 10, "per_10_rounds": 5}})
    low.update(id="low", name="分段乙", problem_rates={"slow_reaction": {"hits": 1, "rounds": 10, "per_10_rounds": 1}})
    mm = make_mm([make_moment()], lobby=[line(aim=stats(100))], rounds=10)
    page = build_match_page(mm, report(), frequency(4, 4), load_config(), {"high": high, "low": low})
    a, b = page.tiers
    assert not a.problems and a.strengths[0].percentile == 100
    assert b.problems[0].count == 4 and b.problems[0].severity == "bad" and not b.strengths
    assert "最该练" not in a.verdict and "最该练的是出枪慢" in b.verdict
    assert "分段乙" in b.verdict and "整体强弱" in a.verdict
    assert not {"verdict", "strengths", "problems", "problem_bad_ratio"}.intersection(page.model_dump())
    assert "problem_targets" not in b.model_dump()


def test_summary_strength_uses_median_of_percentiles():
    from tests.test_summary import stats
    cfg = load_config()
    games = []
    for index, ttd in enumerate([100, 300]):
        mm = make_mm(lobby=[line(aim=stats(ttd))], rounds=10)
        ref = DemoRef(path="test.dem", member=None, match_id=f"a{index}", map_name="de_test", player_name="目标玩家")
        games.append((ref, mm, ProblemReport(hits=[], disabled=[], skipped={}, baseline_source=None), (5, 5)))
    data = tier(time_to_damage_ms=[201, 250, 299])
    summary = aggregate_matches(A, games, None, cfg)
    page = build_summary_page(summary, cfg, {data["id"]: data})
    view, = page.tiers
    assert view.you[0] == 50 and not view.strengths
    cfg["radar"]["strength_min_percentile"] = 50
    row, = build_summary_page(summary, cfg, {data["id"]: data}).tiers[0].strengths
    assert row.you == 200 and row.percentile == 50 and row.samples == 10
    assert row.measure.endswith("（典型单场）")
    assert row.text == "在业余（临时）里排在第 50 百分位。"


def test_missed_trade_examples_match_exact_hit_evidence():
    from cs2coach.features.models import MissedTrade
    hit = MissedTrade(round=1, tick=100, teammate=B, killer=C, distance=10, killer_traded_by_other=False)
    excluded = hit.model_copy(update={"tick": 200, "teammate": A, "killer_traded_by_other": True})
    # tick 相同时仍必须核对队友和击杀者。
    other = hit.model_copy(update={"teammate": C, "killer": B, "side": "CT"})
    mm = make_mm(missed_trades=[excluded, other, hit], rounds=10)
    detected = ProblemReport(hits=[ProblemHit(problem="missed_trade", target="R1", weapon=None,
                                             evidence=hit.model_dump(), thresholds={})],
                             disabled=[], skipped={}, baseline_source=None)
    data = sample()
    data["amateur_tmp"]["problem_rates"] = {"missed_trade": {"hits": 1, "rounds": 100}}
    rows = [FrequencyRow(problem="missed_trade", label="漏补枪", hits=3, per_10_rounds=3,
                         pro_per_10_rounds=None, ratio=None, prominent=False)]
    row, = build_match_page(mm, detected, rows, load_config(), data).tiers[0].problems
    assert [example.tick for example in row.examples] == [100]
    assert "CT" not in row.examples[0].situation
    assert row.evidence_ticks == {"本场": [100]}


@pytest.mark.parametrize("hits,total,rounds,count,expected,severity", [
    (6, 10, 10, 9, 6, "warn"),
    (17, 4930, 1000, 9, 1000 * 17 / 4930, "bad"),
    (0, 10, 10, 1, .5, "warn"),
])
def test_expected_precision_and_poisson_severity(hits, total, rounds, count, expected, severity):
    data = sample()
    data["amateur_tmp"]["problem_rates"] = {"slow_reaction": {
        "hits": hits, "rounds": total, "per_10_rounds": round(10 * hits / total, 2)}}
    row, = build_match_page(make_mm(rounds=rounds), report(), frequency(10 * count / rounds, count),
                            load_config(), data).tiers[0].problems
    assert row.expected == pytest.approx(expected) and row.severity == severity


def test_only_one_death_is_not_a_strength():
    data = tier(traded_death_pct=[0, 20, 40])
    mm = make_mm(lobby=[line(deaths=1, traded_deaths=1)])
    view, = build_match_page(mm, report(), [], load_config(), {data["id"]: data}).tiers
    assert not view.strengths and view.you[4] is None


def test_verdict_weakest_requires_four_axes_even_when_bad():
    from tests.test_summary import stats
    aim = stats(100).model_copy(update={"crosshair_offset_m": 10, "crosshair_offset_samples": 5,
                                      "counter_strafing_pct": 50, "counter_strafing_shots": 5,
                                      "spray_accuracy_pct": 50, "spray_shots": 5})
    data = tier(time_to_damage_ms=[200, 300, 400], crosshair_offset_m=[0, 5, 10],
                counter_strafing_pct=[0, 50, 100], spray_accuracy_pct=[0, 50, 100])
    data["problem_rates"] = sample()["amateur_tmp"]["problem_rates"]
    view, = build_match_page(make_mm(lobby=[line(aim=aim)], rounds=10), report(), frequency(6, 6),
                             load_config(), {data["id"]: data}).tiers
    assert "最该练" in view.verdict and "雷达上最弱的是预瞄（第 17 百分位）" in view.verdict
    assert "最强的是" not in view.verdict


def test_strength_examples_only_use_matching_events():
    from cs2coach.report.page_data import _strength_moments
    from tests.test_summary import stats
    aim = stats(100).model_copy(update={"spray_accuracy_pct": 50, "spray_shots": 10})
    def spray(hits, **changes):
        return make_moment("kill", engagement={"shots": [make_shot(tick=80 + i * 8, hit=hit)
                                                         for i, hit in enumerate(hits)]}, **changes)
    good = spray([True, True, False])
    poor = spray([True, False, False], tick=200)
    death = make_moment("death", tags=["traded"])
    trade = make_moment("kill", tags=["trade_kill"])
    flash = make_moment("assist", tags=["flash_assist"])
    mm = make_mm([poor, good, death, trade, flash], lobby=[line(aim=aim)])
    cfg = load_config()
    assert _strength_moments(mm, "spray_accuracy_pct", 5, cfg) == [good]
    assert _strength_moments(mm, "traded_death_pct", 5, cfg) == [death]
    assert _strength_moments(mm, "trade_kill_events_per_round", 5, cfg) == [trade]
    assert _strength_moments(mm, "enemies_flashed_per_flash", 5, cfg) == [flash]
    assert _strength_moments(mm, "utility_damage_per_round", 5, cfg) == []


def test_verdict_does_not_call_an_above_threshold_axis_weakest():
    """审查 045：汇总里最低的维度是第 51 百分位，结论却写"最弱的是预瞄"。"""
    from cs2coach.report.page_data import _verdict
    cfg = load_config()
    axes = cfg["radar"]["axes"]
    text = _verdict("summary", [], "业余（临时）", [], axes, [51, 60, 70, 80, 90, 66], cfg)
    assert "最弱" not in text and "都在第 40 百分位以上" in text
    text = _verdict("summary", [], "业余（临时）", [], axes, [17.4, 60, 70, 80, 90, 66], cfg)
    assert "雷达上最弱的是命中速度（第 17 百分位）" in text


def test_metric_off_radar_is_never_a_strength():
    # 道具使用移出雷达后，道具伤害再高也不列为优点
    data = tier(utility_damage_per_round=list(range(40)))
    view, = build_match_page(make_mm(lobby=[line(utility_damage=1000)], rounds=10), report(), [],
                             load_config(), {data["id"]: data}).tiers
    assert all(row.id != "utility_damage_per_round" for row in view.strengths)
    cfg = load_config()
    cfg["radar"]["axes"].append({"id": "utility", "name": "道具使用", "metrics": ["utility_damage_per_round"]})
    view, = build_match_page(make_mm(lobby=[line(utility_damage=1000)], rounds=10), report(), [],
                             cfg, {data["id"]: data}).tiers
    assert any(row.id == "utility_damage_per_round" for row in view.strengths)
