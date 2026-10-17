"""把职业与业余 demo 的交火、漏补枪和玩家统计导出为可复用 parquet。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter

import pandas as pd

from cs2coach.audit import read_hits
from cs2coach.baseline import DEMO_MANIFEST, load_baseline
from cs2coach.cache import code_hash
from cs2coach.config import load_config
from cs2coach.dataset import DATASET_ROOT
from cs2coach.features.aim import shot_targets
from cs2coach.features.moments import find_moments
from cs2coach.features.problems import detect_problems
from cs2coach.features.visibility import Sight
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import parse_match

ROOT = Path(__file__).resolve().parents[1]


def sources(audit_dir: Path, manifest: Path = DEMO_MANIFEST) -> list[tuple[str, str, str | None]]:
    """职业清单加审计命中出现过的业余比赛；同一压缩包的成员分别处理。"""
    pro = tomllib.loads(manifest.read_text(encoding="utf-8"))["demos"]
    entries = [("pro", row["archive"], row.get("member")) for row in pro]
    hits = read_hits(audit_dir / "hits.jsonl")
    entries.extend(("amateur", demo, member) for demo, member in sorted(
        {(row["demo"], row.get("member")) for row in hits}, key=lambda pair: (pair[0], pair[1] or "")))
    return entries


def _value(value):
    """列表和嵌套结构以 JSON 字符串存储，避免 parquet 类型推断冲突。"""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def _flat(model, prefix: str) -> dict:
    if model is None:
        return {}
    return {prefix + key: _value(value) for key, value in model.model_dump().items()}


def rows_for_match(match, cfg: dict, baseline: dict, group: str, demo: str, member: str | None) -> list[dict]:
    """每位覆盖率合格的玩家分别导出，瞬间命中与当前 detect_problems 同源。"""
    sight = Sight(match, cfg)
    if not sight.available:
        raise ValueError(f"视野计算不可用：{sight.unavailable_reason}")
    shots = match.shots.join(shot_targets(match, cfg)[["target", "target_angle"]])
    calibration = copy.deepcopy(cfg)
    calibration["problems"]["disabled"] = []
    calibration["problems"]["spray_match_enabled"] = True
    disabled = set(cfg["problems"]["disabled"])
    rows = []
    for sid, player in sorted(match.players.items()):
        if sid <= 0 or player.coverage < cfg["parsing"]["min_target_coverage"]:
            continue
        mm = find_moments(match, sid, cfg, sight=sight, shots_with_targets=shots)
        report = detect_problems(mm, baseline, calibration)
        by_target: dict[str, list[str]] = {}
        for hit in report.hits:
            by_target.setdefault(hit.target, []).append(hit.problem)
        common = {"group": group, "demo": demo, "member": member, "map": mm.map_name, "steamid": sid}
        for moment in mm.moments:
            values = moment.model_dump(exclude={"context", "engagement", "death", "trade", "assist", "opponent_name"})
            values.pop("opponent_name", None)
            values["moment_kind"] = values.pop("kind")
            rows.append({**common, "kind": "moments", **{key: _value(value) for key, value in values.items()},
                         **_flat(moment.context, "ctx_"), **_flat(moment.engagement, "eng_"),
                         **_flat(moment.death, "death_"), **_flat(moment.trade, "trade_"),
                         **_flat(moment.assist, "assist_"),
                         "hits": json.dumps([p for p in by_target.get(moment.id, []) if p not in disabled], ensure_ascii=False),
                         "disabled_hits": json.dumps([p for p in by_target.get(moment.id, []) if p in disabled], ensure_ascii=False)})
        for trade in mm.missed_trades:
            rows.append({**common, "kind": "missed_trades", **{key: _value(value) for key, value in trade.model_dump().items()}})
        rows.append({**common, "kind": "players", "rounds_played": mm.rounds_played,
                     "name_hash": hashlib.sha256(mm.player_name.encode("utf-8")).hexdigest(),
                     **_flat(mm.aim_stats, "aim_")})
    return rows


def export_one(entry: tuple[str, str, str | None], out: Path, cfg: dict, baseline: dict,
               root: Path = ROOT) -> tuple[str, int, bool]:
    group, demo, member = entry
    digest = hashlib.sha256(json.dumps(entry, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
    dest = out / f"{digest}.parquet"
    if dest.exists():
        return group, 0, True
    match = parse_match(resolve_demo(root / demo, member=member), cfg)
    rows = rows_for_match(match, cfg, baseline, group, demo, member)
    if not rows:
        raise ValueError(f"无合格玩家：{demo} / {member}")
    frame = pd.DataFrame(rows)
    # 全部 row 共用一份 schema；缺失字段用 None，避免不同 demo 类型不一致。
    frame = frame.astype(object).where(pd.notna(frame), None)
    out.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".parquet.part")
    try:
        frame.to_parquet(part, index=False, engine="pyarrow")
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)
    return group, len(rows), False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demos-from", type=Path, help="审计目录；默认最新的含 hits.jsonl 的目录")
    ap.add_argument("--jobs", type=int, default=3)
    args = ap.parse_args(argv)
    if args.jobs < 1:
        ap.error("--jobs 必须大于零")
    audit = args.demos_from
    if audit is None:
        candidates = list((ROOT / "data/audit").glob("*/hits.jsonl"))
        if not candidates:
            ap.error("找不到审计 hits.jsonl；请用 --demos-from 指定")
        audit = max(candidates, key=lambda path: path.stat().st_mtime_ns).parent
    cfg, baseline = load_config(), load_baseline()
    if baseline is None:
        raise FileNotFoundError("缺少职业基准")
    entries = sources(audit)
    out = DATASET_ROOT / code_hash()[:12]
    started = perf_counter()
    total, skipped, failed = 0, 0, []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(export_one, entry, out, cfg, baseline): entry for entry in entries}
        for future in as_completed(futures):
            entry = futures[future]
            try:
                group, count, cached = future.result()
                total += count
                skipped += cached
                print(f"{group}: {entry[1]} :: {entry[2] or '原始 demo'} — {'缓存' if cached else str(count) + ' 行'}", flush=True)
            except Exception as exc:
                failed.append((entry, str(exc)))
                print(f"失败：{entry[1]} :: {entry[2]}：{type(exc).__name__}: {exc}", flush=True)
    print(f"完成：{len(entries)-len(failed)}/{len(entries)} 份，新增 {total} 行，缓存 {skipped} 份，耗时 {perf_counter()-started:.1f} 秒", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
