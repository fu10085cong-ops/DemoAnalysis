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

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("demos", type=Path, nargs="*")
    ap.add_argument("--demo-member", action="append", default=[])
    ap.add_argument("--source", default=None, help="样本来源说明，未指定时按成功清单生成")
    ap.add_argument("--manifest", type=Path, default=DEMO_MANIFEST)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=BASELINE_FILE)
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
    mms, demos, failed = [], [], []
    for i, entry in enumerate(entries, 1):
        label = entry["member"] or entry["archive"]
        print(f"[{i}/{len(entries)}] 解析 {label}", flush=True)
        try:
            match = parse_match(resolve_demo(ROOT / entry["archive"], member=entry["member"]), cfg)
            sight = Sight(match, cfg)
            if not sight.available:
                raise ValueError(f"视野计算不可用：{sight.unavailable_reason}")
            game = []
            for sid, player in match.players.items():
                if player.coverage < cfg["parsing"]["min_target_coverage"]:
                    print(f"  跳过 {player.name}：覆盖率 {player.coverage:.1%}", flush=True)
                    continue
                mm = find_moments(match, sid, cfg, sight=sight)
                game.append(mm)
                print(f"  {player.name}（{sid}）：{len(mm.moments)} 个瞬间", flush=True)
            if not game:
                raise ValueError("没有合格选手")
        except Exception as exc:
            failed.append({"member": label, "reason": f"{type(exc).__name__}: {exc}"})
            print(f"  失败，跳过：{failed[-1]['reason']}", flush=True)
            continue
        mms.extend(game)
        demos.append({"event": entry["event"], "teams": entry["teams"], "map": match.meta.map_name,
                      "member": entry["member"] or match.demo_path.name})
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
    baseline = {"version": 2, **build_baseline(values, f"{source}；{len(mms)} 人次"),
                "demos": demos, "weapons": weapon_stats(mms, cfg), "spray": spray_stats(mms, cfg)}
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
