"""收集全部业余 demo 的问题命中和可疑组合。用法：python scripts/audit_collect.py [--date YYYY-MM-DD]"""

from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import date
from pathlib import Path
from time import perf_counter

from cs2coach.audit import make_record, suspicious_summary, write_hits
from cs2coach.baseline import load_baseline
from cs2coach.config import load_config
from cs2coach.features.aim import shot_targets
from cs2coach.features.moments import find_moments
from cs2coach.features.problems import detect_problems
from cs2coach.features.visibility import Sight
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import parse_match
from cs2coach.summary.discover import _members, _supported

ROOT = Path(__file__).resolve().parents[1]


def demo_sources(root: Path) -> list[tuple[Path, str | None]]:
    """枚举两类业余比赛的原始文件及每个压缩包成员。"""
    sources = []
    for folder in (root / "data/demos/5e_demo", root / "data/demos/perfectworld_demo"):
        if not folder.is_dir():
            raise FileNotFoundError(f"demo 目录不存在：{folder}")
        for path in sorted(folder.iterdir()):
            if path.is_file() and _supported(path):
                members = _members(path)
                if not members:
                    raise ValueError(f"压缩包内没有 demo：{path}")
                sources.extend((path, member) for member in members)
    return sources


def collect_one(path: Path, member: str | None, cfg: dict, calibration: dict, baseline: dict) -> tuple[list[dict], int]:
    """一次解析、一次视野对象、一次全场枪弹目标计算。"""
    match = parse_match(resolve_demo(path, member=member), cfg)
    sight = Sight(match, cfg)
    if not sight.available:
        raise ValueError(f"视野计算不可用：{sight.unavailable_reason}")
    shots = match.shots.join(shot_targets(match, cfg)[["target", "target_angle"]])
    names = {sid: player.name for sid, player in match.players.items()}
    disabled = set(cfg["problems"]["disabled"])
    demo = path.relative_to(ROOT).as_posix()
    rows, players = [], 0
    for sid, player in sorted(match.players.items()):
        if sid <= 0 or player.coverage < cfg["parsing"]["min_target_coverage"]:
            continue
        mm = find_moments(match, sid, cfg, sight=sight, shots_with_targets=shots)
        report = detect_problems(mm, baseline, calibration)
        rows.extend(make_record(demo, member, mm, hit, cfg, names, disabled) for hit in report.hits)
        players += 1
    return rows, players


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--name", help="审计输出目录名，覆盖 --date")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    name = args.name or args.date
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        parser.error("--name 必须是安全的单层目录名")
    cfg = load_config(args.config)
    baseline = load_baseline()
    if baseline is None:
        raise FileNotFoundError("缺少职业基准，无法判定规则命中")
    calibration = copy.deepcopy(cfg)
    calibration["problems"]["disabled"] = []
    calibration["problems"]["spray_match_enabled"] = True
    sources = demo_sources(ROOT)
    out = ROOT / "data/audit" / name
    out.mkdir(parents=True, exist_ok=True)
    rows, failures, players = [], [], 0
    started = perf_counter()
    for index, (path, member) in enumerate(sources, 1):
        label = f"{path.relative_to(ROOT)} :: {member or '原始 demo'}"
        print(f"[{index}/{len(sources)}] {label}", flush=True)
        try:
            batch, count = collect_one(path, member, cfg, calibration, baseline)
            rows.extend(batch)
            players += count
            print(f"  {count} 人，{len(batch)} 条命中", flush=True)
        except Exception as exc:
            failures.append({"demo": path.relative_to(ROOT).as_posix(), "member": member,
                             "error": f"{type(exc).__name__}: {exc}"})
            print(f"  失败：{failures[-1]['error']}", flush=True)
    elapsed = perf_counter() - started
    write_hits(out / "hits.jsonl", rows)
    (out / "suspicious.md").write_text(suspicious_summary(rows, cfg), encoding="utf-8")
    (out / "collect_stats.json").write_text(json.dumps({"sources": len(sources), "succeeded": len(sources) - len(failures),
        "players": players, "hits": len(rows), "seconds": round(elapsed, 1), "failures": failures},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"完成：{len(sources) - len(failures)}/{len(sources)} 场，{players} 人次，{len(rows)} 条命中，{elapsed:.1f} 秒", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
