"""054 汇总缓存只看分析相关配置、多场汇总支持 --jobs（Claude 预写，实现方不得修改）。"""

import copy

import pytest

from cs2coach.baseline import load_baseline
from cs2coach.cache import DISPLAY_SECTIONS, cache_key
from cs2coach.config import load_config
from cs2coach.features.problems import detect_problems
from cs2coach.parallel import Outcome
from cs2coach.summary import aggregate
from cs2coach.summary.discover import DemoRef
from tests.test_visibility import duel

# 会影响单场分析结果（MatchMoments、问题检测、比分）的配置段
ANALYSIS_SECTIONS = ("parsing", "moments", "economy", "weapons", "aim", "aim_stats", "visibility",
                     "counter_strafe", "baseline", "problems", "panel", "research")


@pytest.fixture
def demo(tmp_path):
    path = tmp_path / "x.dem"
    path.write_bytes(b"demo")
    return path


def test_every_top_level_section_is_classified():
    assert set(DISPLAY_SECTIONS) == {"training", "summary", "report", "audit", "ladder", "radar",
                                     "review", "checker", "ai", "tools"}
    assert set(load_config()) == set(DISPLAY_SECTIONS) | set(ANALYSIS_SECTIONS)


@pytest.mark.parametrize("section", sorted(DISPLAY_SECTIONS))
def test_display_section_change_keeps_cache_key(demo, section):
    cfg = load_config()
    other = copy.deepcopy(cfg)
    other[section] = {"changed": True}
    assert cache_key(demo, None, 1, cfg) == cache_key(demo, None, 1, other)


@pytest.mark.parametrize("section", ANALYSIS_SECTIONS)
def test_analysis_section_change_invalidates_cache_key(demo, section):
    cfg = load_config()
    other = copy.deepcopy(cfg)
    other[section] = {**other[section], "changed": True}
    assert cache_key(demo, None, 1, cfg) != cache_key(demo, None, 1, other)


def test_unclassified_new_section_invalidates_cache_key(demo):
    # 以后新增的配置段默认算作影响分析（宁可多算一次，不能读到过期结果）
    cfg = load_config()
    other = copy.deepcopy(cfg)
    other["brand_new_section"] = {"x": 1}
    assert cache_key(demo, None, 1, cfg) != cache_key(demo, None, 1, other)


def test_analysis_does_not_read_display_sections():
    # 去掉全部展示类配置段后，单场分析和问题检测照常运行：说明它们确实不影响缓存的内容
    cfg = load_config()
    stripped = {key: value for key, value in cfg.items() if key not in DISPLAY_SECTIONS}
    mm = duel(cfg=stripped)
    assert mm.moments
    detect_problems(mm, load_baseline(), stripped)


def _refs():
    return [DemoRef(path=f"m{i}.dem", member=None, match_id=f"m{i}", map_name="de_test", player_name="目标玩家")
            for i in range(3)]


def test_build_summary_forwards_jobs_and_keeps_input_order(monkeypatch):
    seen = {}

    def fake_map(fn, items, jobs, args=(), on_done=None):
        items = list(items)
        seen["jobs"], seen["n"] = jobs, len(items)
        outcomes = [Outcome(None, f"ValueError: 坏数据 {i}") for i in range(len(items))]
        for index in reversed(range(len(items))):  # 完成顺序与输入相反，也不能打乱结果
            if on_done is not None:
                on_done(index, outcomes[index])
        return outcomes

    monkeypatch.setattr(aggregate, "map_ordered", fake_map)
    summary = aggregate.build_summary(1, _refs(), load_config(), None, jobs=4)
    assert seen == {"jobs": 4, "n": 3}
    assert [s.match_id for s in summary.skipped] == ["m0", "m1", "m2"]
    assert [s.reason for s in summary.skipped] == ["ValueError: 坏数据 0", "ValueError: 坏数据 1", "ValueError: 坏数据 2"]


@pytest.mark.parametrize("configured", [1, 2])
def test_build_summary_default_jobs_from_config(monkeypatch, configured):
    import os
    seen = {}

    def fake_map(fn, items, jobs, args=(), on_done=None):
        seen["jobs"] = jobs
        return [Outcome(None, "ValueError: 坏数据") for _ in items]

    monkeypatch.setattr(aggregate, "map_ordered", fake_map)
    cfg = load_config()
    cfg["tools"]["jobs"] = configured
    aggregate.build_summary(1, _refs(), cfg, None)
    assert seen["jobs"] == min(configured, os.cpu_count() or 1)


def test_cli_summary_accepts_jobs():
    from cs2coach.cli import _build_parser
    args = _build_parser().parse_args(["summary", "--player", "1", "--jobs", "1"])
    assert args.jobs == 1
