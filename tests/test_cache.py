"""分析结果缓存：命中、失效条件、损坏文件重算、文件不存在时不缓存。"""

import os

from cs2coach.cache import cache_key, cached


def test_hit_and_invalidation(tmp_path):
    demo = tmp_path / "a.dem"
    demo.write_bytes(b"x")
    cfg = {"a": 1}
    key = cache_key(demo, None, 7, cfg)
    calls = []
    build = lambda: calls.append(1) or {"v": len(calls)}  # noqa: E731
    assert cached(key, build, tmp_path / "c") == {"v": 1}
    assert cached(key, build, tmp_path / "c") == {"v": 1} and len(calls) == 1  # 第二次读缓存
    assert cache_key(demo, None, 7, {"a": 2}) != key            # 配置变了
    assert cache_key(demo, None, 8, cfg) != key                 # 换了玩家
    assert cache_key(demo, "m.dem", 7, cfg) != key              # 换了压缩包成员
    os.utime(demo, ns=(1, 1))
    assert cache_key(demo, None, 7, cfg) != key                 # demo 文件变了


def test_corrupt_file_is_rebuilt_and_missing_demo_not_cached(tmp_path):
    demo = tmp_path / "a.dem"
    demo.write_bytes(b"x")
    key = cache_key(demo, None, 7, {})
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / f"{key}.pkl.gz").write_bytes(b"broken")
    assert cached(key, lambda: 42, tmp_path / "c") == 42
    assert cache_key(tmp_path / "missing.dem", None, 7, {}) is None
    calls = []
    assert cached(None, lambda: calls.append(1) or 5, tmp_path / "c") == 5
    assert cached(None, lambda: calls.append(1) or 5, tmp_path / "c") == 5 and len(calls) == 2


def test_result_rejected_by_store_is_not_written(tmp_path):
    demo = tmp_path / "a.dem"
    demo.write_bytes(b"x")
    key = cache_key(demo, None, 7, {})
    calls = []
    build = lambda: calls.append(1) or {"ok": len(calls) > 1}  # noqa: E731
    keep = lambda value: value["ok"]  # noqa: E731
    assert cached(key, build, tmp_path / "c", store=keep) == {"ok": False}  # 不写入
    assert cached(key, build, tmp_path / "c", store=keep) == {"ok": True}   # 重新计算后写入
    assert cached(key, build, tmp_path / "c", store=keep) == {"ok": True} and len(calls) == 2
