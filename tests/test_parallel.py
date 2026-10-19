"""048 逐场并行：结果按输入顺序、单项失败不影响其他项、--jobs 1 不开进程池。预写测试，实现方不要修改。"""

import os

import pytest

from cs2coach.config import ConfigError, load_config
from cs2coach.parallel import Outcome, default_jobs, map_ordered
from tests.helpers.parallel_fns import fail_on_two, square_with_offset


def test_parallel_keeps_input_order_and_uses_other_processes():
    outcomes = map_ordered(square_with_offset, [(0,), (1,), (2,), (3,)], jobs=2, args=(10,))
    assert [o.value[0] for o in outcomes] == [10, 11, 14, 19]
    assert all(o.error is None for o in outcomes)
    assert {o.value[1] for o in outcomes} != {os.getpid()}  # 确实在子进程里算


def test_jobs_one_runs_inline_without_pool(monkeypatch):
    import cs2coach.parallel as parallel

    def boom(*args, **kwargs):
        raise AssertionError("--jobs 1 不应创建进程池")
    monkeypatch.setattr(parallel, "ProcessPoolExecutor", boom)
    outcomes = map_ordered(square_with_offset, [(3,), (4,)], jobs=1, args=(0,))
    assert [o.value for o in outcomes] == [(9, os.getpid()), (16, os.getpid())]


@pytest.mark.parametrize("jobs", [1, 2])
def test_one_failure_does_not_stop_others(jobs):
    outcomes = map_ordered(fail_on_two, [(1,), (2,), (3,)], jobs=jobs)
    assert [o.value for o in outcomes] == [1, None, 3]
    assert outcomes[1].error == "ValueError: 第二项坏了"
    assert outcomes[0].error is None and outcomes[2].error is None


def test_progress_callback_sees_every_item_once():
    seen = []
    map_ordered(square_with_offset, [(0,), (1,), (2,)], jobs=2, args=(0,),
                on_done=lambda index, outcome: seen.append(index))
    assert sorted(seen) == [0, 1, 2]


def test_empty_input_and_outcome_shape():
    assert map_ordered(square_with_offset, [], jobs=4, args=(0,)) == []
    assert Outcome(value=1, error=None).value == 1


def test_default_jobs_from_config_and_validation(tmp_path):
    cfg = load_config()
    assert cfg["tools"]["jobs"] == 2  # 2026-10-19 起默认 2，尽量少占 CPU
    assert default_jobs(cfg) == min(2, os.cpu_count() or 1)
    bad = tmp_path / "bad.toml"
    bad.write_text("[tools]\njobs = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)


@pytest.mark.parametrize("module_name,argv,source_attr", [
    ("scripts.audit_collect", ["--name", "x", "--jobs", "3"], "demo_sources"),
    ("scripts.build_baseline", ["--jobs", "3"], None),
    ("scripts.build_tier_baseline", ["--tier", "amateur_tmp", "--jobs", "3"], "demo_entries"),
    ("scripts.page_check_all", ["--jobs", "3"], "demo_entries"),
])
def test_scripts_pass_jobs_to_map_ordered(module_name, argv, source_attr, monkeypatch, tmp_path):
    """每个脚本都接受 --jobs 并交给 map_ordered；拦截 map_ordered 和 demo 来源，不真的解析，也不写共享目录。"""
    import importlib
    module = importlib.import_module(module_name)
    if hasattr(module, "ROOT"):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    if source_attr:
        monkeypatch.setattr(module, source_attr, lambda *a, **k: [(tmp_path / "a.dem", None)])
    calls = []

    class Stop(Exception):
        pass

    def fake(fn, items, jobs, args=(), on_done=None):
        calls.append(jobs)
        raise Stop
    monkeypatch.setattr(module, "map_ordered", fake)
    with pytest.raises(Stop):
        module.main(argv)
    assert calls == [3]


def test_rule_regression_forwards_jobs(monkeypatch, tmp_path):
    from scripts import rule_regression
    seen = {}
    monkeypatch.setattr(rule_regression.build_baseline, "main", lambda argv: seen.setdefault("baseline", argv) and 0 or 0)
    monkeypatch.setattr(rule_regression.audit_collect, "main", lambda argv: seen.setdefault("collect", argv) and 1 or 1)
    monkeypatch.setattr(rule_regression, "AUDIT_ROOT", tmp_path)
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "hits.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(rule_regression, "BASELINE_FILE", tmp_path / "pro.json")
    (tmp_path / "pro.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError):  # 收集返回 1 → 脚本按失败处理，这里只关心参数是否转发
        rule_regression.main(["--name", "new", "--compare-to", "old", "--jobs", "4"])
    assert seen["baseline"] == ["--jobs", "4"]
    assert seen["collect"] == ["--name", "new", "--jobs", "4"]
