"""分析结果缓存：同一份 demo、同一名玩家、同样的配置和代码，第二次直接读结果，不再解析。

只缓存小而稳定的结果（MatchMoments、ProblemReport、比分），不缓存逐 tick 数据（一场约 90 MB）。
键包含：demo 文件路径、大小、修改时间、压缩包成员、玩家、职业基准文件、完整配置，以及解析层、
特征层、基准代码的内容哈希；任何一项变化都会重新计算。缓存目录在 data/cache/（已被 git 忽略）。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "cache" / "analysis"
_SOURCE_DIRS = ("parsing", "features")
_SOURCE_FILES = ("baseline.py", "baselines/pro_sample.json")
_code_hash: str | None = None


def code_hash() -> str:
    """解析层、特征层与职业基准的内容哈希；改了这些代码，旧缓存自动失效。"""
    global _code_hash
    if _code_hash is None:
        pkg = Path(__file__).resolve().parent
        h = hashlib.sha256()
        files = sorted(f for d in _SOURCE_DIRS for f in (pkg / d).rglob("*.py"))
        files += [pkg / f for f in _SOURCE_FILES if (pkg / f).exists()]
        for f in files:
            h.update(f.relative_to(pkg).as_posix().encode())
            h.update(f.read_bytes())
        _code_hash = h.hexdigest()[:16]
    return _code_hash


def cache_key(demo: Path, member: str | None, steamid: int, cfg: dict[str, Any]) -> str | None:
    """文件不存在或读不到属性时返回 None：不缓存，直接计算（由计算过程报告真实错误）。"""
    try:
        stat = demo.stat()
    except OSError:
        return None
    parts = {"demo": str(demo.resolve()), "size": stat.st_size, "mtime": stat.st_mtime_ns, "member": member,
             "player": int(steamid), "cfg": cfg, "code": code_hash()}
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def cached(key: str | None, build: Callable[[], Any], cache_dir: Path = CACHE_DIR, enabled: bool = True,
           store: Callable[[Any], bool] | None = None) -> Any:
    """命中则读取，否则计算并写入；读取失败（文件损坏、版本不兼容）时重新计算。key 为 None 时不缓存。

    store：计算结果不满足时只返回、不写入（例如视野计算临时失败的结果，下次应重新计算）。
    """
    if key is None:
        return build()
    path = cache_dir / f"{key}.pkl.gz"
    if enabled and path.exists():
        try:
            with gzip.open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            path.unlink(missing_ok=True)
    value = build()
    if enabled and (store is None or store(value)):
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wb", compresslevel=1) as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)
    return value
