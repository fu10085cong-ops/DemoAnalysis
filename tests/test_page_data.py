"""报告页分段比较、问题行、跳转模板与匿名基准。"""

import json

import pytest

from cs2coach.config import load_config
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


def frequency(you):
    return [FrequencyRow(problem="slow_reaction", label="出枪慢", hits=3, per_10_rounds=you,
        pro_per_10_rounds=1, ratio=you, prominent=True)]


def sample():
    data = tier(trade_kills_per_round=[0, .1, .2], traded_death_pct=[0, 20, 40])
    data["problem_rates"] = {"slow_reaction": {"per_10_rounds": 2}}
    return {data["id"]: data}


@pytest.mark.parametrize("you,severity", [(1, "ok"), (2, "ok"), (2.5, "warn"), (3, "bad")])
def test_severity_and_problem_template(you, severity):
    mm = make_mm([make_moment()], lobby=[line()])
    page = build_match_page(mm, report(), frequency(you), load_config(), sample())
    problem, = page.problems
    assert problem.severity == severity and problem.target == 2 and problem.count == 3
    assert "。" == problem.text[-1] and problem.text.count("。") == 1
    assert len(problem.examples) == 1
    assert problem.examples[0].match_label == "本场"
    assert problem.examples[0].situation == "T 方，2v2，未下包"
    if severity == "bad":
        assert page.verdict == "最该练的是出枪慢：每 10 回合 3 次，业余（临时）是 2 次。"
    else:
        assert page.verdict == "这场没有明显落后于业余（临时）的问题，最强的是补枪配合。"


def test_missing_tier_and_empty_samples(tmp_path, monkeypatch):
    monkeypatch.setattr("cs2coach.report.page_data.TIER_DIR", tmp_path)
    assert load_tiers(load_config()) == {}
    page = build_match_page(make_mm(), report(), frequency(3), load_config(), {})
    assert page.problems[0].target is None and page.problems[0].severity == "warn"
    assert not page.strengths and not page.tiers
    assert "尚未构建" in page.verdict
    data = sample()
    (tmp_path / "amateur_tmp.json").write_text(json.dumps(data["amateur_tmp"]), encoding="utf-8")
    assert load_tiers(load_config()) == data


@pytest.mark.parametrize("lines,expected", [
    ([line(kills=20, deaths=10), line(steamid=B, name="队友甲", kills=21, deaths=15)], "目标玩家，20/10"),
    ([line(kills=20, deaths=10), line(steamid=B, name="队友甲", kills=21, deaths=11)], "队友甲，21/11"),
    ([line(steamid=B, name="队友甲"), line()], "目标玩家，10/5"),
])
def test_best_player_and_average_include_target(lines, expected):
    page = build_match_page(make_mm(lobby=lines), report(), [], load_config(), sample())
    avg, best, target = page.tiers[0].comparisons
    assert best.name == f"本场最佳（{expected}）"
    assert avg.id == "match_avg" and target.values == [50]*6
    expected_average = sum(axis_values(player_metrics(item), sample()["amateur_tmp"],
                                      load_config()["radar"]["axes"])[4] for item in lines) / len(lines)
    assert avg.values[4] == expected_average


def test_commands_follow_review_pack_templates():
    cfg = load_config()
    cfg["review"].update(demo_subdir="review/custom", playdemo_template="playdemo {subdir}/{demo_stem}",
                         jump_template="demo_gototick {tick}; demo_pause")
    mm = make_mm([make_moment()], demo="example.dem", lobby=[line()])
    command = build_match_page(mm, report(), frequency(3), cfg, sample()).problems[0].examples[0].command
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
    for private in (str(A), "目标玩家", "alice", "synthetic.dem", "private.dem", "steamid", "players", "dirs"):
        assert private not in serialized
    assert data["source"] == "1 份 demo、1 人次"
    assert data["metrics"]["trade_kills_per_round"] == {"median": .2, "n": 1, "values": [.2]}


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


def test_summary_median_of_percentile_axes_and_highlights():
    cfg = load_config()
    games = []
    for number, trades in enumerate([0, 1, 2]):
        mm = make_mm([make_moment("kill", tags=["opening_kill"])],
                     lobby=[line(trade_kills=trades)], rounds=10)
        mm.highlights = find_highlights(mm, [make_round()])
        ref = DemoRef(path="test.dem", member=None, match_id=f"a{number}", map_name="de_dust2", player_name="目标玩家")
        games.append((ref, mm, report(), (6, 4)))
    summary = aggregate_matches(A, games, None, cfg)
    page = build_summary_page(summary, cfg, sample())
    # 补枪百分位中位数为 50，被补比例为 83，维度逐场平均后中位数为 66.5。
    assert page.tiers[0].you[4] == 66.5
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
    row, = page.strengths
    assert row.id == "time_to_damage_ms" and row.target == 300
    assert [e.tick for e in row.examples] == [90, 290]
    assert len(row.examples) == 2


@pytest.mark.parametrize("metric,eligible", [
    ("counter_strafing_pct", True), ("spray_accuracy_pct", True),
    ("trade_kills_per_round", True), ("traded_death_pct", True),
    ("utility_damage_per_round", False), ("enemies_flashed_per_flash", False), ("wasted_utility_avg", False),
])
def test_other_strength_example_sources(metric, eligible):
    from cs2coach.report.page_data import _strength_moments
    kill = make_moment("kill", tags=["trade_kill"], engagement={"shots": [make_shot(tick=t) for t in (80, 88, 96)]})
    mm = make_mm([kill])
    assert bool(_strength_moments(mm, metric, 5, load_config())) is eligible
    if not eligible:
        mm.moments.append(make_moment("assist", tags=["flash_assist"]))
        assert _strength_moments(mm, metric, 5, load_config()) == [mm.moments[-1]]


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
    assert builder.main(["--all"]) == 0
    data = json.loads((tmp_path / "output/pro.json").read_text(encoding="utf-8"))
    assert seen == [(3, pro)] and data["source"] == "1 份 demo、3 人次"
    assert data["metrics"]["trade_kills_per_round"]["values"] == [.2, .2, .2]
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
    assert page.verdict.startswith("这场没有明显落后于业余（临时）的问题，最强的是")
    assert "，最弱的是" in page.verdict
