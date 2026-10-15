"""生成职业基准：对给定的职业比赛 demo，计算每名选手每场的枪法统计，写入 src/cs2coach/baselines/pro_sample.json。

用法:
    python scripts/build_baseline.py --source "ESL Pro League S24 Vitality vs MOUZ（BO3）" <demo> [<demo> ...] [--demo-member 名字 ...]

压缩包内有多个 demo 时，可对同一个压缩包重复传入并配合多个 --demo-member（按顺序一一对应）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cs2coach.baseline import BASELINE_FILE, METRICS, build_baseline
from cs2coach.config import load_config
from cs2coach.console import ensure_utf8_stdio
from cs2coach.features.moments import find_moments
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import parse_match


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("demos", type=Path, nargs="+")
    ap.add_argument("--demo-member", action="append", default=[])
    ap.add_argument("--source", required=True, help="样本来源说明，会写进报告")
    ap.add_argument("--out", type=Path, default=BASELINE_FILE)
    args = ap.parse_args(argv)
    members = args.demo_member + [None] * (len(args.demos) - len(args.demo_member))

    cfg = load_config()
    values: dict[str, list[float]] = {m: [] for m in METRICS}
    players = 0
    for demo, member in zip(args.demos, members):
        match = parse_match(resolve_demo(demo, member=member), cfg)
        for sid in match.players:
            stats = find_moments(match, sid, cfg).aim_stats
            if stats is None:
                print(f"错误: {demo} 视野计算不可用，无法生成基准", file=sys.stderr)
                return 1
            players += 1
            for m in METRICS:
                values[m].append(getattr(stats, m))
        print(f"{member or demo.name}: {len(match.players)} 名选手", flush=True)

    baseline = build_baseline(values, f"{args.source}；{players} 人次")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入 {args.out}")
    for m, b in baseline["metrics"].items():
        print(f"  {m}: 中位数 {b['median']}（{b['p25']}–{b['p75']}，n={b['n']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
