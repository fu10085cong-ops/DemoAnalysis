"""生成职业基准 v2，默认使用 baselines/pro_demos.toml 的清单。

用法：python scripts/build_baseline.py [--manifest 清单] [--config 配置] [--out 输出]
也可传入 demo 路径及 --source、--demo-member，保留旧用法。
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from cs2coach.baseline import BASELINE_FILE, DEMO_MANIFEST, METRICS, build_baseline, problem_rates, spray_stats, weapon_stats
from cs2coach.config import load_config
from cs2coach.console import ensure_utf8_stdio
from cs2coach.features.moments import find_moments
from cs2coach.features.visibility import Sight
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import parse_match
from cs2coach.parallel import default_jobs, map_ordered

ROOT = Path(__file__).resolve().parents[1]


def parse_one_demo(entry: dict, cfg: dict) -> tuple[list, dict, list[tuple]]:
    """解析一场 demo 并计算全部合格选手的瞬间。返回 (瞬间列表, demo 元数据, 逐人事件)。

    逐人事件按发生顺序为 ("skip", 名字, 覆盖率) 或 ("moments", 名字, sid, 瞬间数)，
    供主进程按原来的内容和顺序打印；本子进程不打印。
    """
    match = parse_match(resolve_demo(ROOT / entry["archive"], member=entry["member"]), cfg)
    sight = Sight(match, cfg)
    if not sight.available:
        raise ValueError(f"视野计算不可用：{sight.unavailable_reason}")
    game, events = [], []
    for sid, player in match.players.items():
        if player.coverage < cfg["parsing"]["min_target_coverage"]:
            events.append(("skip", player.name, player.coverage))
            continue
        mm = find_moments(match, sid, cfg, sight=sight)
        game.append(mm)
        events.append(("moments", player.name, sid, len(mm.moments)))
    if not game:
        raise ValueError("没有合格选手")
    demo = {"event": entry["event"], "teams": entry["teams"], "map": match.meta.map_name,
            "member": entry["member"] or match.demo_path.name}
    return game, demo, events


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("demos", type=Path, nargs="*")
    ap.add_argument("--demo-member", action="append", default=[])
    ap.add_argument("--source", default=None, help="样本来源说明，未指定时按成功清单生成")
    ap.add_argument("--manifest", type=Path, default=DEMO_MANIFEST)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=BASELINE_FILE)
    ap.add_argument("--jobs", type=int, default=None,
                    help="并行进程数（默认取配置 tools.jobs 与 CPU 核数的较小值）")
    args = ap.parse_args(argv)
    if len(args.demo_member) > len(args.demos):
        ap.error("--demo-member 的数量不能多于 demo 路径")
    if args.demos:
        members = args.demo_member + [None] * (len(args.demos) - len(args.demo_member))
        entries = [{"archive": str(p.resolve()), "member": member, "event": args.source or "自定义",
                    "teams": "", "map": ""} for p, member in zip(args.demos, members)]
    else:
        entries = tomllib.loads(args.manifest.read_text(encoding="utf-8"))["demos"]

    cfg = load_config(args.config)
    jobs = args.jobs if args.jobs is not None else default_jobs(cfg)

    def report_progress(index: int, outcome) -> None:
        entry = entries[index]
        print(f"[{index + 1}/{len(entries)}] 解析 {entry['member'] or entry['archive']}", flush=True)

    outcomes = map_ordered(parse_one_demo, [(entry,) for entry in entries], jobs, args=(cfg,),
                           on_done=report_progress)
    mms, demos, failed = [], [], []
    for entry, outcome in zip(entries, outcomes):
        if outcome.error is not None:
            failed.append({"member": entry["member"] or entry["archive"], "reason": outcome.error})
            print(f"  失败，跳过：{outcome.error}", flush=True)
            continue
        game, demo, events = outcome.value
        for event in events:
            if event[0] == "skip":
                print(f"  跳过 {event[1]}：覆盖率 {event[2]:.1%}", flush=True)
            else:
                print(f"  {event[1]}（{event[2]}）：{event[3]} 个瞬间", flush=True)
        mms.extend(game)
        demos.append(demo)
        print(f"  成功：{len(game)} 人次", flush=True)

    print(f"汇总：成功 {len(demos)} 场，失败 {len(failed)} 场，合格选手 {len(mms)} 人次", flush=True)
    if not mms:
        print("没有成功样本，保留已有基准", file=sys.stderr)
        return 1
    events = "、".join(dict.fromkeys(d["event"] for d in demos))
    groups = len({d["teams"] for d in demos})
    source = args.source or f"{events}：{groups} 组对局、{len(demos)} 份 demo（分段分别处理）"
    values = {metric: [getattr(mm.aim_stats, metric) for mm in mms if mm.aim_stats is not None]
              for metric in METRICS}
    players = {}
    for mm in mms:
        players.setdefault(mm.player, mm.player_name)
    baseline = {"version": 2, **build_baseline(values, f"{source}；{len(mms)} 人次"),
                "demos": demos, "weapons": weapon_stats(mms, cfg), "spray": spray_stats(mms, cfg),
                "players": [{"steamid": sid, "name": name} for sid, name in
                            sorted(players.items())]}
    print("使用本次新算出的阈值统计职业问题频率（包含停用规则，排除 match 目标）", flush=True)
    baseline["problem_rates"] = problem_rates(mms, baseline, cfg)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temp = args.out.with_suffix(args.out.suffix + ".part")
    temp.write_text(json.dumps(baseline, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(args.out)
    print(f"已写入 {args.out}")
    for metric, b in baseline["metrics"].items():
        print(f"  {metric}: 中位数 {b['median']}（{b['p25']}–{b['p75']}，n={b['n']}）")
    for w, b in baseline["weapons"].items():
        print(f"  {w}: 交火 {b['engagements']}，TTD p50={b['time_to_damage_ms']['p50']} "
              f"p75={b['time_to_damage_ms']['p75']}，准星 p75={b['crosshair_placement_deg']['p75']}")
    for w, segments in baseline["spray"].items():
        print(f"  {w} 压枪：{json.dumps(segments, ensure_ascii=False)}")
    print("problem_rates: " + json.dumps(baseline["problem_rates"], ensure_ascii=False, indent=2))
    for item in failed:
        print(f"失败项：{item['member']}：{item['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
